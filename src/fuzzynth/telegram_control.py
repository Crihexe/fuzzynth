"""Authenticated Telegram polling and owner campaign-control commands."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import http.client
import json
from pathlib import Path
import time
from typing import Callable
from urllib.parse import urlencode

from fuzzynth.artifacts import ArtifactIntegrityError, ArtifactStore
from fuzzynth.budgets import BudgetLedger, load_meter_policies
from fuzzynth.campaign_config import CampaignConfiguration, load_campaign_configuration
from fuzzynth.catalog import CatalogError, EvidenceCatalog
from fuzzynth.control import ControlLedger
from fuzzynth.corpus import extract_corpus_references
from fuzzynth.notifications import (
    MAX_MESSAGE_CHARS,
    NotificationError,
    TelegramCredentials,
    send_telegram_message,
)
from fuzzynth.outcomes import diagnose_harness_misuse
from fuzzynth.sessions import SessionLedger


MAX_UPDATES_BYTES = 256 * 1024
MAX_COMMAND_CHARS = 256
MAX_UPDATES_PER_POLL = 50
CONFIRMATION_WORD = "CONFIRM"


FetchUpdates = Callable[[TelegramCredentials, int, int], list[dict[str, object]]]
ReplySender = Callable[[TelegramCredentials, str], int]
ErrorHandler = Callable[[str], None]


def fetch_telegram_updates(
    credentials: TelegramCredentials,
    offset: int,
    poll_timeout: int,
) -> list[dict[str, object]]:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("Telegram offset must be non-negative")
    if (
        isinstance(poll_timeout, bool)
        or not isinstance(poll_timeout, int)
        or not 0 <= poll_timeout <= 50
    ):
        raise ValueError("Telegram poll timeout must be between 0 and 50 seconds")
    query = urlencode(
        {
            "offset": str(offset),
            "timeout": str(poll_timeout),
            "limit": str(MAX_UPDATES_PER_POLL),
            "allowed_updates": json.dumps(["message"], separators=(",", ":")),
        }
    )
    connection = http.client.HTTPSConnection(
        "api.telegram.org", timeout=max(15, poll_timeout + 10)
    )
    try:
        connection.request("GET", f"/bot{credentials.token}/getUpdates?{query}")
        response = connection.getresponse()
        body = response.read(MAX_UPDATES_BYTES + 1)
        if len(body) > MAX_UPDATES_BYTES:
            raise NotificationError("Telegram updates exceeded local byte limit")
    except (OSError, http.client.HTTPException) as exc:
        raise NotificationError(
            f"Telegram polling failed ({type(exc).__name__})"
        ) from exc
    finally:
        connection.close()
    try:
        document = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NotificationError(
            f"Telegram returned invalid update JSON (HTTP {response.status})"
        ) from exc
    result = document.get("result")
    if response.status != 200 or document.get("ok") is not True or not isinstance(
        result, list
    ):
        raise NotificationError(
            f"Telegram rejected update polling (HTTP {response.status})"
        )
    if len(result) > MAX_UPDATES_PER_POLL or any(
        not isinstance(update, dict) for update in result
    ):
        raise NotificationError("Telegram returned an invalid update batch")
    return result


@dataclass(frozen=True, slots=True)
class AuthorizedCommand:
    update_id: int
    actor_id: str
    text: str


def authorize_command(
    update: dict[str, object], credentials: TelegramCredentials
) -> AuthorizedCommand | None:
    update_id = update.get("update_id")
    message = update.get("message")
    if (
        isinstance(update_id, bool)
        or not isinstance(update_id, int)
        or update_id < 0
        or not isinstance(message, dict)
    ):
        return None
    chat = message.get("chat")
    sender = message.get("from")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(sender, dict) or not isinstance(
        text, str
    ):
        return None
    chat_id = chat.get("id")
    actor_id = sender.get("id")
    chat_type = chat.get("type")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        return None
    if isinstance(actor_id, bool) or not isinstance(actor_id, int):
        return None
    if str(chat_id) != credentials.chat_id:
        return None
    if credentials.user_id is not None:
        if str(actor_id) != credentials.user_id:
            return None
    elif chat_type != "private" or str(actor_id) != credentials.chat_id:
        # A group requires an explicit TELEGRAM_USER_ID. In a private chat the
        # configured chat and sender identifiers must be the same.
        return None
    text = text.strip()
    if not text or len(text) > MAX_COMMAND_CHARS:
        return None
    return AuthorizedCommand(update_id=update_id, actor_id=str(actor_id), text=text)


def _format_amount(microunits: int | None, unit: str) -> str:
    if microunits is None:
        return "unlimited"
    amount = microunits / 1_000_000
    if unit == "USD":
        return f"${amount:.6f}"
    return f"{amount:.6f} {unit}"


def _token_ratio(used: object, cap: object) -> str:
    used_int = int(used)
    return f"{used_int}/{int(cap)}" if cap is not None else str(used_int)


class TelegramControlService:
    """Processes safe commands and owns the durable status ledgers it reads."""

    def __init__(
        self,
        *,
        repo_root: Path,
        state_root: Path,
        credentials: TelegramCredentials,
    ):
        self.repo_root = repo_root.resolve()
        self.state_root = state_root.resolve()
        self.credentials = credentials
        self.configuration: CampaignConfiguration = load_campaign_configuration(
            self.repo_root / "config/campaign-workers.toml",
            repo_root=self.repo_root,
        )
        self.policies = load_meter_policies(self.repo_root / "config/budgets.toml")
        self.control = ControlLedger(self.state_root / "control.sqlite3")
        self.budgets = BudgetLedger(self.state_root / "budgets.sqlite3", self.policies)
        self.store = ArtifactStore(self.state_root / "artifacts")
        self.sessions = SessionLedger(
            self.state_root / "sessions.sqlite3",
            self.store,
        )
        self.catalog = EvidenceCatalog(self.state_root / "catalog.sqlite3")

    def close(self) -> None:
        self.catalog.close()
        self.sessions.close()
        self.budgets.close()
        self.control.close()

    def __enter__(self) -> TelegramControlService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _reload_configuration(self) -> None:
        # This service is intentionally long-lived while campaign deployments
        # may change the worker matrix. Never answer owner commands from a
        # startup-time snapshot of the TOML.
        self.configuration = load_campaign_configuration(
            self.repo_root / "config/campaign-workers.toml",
            repo_root=self.repo_root,
        )

    def _status(self) -> str:
        worker_ids = tuple(self.configuration.workers)
        snapshot = self.control.snapshot(worker_ids)
        counts = Counter(session.status for session in self.sessions.list_sessions())
        worker_states = Counter(
            (
                snapshot.effective_state(worker.worker_id)
                if worker.enabled
                else "disabled"
            )
            for worker in self.configuration.workers.values()
        )
        return "\n".join(
            (
                "FUZZYNTH STATUS",
                f"global={snapshot.global_state}",
                "workers=" + ",".join(
                    f"{state}:{count}" for state, count in sorted(worker_states.items())
                ),
                "sessions="
                + (
                    ",".join(
                        f"{state}:{count}" for state, count in sorted(counts.items())
                    )
                    or "none"
                ),
                "dataset="
                + ("enabled" if self.configuration.context.dataset_enabled else "disabled"),
                "live_campaign_cli=disabled",
            )
        )

    def _workers(self) -> str:
        snapshot = self.control.snapshot(tuple(self.configuration.workers))
        lines = ["FUZZYNTH WORKERS"]
        for worker in self.configuration.workers.values():
            configured = "enabled" if worker.enabled else "disabled"
            control = snapshot.effective_state(worker.worker_id)
            effective = control if worker.enabled else "disabled"
            lines.append(
                f"{worker.worker_id}: config={configured}, control={control}, "
                f"effective={effective}"
            )
        return "\n".join(lines)

    def _budgets(self) -> str:
        lines = ["FUZZYNTH BUDGETS (accounted + reserved)"]
        for meter_id in sorted(self.policies):
            status = self.budgets.status(meter_id)
            unit = str(status["unit"])
            lines.extend(
                (
                    f"{meter_id}: "
                    f"{_format_amount(int(status['total_microunits']), unit)}/"
                    f"{_format_amount(status['hard_total_microunits'], unit)}",
                    "  tokens "
                    + " ".join(
                        (
                            "uncached="
                            + _token_ratio(
                                status["uncached_input_tokens"],
                                status["hard_uncached_input_tokens"],
                            ),
                            "cached="
                            + _token_ratio(
                                status["cached_input_tokens"],
                                status["hard_cached_input_tokens"],
                            ),
                            "output="
                            + _token_ratio(
                                status["output_tokens"],
                                status["hard_output_tokens"],
                            ),
                        )
                    ),
                    f"  uncertain={status['uncertain_reservations']}",
                )
            )
        return "\n".join(lines)

    def _sessions(self) -> str:
        sessions = self.sessions.list_sessions()
        lines = ["FUZZYNTH SESSIONS"]
        if not sessions:
            lines.append("none")
        for session in sessions[-10:]:
            lines.append(
                f"{session.session_id}: {session.worker_id} {session.status} "
                f"turn={session.next_turn}/{session.target_turns}"
            )
        if len(sessions) > 10:
            lines.append(f"older_omitted={len(sessions) - 10}")
        return "\n".join(lines)

    def _last_crash(self) -> str:
        candidate = self.catalog.latest_bug_candidate()
        if candidate is None:
            return "FUZZYNTH LAST CRASH\nnone"
        lines = [
            "FUZZYNTH LAST CRASH",
            f"session={candidate.session_id or 'none'}",
            f"worker={candidate.worker_id}",
            f"generation={candidate.generation_id}",
            f"execution={candidate.execution_id}",
            f"outcome={candidate.outcome}",
            f"signal={candidate.signal_name or 'none'}",
            f"program_sha256={candidate.program_sha256}",
        ]
        if candidate.session_id is not None:
            try:
                session = self.sessions.get(candidate.session_id)
                lines.append(
                    "corpus_window_sha256="
                    + (
                        session.corpus.sha256
                        if session.corpus is not None
                        else "none"
                    )
                )
                if session.corpus is not None:
                    sources = extract_corpus_references(
                        self.store.read(session.corpus)
                    )
                    lines.extend(
                        f"corpus_source={source.name}@{source.sha256}"
                        for source in sources
                    )
            except (OSError, ArtifactIntegrityError):
                lines.append("corpus_provenance=unavailable")
        try:
            program = self.store.read(
                self.catalog.artifact_reference(candidate.program_sha256)
            )
            stderr = self.store.read(
                self.catalog.artifact_reference(candidate.stderr_sha256)
            )
            diagnostic = diagnose_harness_misuse(program, stderr)
            if diagnostic is not None:
                lines.append(
                    f"triage=suspected_harness_misuse:{diagnostic.code}"
                )
        except (OSError, ArtifactIntegrityError, CatalogError):
            lines.append("triage=unavailable")
        lines.append("action=evidence_saved_worker_continuing_no_automatic_replay")
        return "\n".join(lines)

    @staticmethod
    def _help() -> str:
        return "\n".join(
            (
                "FUZZYNTH COMMANDS",
                "/status /workers /sessions /cost /budget /lastcrash",
                "/pause <worker|all>",
                "/resume <worker|all>",
                f"/stop {CONFIRMATION_WORD}",
                f"/start {CONFIRMATION_WORD}",
                "Changes take effect before the next model turn.",
            )
        )

    def _mutate(self, command: AuthorizedCommand, verb: str, args: list[str]) -> str:
        request_id = f"telegram-update:{command.update_id}"
        actor = f"telegram:{command.actor_id}"
        normalized = " ".join((verb, *args))
        if verb in {"stop", "start"}:
            if args != [CONFIRMATION_WORD]:
                return f"Confirmation required: /{verb} {CONFIRMATION_WORD}"
            state = "stopped" if verb == "stop" else "running"
            change = self.control.set_global(
                state,
                request_id=request_id,
                source="telegram",
                actor=actor,
                command=normalized,
            )
            return (
                f"global={change.new_state}; applied={str(change.applied).lower()}; "
                "effective_before_next_turn=true"
            )
        if len(args) != 1:
            return f"Usage: /{verb} <worker|all>"
        target = args[0]
        state = "paused" if verb == "pause" else "running"
        if target == "all":
            if verb == "resume" and self.control.global_state() == "stopped":
                return f"Global state is stopped; use /start {CONFIRMATION_WORD}"
            if verb == "resume":
                changes = [
                    self.control.set_global(
                        state,
                        request_id=request_id + ":global",
                        source="telegram",
                        actor=actor,
                        command=normalized,
                    )
                ]
                changes.extend(
                    self.control.set_worker(
                        worker_id,
                        "running",
                        request_id=request_id + ":" + worker_id,
                        source="telegram",
                        actor=actor,
                        command=normalized,
                    )
                    for worker_id in self.configuration.workers
                )
                applied = any(item.applied for item in changes)
                change = changes[0]
            else:
                change = self.control.set_global(
                    state,
                    request_id=request_id,
                    source="telegram",
                    actor=actor,
                    command=normalized,
                )
                applied = change.applied
        else:
            if target not in self.configuration.workers:
                return "Unknown worker. Use /workers for exact IDs."
            change = self.control.set_worker(
                target,
                state,
                request_id=request_id,
                source="telegram",
                actor=actor,
                command=normalized,
            )
            applied = change.applied
        effective = (
            self.control.global_state()
            if target == "all"
            else self.control.effective_state(target)
        )
        resumed = 0
        if verb == "resume":
            for session in self.sessions.list_sessions():
                if session.status != "paused":
                    continue
                if target != "all" and session.worker_id != target:
                    continue
                if self.control.dispatch_allowed(session.worker_id):
                    self.sessions.resume(session.session_id)
                    resumed += 1
        return (
            f"target={change.target}; configured={change.new_state}; "
            f"effective={effective}; applied={str(applied).lower()}; "
            f"sessions_resumed={resumed}; "
            "takes_effect_before_next_turn=true"
        )

    def handle_update(self, update: dict[str, object]) -> str | None:
        command = authorize_command(update, self.credentials)
        if command is None:
            return None
        self._reload_configuration()
        parts = command.text.split()
        raw_verb = parts[0]
        if not raw_verb.startswith("/"):
            return self._help()
        verb = raw_verb[1:].split("@", maxsplit=1)[0].lower()
        args = parts[1:]
        if verb in {"status"} and not args:
            return self._status()
        if verb == "workers" and not args:
            return self._workers()
        if verb in {"cost", "budget"} and not args:
            return self._budgets()
        if verb == "sessions" and not args:
            return self._sessions()
        if verb == "lastcrash" and not args:
            return self._last_crash()
        if verb in {"pause", "resume", "stop", "start"}:
            return self._mutate(command, verb, args)
        return self._help()

    def poll_once(
        self,
        *,
        poll_timeout: int = 25,
        fetcher: FetchUpdates = fetch_telegram_updates,
        sender: ReplySender = send_telegram_message,
    ) -> int:
        updates = fetcher(
            self.credentials,
            self.control.telegram_offset(),
            poll_timeout,
        )
        processed = 0

        def update_key(update: dict[str, object]) -> int:
            update_id = update.get("update_id")
            if (
                isinstance(update_id, bool)
                or not isinstance(update_id, int)
                or update_id < 0
            ):
                raise NotificationError("Telegram update omitted a valid update ID")
            return update_id

        for update in sorted(updates, key=update_key):
            update_id = update.get("update_id")
            if (
                isinstance(update_id, bool)
                or not isinstance(update_id, int)
                or update_id < 0
            ):
                raise NotificationError("Telegram update omitted a valid update ID")
            reply = self.handle_update(update)
            if reply is not None:
                if len(reply) > MAX_MESSAGE_CHARS:
                    raise NotificationError("Telegram control reply exceeded byte limit")
                sender(self.credentials, reply)
            self.control.advance_telegram_offset(update_id + 1)
            processed += 1
        return processed


def run_control_loop(
    service: TelegramControlService,
    *,
    poll_timeout: int = 25,
    once: bool = False,
    error_handler: ErrorHandler | None = None,
) -> None:
    while True:
        try:
            service.poll_once(poll_timeout=poll_timeout)
        except NotificationError as exc:
            if once:
                raise
            if error_handler is not None:
                error_handler(str(exc))
            time.sleep(5)
        if once:
            return
