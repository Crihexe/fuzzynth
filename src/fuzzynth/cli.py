"""Command-line entry point for local Fuzzynth administration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys

from fuzzynth import __version__
from fuzzynth.artifacts import ArtifactStore
from fuzzynth.budgets import (
    BudgetConfigurationError,
    BudgetLedger,
    load_meter_policies,
)
from fuzzynth.campaign_config import (
    CampaignConfigurationError,
    choose_session_plan,
    load_campaign_configuration,
)
from fuzzynth.catalog import CatalogError
from fuzzynth.credentials import CredentialError, load_credentials
from fuzzynth.control import ControlLedger, ControlStateError
from fuzzynth.corpus import CorpusError, CorpusPool
from fuzzynth.docker_executor import DockerExecutionError
from fuzzynth.execution_service import ExecutionServiceError, execute_file
from fuzzynth.probe import PROBE_INPUT, PROBE_INSTRUCTIONS, run_probe
from fuzzynth.responses import GenerationRequest
from fuzzynth.sessions import SessionLedger, SessionStateError
from fuzzynth.notifications import (
    NotificationError,
    TelegramCampaignNotifier,
    load_telegram_credentials,
    send_telegram_message,
)
from fuzzynth.supervisor import CampaignSupervisor
from fuzzynth.telegram_control import TelegramControlService, run_control_loop


def _doctor(credentials_path: Path | None) -> int:
    credentials = load_credentials(credentials_path)
    result = {
        "status": "ok",
        "version": __version__,
        "providers": credentials.safe_status(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _workers(repo_root: Path, seed: int) -> int:
    configuration = load_campaign_configuration(
        repo_root / "config/campaign-workers.toml",
        repo_root=repo_root,
    )
    workers = []
    for worker in configuration.workers.values():
        plan = choose_session_plan(worker, seed)
        workers.append(
            {
                "id": worker.worker_id,
                "enabled": worker.enabled,
                "provider": worker.provider,
                "model": worker.model,
                "mode": worker.mode,
                "meter": worker.meter,
                "reasoning_efforts": worker.reasoning_efforts,
                "send_reasoning": worker.send_reasoning,
                "verbosity": worker.verbosity,
                "send_verbosity": worker.send_verbosity,
                "temperatures": worker.temperatures,
                "pricing_profile": worker.pricing_profile,
                "turn_range": [
                    worker.min_turns_per_session,
                    worker.max_turns_per_session,
                ],
                "example_session_for_seed": {
                    "reasoning_effort": plan.reasoning_effort,
                    "temperature": plan.temperature,
                    "target_turns": plan.target_turns,
                },
                "v8_build_profile": worker.v8_build_profile,
                "v8_worker_profile": worker.v8_worker_profile,
                "d8_flags": worker.d8_flags,
            }
        )
    print(
        json.dumps(
            {
                "dataset_enabled": configuration.context.dataset_enabled,
                "seed": seed,
                "workers": workers,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _budget_status(repo_root: Path, state_root: Path) -> int:
    policies = load_meter_policies(repo_root / "config/budgets.toml")
    with BudgetLedger(state_root / "budgets.sqlite3", policies) as ledger:
        status = [ledger.status(meter_id) for meter_id in sorted(policies)]
    print(json.dumps({"meters": status}, indent=2, sort_keys=True))
    return 0


def _session_status(state_root: Path) -> int:
    store = ArtifactStore(state_root / "artifacts")
    with SessionLedger(state_root / "sessions.sqlite3", store) as ledger:
        sessions = [
            {
                "session_id": session.session_id,
                "worker_id": session.worker_id,
                "status": session.status,
                "next_turn": session.next_turn,
                "target_turns": session.target_turns,
                "pause_reason": session.pause_reason,
                "has_corpus": session.corpus is not None,
                "reasoning_effort": session.reasoning_effort,
                "temperature": session.temperature,
            }
            for session in ledger.list_sessions()
        ]
    print(json.dumps({"sessions": sessions}, indent=2, sort_keys=True))
    return 0


def _control_status(repo_root: Path, state_root: Path) -> int:
    configuration = load_campaign_configuration(
        repo_root / "config/campaign-workers.toml",
        repo_root=repo_root,
    )
    worker_ids = tuple(configuration.workers)
    with ControlLedger(state_root / "control.sqlite3") as ledger:
        document = ledger.snapshot(worker_ids).as_dict()
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fuzzynth")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="validate local configuration without network calls"
    )
    doctor.add_argument(
        "--credentials",
        type=Path,
        help="external provider credentials file",
    )

    probe = subparsers.add_parser(
        "probe", help="make one bounded live Responses API capability request"
    )
    probe.add_argument("--live", action="store_true", help="confirm network usage")
    probe.add_argument("--provider", choices=("alternate", "official"), required=True)
    probe.add_argument("--model", required=True)
    probe.add_argument("--temperature", type=float)
    probe.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
    )
    probe.add_argument("--verbosity", choices=("low", "medium", "high"))
    probe.add_argument("--max-output-tokens", type=int, default=64)
    probe.add_argument("--timeout", type=float, default=30.0)
    probe.add_argument("--credentials", type=Path)

    execute = subparsers.add_parser(
        "execute", help="run one JS file in a pinned isolated d8 worker"
    )
    execute.add_argument("--program", type=Path, required=True)
    execute.add_argument("--profile", default="release_symbolized")
    execute.add_argument("--worker-profile", default="standard")
    execute.add_argument("--flag", action="append", default=[])
    execute.add_argument("--state-root", type=Path, default=Path("state"))

    workers = subparsers.add_parser(
        "workers", help="show configured campaign workers without provider calls"
    )
    workers.add_argument("--seed", type=int, default=1)
    workers.add_argument("--repo-root", type=Path, default=Path("."))

    budgets = subparsers.add_parser(
        "budget-status", help="show durable token/cost counters without provider calls"
    )
    budgets.add_argument("--state-root", type=Path, default=Path("state"))
    budgets.add_argument("--repo-root", type=Path, default=Path("."))

    sessions = subparsers.add_parser(
        "session-status", help="show durable campaign session state"
    )
    sessions.add_argument("--state-root", type=Path, default=Path("state"))

    controls = subparsers.add_parser(
        "control-status", help="show durable campaign dispatch controls"
    )
    controls.add_argument("--state-root", type=Path, default=Path("state"))
    controls.add_argument("--repo-root", type=Path, default=Path("."))

    telegram = subparsers.add_parser(
        "telegram-control",
        help="poll authenticated Telegram owner commands",
    )
    telegram.add_argument("--live", action="store_true", help="confirm network usage")
    telegram.add_argument("--once", action="store_true", help="perform one poll")
    telegram.add_argument("--poll-timeout", type=int, default=25)
    telegram.add_argument("--credentials", type=Path)
    telegram.add_argument("--state-root", type=Path, default=Path("state"))
    telegram.add_argument("--repo-root", type=Path, default=Path("."))

    campaign = subparsers.add_parser(
        "campaign-run",
        help="run supervised iterative workers with an explicit corpus",
    )
    campaign.add_argument("--live", action="store_true", help="confirm provider usage")
    campaign.add_argument("--corpus-file", type=Path, action="append", required=True)
    campaign.add_argument("--window-size", type=int, default=2)
    campaign.add_argument("--worker", action="append")
    campaign.add_argument("--seed", type=int, default=1)
    campaign.add_argument("--max-turns-per-worker", type=int)
    campaign.add_argument("--max-sessions-per-worker", type=int)
    campaign.add_argument("--exit-when-blocked", action="store_true")
    campaign.add_argument("--credentials", type=Path)
    campaign.add_argument("--telegram-credentials", type=Path)
    campaign.add_argument("--state-root", type=Path, default=Path("state"))
    campaign.add_argument("--repo-root", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args.credentials)
        if args.command == "probe":
            if not args.live:
                print("fuzzynth: probe requires --live", file=sys.stderr)
                return 2
            credentials = load_credentials(args.credentials)
            provider = getattr(credentials, args.provider)
            request = GenerationRequest(
                model=args.model,
                instructions=PROBE_INSTRUCTIONS,
                input_text=PROBE_INPUT,
                max_output_tokens=args.max_output_tokens,
                temperature=args.temperature,
                reasoning_effort=args.reasoning_effort,
                verbosity=args.verbosity,
            )
            result = run_probe(provider, request, timeout=args.timeout)
            print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
            return 0 if result.supported else 1
        if args.command == "execute":
            result = execute_file(
                args.program,
                build_profile=args.profile,
                worker_profile=args.worker_profile,
                flags=tuple(args.flag),
                state_root=args.state_root,
            )
            print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
            return 1 if result.bug_candidate else 0
        if args.command == "workers":
            return _workers(args.repo_root.resolve(), args.seed)
        if args.command == "budget-status":
            return _budget_status(
                args.repo_root.resolve(),
                args.state_root.resolve(),
            )
        if args.command == "session-status":
            return _session_status(args.state_root.resolve())
        if args.command == "control-status":
            return _control_status(
                args.repo_root.resolve(),
                args.state_root.resolve(),
            )
        if args.command == "telegram-control":
            if not args.live:
                print("fuzzynth: telegram-control requires --live", file=sys.stderr)
                return 2
            telegram_credentials = load_telegram_credentials(args.credentials)
            with TelegramControlService(
                repo_root=args.repo_root,
                state_root=args.state_root,
                credentials=telegram_credentials,
            ) as service:
                run_control_loop(
                    service,
                    poll_timeout=args.poll_timeout,
                    once=args.once,
                    error_handler=lambda message: print(
                        f"fuzzynth: Telegram control retrying: {message}",
                        file=sys.stderr,
                    ),
                )
            return 0
        if args.command == "campaign-run":
            if not args.live:
                print("fuzzynth: campaign-run requires --live", file=sys.stderr)
                return 2
            repo_root = args.repo_root.resolve()
            configuration = load_campaign_configuration(
                repo_root / "config/campaign-workers.toml",
                repo_root=repo_root,
            )
            worker_ids = tuple(args.worker or (
                worker.worker_id
                for worker in configuration.enabled_workers()
            ))
            corpus = CorpusPool.load(tuple(args.corpus_file))
            provider_credentials = load_credentials(args.credentials)
            telegram_credentials = load_telegram_credentials(
                args.telegram_credentials
            )
            campaign_notifier = TelegramCampaignNotifier(
                telegram_credentials,
                state_root=args.state_root,
            )
            supervisor = CampaignSupervisor(
                repo_root=repo_root,
                state_root=args.state_root,
                credentials=provider_credentials,
                corpus=corpus,
                worker_ids=worker_ids,
                window_size=args.window_size,
                base_seed=args.seed,
                campaign_notifier=campaign_notifier,
                operational_alert=lambda message: send_telegram_message(
                    telegram_credentials,
                    message,
                ),
            )
            previous_term = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, lambda *_args: supervisor.stop())
            try:
                summaries = supervisor.run(
                    max_turns_per_worker=args.max_turns_per_worker,
                    max_sessions_per_worker=args.max_sessions_per_worker,
                    exit_when_blocked=args.exit_when_blocked,
                )
            finally:
                signal.signal(signal.SIGTERM, previous_term)
            print(
                json.dumps(
                    {"workers": [summary.as_dict() for summary in summaries]},
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
    except (
        BudgetConfigurationError,
        CampaignConfigurationError,
        CredentialError,
        CatalogError,
        DockerExecutionError,
        ExecutionServiceError,
        SessionStateError,
        ControlStateError,
        CorpusError,
        NotificationError,
        OSError,
        ValueError,
    ) as exc:
        print(f"fuzzynth: configuration error: {exc}", file=sys.stderr)
        return 2
    return 2
