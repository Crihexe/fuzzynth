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
from fuzzynth.control import ControlStateError
from fuzzynth.corpus import CorpusPool
from fuzzynth.credentials import CredentialStore
from fuzzynth.notifications import TelegramCampaignNotifier
from fuzzynth.sessions import SessionRecord, SessionStateError


EventSink = Callable[[dict[str, object]], None]
OperationalAlert = Callable[[str], None]


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
            while not self.stop_event.is_set():
                if max_turns is not None and turns >= max_turns:
                    return WorkerRunSummary(
                        worker_id, turns, sessions_started,
                        service.control.effective_state(worker_id), "turn_limit",
                    )
                state = service.control.effective_state(worker_id)
                if state != "running":
                    if exit_when_blocked:
                        return WorkerRunSummary(
                            worker_id, turns, sessions_started, state, "control_blocked"
                        )
                    self.stop_event.wait(self.idle_seconds)
                    continue
                worker_sessions = tuple(
                    session
                    for session in service.sessions.list_sessions()
                    if session.worker_id == worker_id
                )
                session = self._open_session(worker_sessions)
                if session is not None and session.status == "paused":
                    self._pause_worker(service, worker_id, "paused_session")
                    self._alert(
                        "FUZZYNTH SUPERVISOR — worker remains paused\n"
                        f"worker={worker_id}\nsession={session.session_id}\n"
                        f"reason={session.pause_reason or 'unknown'}"
                    )
                    return WorkerRunSummary(
                        worker_id, turns, sessions_started, "paused", "session_paused"
                    )
                if session is None:
                    if max_sessions is not None and sessions_started >= max_sessions:
                        return WorkerRunSummary(
                            worker_id, turns, sessions_started, state, "session_limit"
                        )
                    ordinal = len(worker_sessions) + 1
                    seed = _stable_seed(self.base_seed, worker_id, ordinal)
                    window = self.corpus.build_window(seed=seed, size=self.window_size)
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
                        }
                    )
                result = service.run_session(session.session_id, max_turns=1)
                if result.turns:
                    turns += 1
                    turn = result.turns[0]
                    self.event_sink(
                        {
                            "event": "turn_completed",
                            "worker_id": worker_id,
                            "session_id": result.session.session_id,
                            "session_status": result.session.status,
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
                        }
                    )
                if result.session.status == "crash":
                    self._pause_worker(service, worker_id, "crash_candidate")
                    return WorkerRunSummary(
                        worker_id, turns, sessions_started, "paused", "crash_candidate"
                    )
                if result.session.status == "paused":
                    self._pause_worker(
                        service,
                        worker_id,
                        result.session.pause_reason or "session_paused",
                    )
                    return WorkerRunSummary(
                        worker_id, turns, sessions_started, "paused",
                        result.session.pause_reason or "session_paused",
                    )
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
