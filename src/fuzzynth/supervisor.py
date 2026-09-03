"""Parallel, bounded lifecycle supervision for iterative campaign workers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
from typing import Callable
import uuid

from fuzzynth.budgets import BudgetLimitError
from fuzzynth.campaign_service import CampaignService, CampaignServiceError
from fuzzynth.control import ControlStateError, is_supervisor_provider_pause
from fuzzynth.corpus import CorpusPool, extract_wasm_staging_target
from fuzzynth.credentials import CredentialStore
from fuzzynth.notifications import TelegramCampaignNotifier
from fuzzynth.outcomes import diagnose_harness_misuse
from fuzzynth.session_context import TurnContext
from fuzzynth.sessions import SessionRecord, SessionStateError


EventSink = Callable[[dict[str, object]], None]
OperationalAlert = Callable[[str], None]

_TRANSIENT_PROVIDER_RETRY_DELAYS = (5.0, 15.0, 60.0, 300.0)


@dataclass(frozen=True, slots=True)
class WorkerRunSummary:
    worker_id: str
    turns: int
    sessions_started: int
    final_state: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "turns": self.turns,
            "sessions_started": self.sessions_started,
            "final_state": self.final_state,
            "reason": self.reason,
        }


def json_event_sink(event: dict[str, object]) -> None:
    print(json.dumps(event, separators=(",", ":"), sort_keys=True), flush=True)


def _stable_seed(base_seed: int, worker_id: str, ordinal: int) -> int:
    material = f"{base_seed}:{worker_id}:{ordinal}".encode()
    # SQLite INTEGER is signed 64-bit. Keep the deterministic seed inside that
    # portable range while retaining 63 bits of entropy.
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & (
        (1 << 63) - 1
    )


def _failure_kind(feedback: bytes) -> str | None:
    try:
        document = json.loads(feedback)
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("timed_out") is True:
        return "timeout"
    if document.get("oom_killed") is True:
        return "oom"
    diagnostics = "\n".join(
        str(document.get(name, ""))
        for name in ("stdout_tail", "stderr_tail")
    )
    if "SyntaxError" in diagnostics:
        return "syntax_error"
    if "WebAssembly" in diagnostics and any(
        marker in diagnostics
        for marker in ("CompileError", "validation", "expected magic word")
    ):
        return "wasm_compile_error"
    return None


def _globally_repeated_semantic_path(feedback: bytes) -> bool:
    try:
        document = json.loads(feedback)
    except (UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(document, dict):
        return False
    observation = document.get("program_observation")
    if not isinstance(observation, dict):
        return False
    novelty = observation.get("semantic_novelty")
    return isinstance(novelty, dict) and novelty.get("repeated_globally") is True


def adaptive_session_reset_reason(
    history: tuple[TurnContext, ...],
) -> str | None:
    """Identify contexts where another iterative turn is predictably wasteful."""

    if not history:
        return None
    latest_kind = _failure_kind(history[-1].feedback)
    if latest_kind in {"timeout", "oom"}:
        return latest_kind
    if len(history) < 2:
        return None
    if history[-1].program == history[-2].program:
        return "duplicate_program"
    if all(
        _globally_repeated_semantic_path(turn.feedback)
        for turn in history[-2:]
    ):
        return "repeated_global_semantic_path"
    previous_kind = _failure_kind(history[-2].feedback)
    if latest_kind == previous_kind and latest_kind in {
        "syntax_error",
        "wasm_compile_error",
    }:
        return f"repeated_{latest_kind}"
    return None


def transient_provider_retry_delay(
    pause_reason: str | None,
    prior_retries: int,
) -> float | None:
    """Return bounded retry delay for a generic transient provider failure."""

    if pause_reason != "provider_error" or prior_retries < 0:
        return None
    return _TRANSIENT_PROVIDER_RETRY_DELAYS[
        min(prior_retries, len(_TRANSIENT_PROVIDER_RETRY_DELAYS) - 1)
    ]


class CampaignSupervisor:
    def __init__(
        self,
        *,
        repo_root: Path,
        state_root: Path,
        credentials: CredentialStore,
        corpus: CorpusPool,
        worker_ids: tuple[str, ...],
        window_size: int = 2,
        base_seed: int = 1,
        campaign_notifier: TelegramCampaignNotifier | None = None,
        operational_alert: OperationalAlert | None = None,
        event_sink: EventSink = json_event_sink,
        idle_seconds: float = 5.0,
        startup_stagger_seconds: float = 0.75,
    ):
        if not worker_ids or len(set(worker_ids)) != len(worker_ids):
            raise ValueError("worker IDs must be non-empty and unique")
        if window_size < 1:
            raise ValueError("window size must be positive")
        if idle_seconds <= 0:
            raise ValueError("idle_seconds must be positive")
        if startup_stagger_seconds < 0:
            raise ValueError("startup_stagger_seconds must be non-negative")
        self.repo_root = repo_root.resolve()
        self.state_root = state_root.resolve()
        self.credentials = credentials
        self.corpus = corpus
        self.worker_ids = worker_ids
        self.window_size = window_size
        self.base_seed = base_seed
        self.campaign_notifier = campaign_notifier
        self.operational_alert = operational_alert
        self.event_sink = event_sink
        self.idle_seconds = idle_seconds
        self.startup_stagger_seconds = startup_stagger_seconds
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    @staticmethod
    def _open_session(sessions: tuple[SessionRecord, ...]) -> SessionRecord | None:
        for session in reversed(sessions):
            if session.status in {"active", "paused"}:
                return session
        return None

    def _pause_worker(self, service: CampaignService, worker_id: str, reason: str) -> None:
        service.control.set_worker(
            worker_id,
            "paused",
            request_id=f"supervisor:{uuid.uuid4()}",
            source="supervisor",
            actor="campaign-supervisor",
            command=f"pause after {reason}",
        )

    def _alert(self, message: str) -> None:
        if self.operational_alert is not None:
            try:
                self.operational_alert(message)
            except Exception:
                self.event_sink({"event": "notification_failed"})

    def _run_worker(
        self,
        worker_id: str,
        *,
        max_turns: int | None,
        max_sessions: int | None,
        exit_when_blocked: bool,
    ) -> WorkerRunSummary:
        turns = 0
        sessions_started = 0
        transient_provider_retries = 0
        startup_delay = (
            self.worker_ids.index(worker_id) * self.startup_stagger_seconds
        )
        if self.stop_event.wait(startup_delay):
            return WorkerRunSummary(
                worker_id, turns, sessions_started, "stopped", "supervisor_stopped"
            )
        with CampaignService(
            repo_root=self.repo_root,
            state_root=self.state_root,
            credentials=self.credentials,
            event_notifier=self.campaign_notifier,
        ) as service:
            if worker_id not in service.configuration.workers:
                raise CampaignServiceError(f"unknown campaign worker: {worker_id}")
            if not service.configuration.workers[worker_id].enabled:
                raise CampaignServiceError(f"campaign worker is disabled: {worker_id}")
            worker = service.configuration.workers[worker_id]
            while not self.stop_event.is_set():
                if max_turns is not None and turns >= max_turns:
                    return WorkerRunSummary(
                        worker_id, turns, sessions_started,
                        service.control.effective_state(worker_id), "turn_limit",
                    )
                worker_sessions = tuple(
                    session
                    for session in service.sessions.list_sessions()
                    if session.worker_id == worker_id
                )
                session = self._open_session(worker_sessions)
                state = service.control.effective_state(worker_id)
                if state != "running":
                    latest_change = service.control.latest_change(worker_id)
                    retry_delay = transient_provider_retry_delay(
                        session.pause_reason if session is not None else None,
                        transient_provider_retries,
                    )
                    recoverable_supervisor_pause = bool(
                        state == "paused"
                        and service.control.global_state() == "running"
                        and session is not None
                        and session.status == "paused"
                        and retry_delay is not None
                        and is_supervisor_provider_pause(latest_change)
                    )
                    if recoverable_supervisor_pause:
                        if self.stop_event.wait(retry_delay):
                            break
                        service.sessions.resume(session.session_id)
                        service.control.set_worker(
                            worker_id,
                            "running",
                            request_id=f"transient-retry:{uuid.uuid4()}",
                            source="supervisor",
                            actor="campaign-supervisor",
                            command="resume after transient provider error",
                        )
                        transient_provider_retries += 1
                        self.event_sink(
                            {
                                "event": "provider_retry",
                                "worker_id": worker_id,
                                "session_id": session.session_id,
                                "attempt": transient_provider_retries,
                                "delay_seconds": retry_delay,
                            }
                        )
                        continue
                    if exit_when_blocked:
                        return WorkerRunSummary(
                            worker_id, turns, sessions_started, state, "control_blocked"
                        )
                    self.stop_event.wait(self.idle_seconds)
                    continue
                if session is not None and session.status == "paused":
                    retry_delay = transient_provider_retry_delay(
                        session.pause_reason,
                        transient_provider_retries,
                    )
                    if retry_delay is not None:
                        if self.stop_event.wait(retry_delay):
                            break
                        service.sessions.resume(session.session_id)
                        transient_provider_retries += 1
                        self.event_sink(
                            {
                                "event": "provider_retry",
                                "worker_id": worker_id,
                                "session_id": session.session_id,
                                "attempt": transient_provider_retries,
                                "delay_seconds": retry_delay,
                            }
                        )
                        continue
                    self._pause_worker(service, worker_id, "paused_session")
                    self._alert(
                        "FUZZYNTH SUPERVISOR — worker remains paused\n"
                        f"worker={worker_id}\nsession={session.session_id}\n"
                        f"reason={session.pause_reason or 'unknown'}"
                    )
                    if exit_when_blocked:
                        return WorkerRunSummary(
                            worker_id, turns, sessions_started, "paused", "session_paused"
                        )
                    self.stop_event.wait(self.idle_seconds)
                    continue
                if session is None:
                    if max_sessions is not None and sessions_started >= max_sessions:
                        return WorkerRunSummary(
                            worker_id, turns, sessions_started, state, "session_limit"
                        )
                    ordinal = len(worker_sessions) + 1
                    seed = _stable_seed(
                        self.base_seed,
                        worker.corpus_pair_id,
                        ordinal,
                    )
                    window = self.corpus.build_window(
                        seed=seed,
                        size=self.window_size,
                        strategy=worker.corpus_strategy,
                        routing_ordinal=ordinal,
                    )
                    session = service.start_session(
                        worker_id,
                        seed=seed,
                        corpus_window=window,
                    )
                    sessions_started += 1
                    self.event_sink(
                        {
                            "event": "session_started",
                            "session_id": session.session_id,
                            "worker_id": worker_id,
                            "target_turns": session.target_turns,
                            "corpus_sha256": session.corpus.sha256 if session.corpus else None,
                            "corpus_pair_id": worker.corpus_pair_id,
                            "prompt_variant": worker.prompt_variant,
                            "corpus_strategy": worker.corpus_strategy,
                            "corpus_staging_target": extract_wasm_staging_target(
                                window
                            ),
                        }
                    )
                result = service.run_session(session.session_id, max_turns=1)
                adaptive_reset = None
                current_session = result.session
                if result.turns and current_session.status == "active":
                    adaptive_reset = adaptive_session_reset_reason(
                        service.sessions.history(
                            current_session.session_id,
                            limit=2,
                        )
                    )
                    if adaptive_reset is not None:
                        current_session = service.sessions.complete_early(
                            current_session.session_id
                        )
                        self.event_sink(
                            {
                                "event": "adaptive_session_reset",
                                "worker_id": worker_id,
                                "session_id": current_session.session_id,
                                "reason": adaptive_reset,
                            }
                        )
                if result.turns:
                    turns += 1
                    turn = result.turns[0]
                    if turn.pause_reason is None:
                        transient_provider_retries = 0
                    diagnostic = (
                        diagnose_harness_misuse(
                            turn.program or b"",
                            turn.execution.stderr,
                        )
                        if turn.execution is not None
                        else None
                    )
                    self.event_sink(
                        {
                            "event": "turn_completed",
                            "worker_id": worker_id,
                            "corpus_pair_id": worker.corpus_pair_id,
                            "prompt_variant": worker.prompt_variant,
                            "session_id": result.session.session_id,
                            "session_status": current_session.status,
                            "generation_id": turn.generation_id,
                            "execution_id": (
                                turn.execution.execution_id if turn.execution else None
                            ),
                            "outcome": turn.execution.outcome if turn.execution else None,
                            "duration_ms": (
                                turn.execution.duration_ms if turn.execution else None
                            ),
                            "input_tokens": turn.input_tokens,
                            "cached_input_tokens": turn.cached_input_tokens,
                            "output_tokens": turn.output_tokens,
                            "reasoning_tokens": turn.reasoning_tokens,
                            "actual_microunits": turn.actual_microunits,
                            "pause_reason": turn.pause_reason,
                            "stop_reason": turn.stop_reason,
                            "suspected_harness_misuse": (
                                diagnostic.code if diagnostic is not None else None
                            ),
                            "adaptive_session_reset": adaptive_reset,
                        }
                    )
                if result.session.status == "paused":
                    retry_delay = transient_provider_retry_delay(
                        result.session.pause_reason,
                        transient_provider_retries,
                    )
                    if retry_delay is not None:
                        if self.stop_event.wait(retry_delay):
                            break
                        service.sessions.resume(result.session.session_id)
                        transient_provider_retries += 1
                        self.event_sink(
                            {
                                "event": "provider_retry",
                                "worker_id": worker_id,
                                "session_id": result.session.session_id,
                                "attempt": transient_provider_retries,
                                "delay_seconds": retry_delay,
                            }
                        )
                        continue
                    self._pause_worker(
                        service,
                        worker_id,
                        result.session.pause_reason or "session_paused",
                    )
                    if exit_when_blocked:
                        return WorkerRunSummary(
                            worker_id, turns, sessions_started, "paused",
                            result.session.pause_reason or "session_paused",
                        )
                    self.stop_event.wait(self.idle_seconds)
                    continue
        return WorkerRunSummary(
            worker_id, turns, sessions_started, "stopped", "supervisor_stopped"
        )

    def _guarded_worker(self, worker_id: str, **limits: object) -> WorkerRunSummary:
        error_type = ""
        try:
            return self._run_worker(worker_id, **limits)
        except BudgetLimitError as exc:
            reason = "budget_limit"
            error_type = type(exc).__name__
        except (CampaignServiceError, SessionStateError, ControlStateError) as exc:
            reason = "campaign_state_error"
            error_type = type(exc).__name__
        except Exception as exc:
            reason = "unexpected_supervisor_error"
            error_type = type(exc).__name__
        if self.stop_event.is_set():
            return WorkerRunSummary(
                worker_id, 0, 0, "stopped", "supervisor_stopped"
            )
        try:
            with CampaignService(
                repo_root=self.repo_root,
                state_root=self.state_root,
                credentials=self.credentials,
            ) as service:
                self._pause_worker(service, worker_id, reason)
        except Exception:
            pass
        self._alert(
            "FUZZYNTH SUPERVISOR — worker paused\n"
            f"worker={worker_id}\nreason={reason}\nerror_type={error_type}\n"
            "action=manual_review_required"
        )
        self.event_sink(
            {
                "event": "worker_paused",
                "worker_id": worker_id,
                "reason": reason,
                "error_type": error_type,
            }
        )
        return WorkerRunSummary(worker_id, 0, 0, "paused", reason)

    def run(
        self,
        *,
        max_turns_per_worker: int | None = None,
        max_sessions_per_worker: int | None = None,
        exit_when_blocked: bool = False,
    ) -> tuple[WorkerRunSummary, ...]:
        for name, value in (
            ("max_turns_per_worker", max_turns_per_worker),
            ("max_sessions_per_worker", max_sessions_per_worker),
        ):
            if value is not None and (isinstance(value, bool) or value < 1):
                raise ValueError(f"{name} must be positive")
        limits = {
            "max_turns": max_turns_per_worker,
            "max_sessions": max_sessions_per_worker,
            "exit_when_blocked": exit_when_blocked,
        }
        summaries: list[WorkerRunSummary] = []
        with ThreadPoolExecutor(
            max_workers=len(self.worker_ids),
            thread_name_prefix="fuzzynth-worker",
        ) as executor:
            futures = {
                executor.submit(self._guarded_worker, worker_id, **limits): worker_id
                for worker_id in self.worker_ids
            }
            try:
                for future in as_completed(futures):
                    summaries.append(future.result())
            except KeyboardInterrupt:
                self.stop()
                for future in futures:
                    future.cancel()
                raise
        return tuple(sorted(summaries, key=lambda item: item.worker_id))
