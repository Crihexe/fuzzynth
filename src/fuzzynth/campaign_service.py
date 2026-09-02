"""Durable orchestration of iterative non-streaming campaign sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fuzzynth.artifacts import ArtifactStore
from fuzzynth.budgets import BudgetLedger, load_meter_policies, load_request_caps
from fuzzynth.campaign_config import (
    CampaignConfiguration,
    SessionPlan,
    choose_session_plan,
    load_campaign_configuration,
)
from fuzzynth.campaign_turn import CampaignTurnRunner, TurnResult
from fuzzynth.catalog import EvidenceCatalog
from fuzzynth.credentials import CredentialStore, ProviderCredentials
from fuzzynth.execution_service import RecordedExecution, execute_program
from fuzzynth.responses import ResponsesClient
from fuzzynth.session_context import build_turn_input
from fuzzynth.sessions import SessionLedger, SessionRecord, SessionStateError


class CampaignServiceError(RuntimeError):
    """A campaign cannot safely start or advance."""


ClientFactory = Callable[[ProviderCredentials, float], ResponsesClient]
Executor = Callable[..., RecordedExecution]


@dataclass(frozen=True, slots=True)
class SessionRunResult:
    session: SessionRecord
    turns: tuple[TurnResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session.session_id,
            "worker_id": self.session.worker_id,
            "status": self.session.status,
            "next_turn": self.session.next_turn,
            "target_turns": self.session.target_turns,
            "pause_reason": self.session.pause_reason,
            "turns": [turn.as_dict() for turn in self.turns],
        }


def _default_client_factory(
    provider: ProviderCredentials, timeout: float
) -> ResponsesClient:
    return ResponsesClient(provider=provider, timeout=timeout)


class CampaignService:
    def __init__(
        self,
        *,
        repo_root: Path,
        state_root: Path,
        credentials: CredentialStore,
        client_factory: ClientFactory = _default_client_factory,
        executor: Executor = execute_program,
    ):
        self.repo_root = repo_root.resolve()
        self.state_root = state_root.resolve()
        self.credentials = credentials
        self.client_factory = client_factory
        budget_path = self.repo_root / "config/budgets.toml"
        self.policies = load_meter_policies(budget_path)
        self.caps = load_request_caps(budget_path)
        self.configuration: CampaignConfiguration = load_campaign_configuration(
            self.repo_root / "config/campaign-workers.toml",
            repo_root=self.repo_root,
        )
        if self.configuration.context.max_context_bytes != self.caps.max_context_bytes:
            raise CampaignServiceError("context byte limits disagree across configs")
        if self.configuration.context.max_feedback_bytes != self.caps.max_feedback_bytes:
            raise CampaignServiceError("feedback byte limits disagree across configs")
        self.store = ArtifactStore(self.state_root / "artifacts")
        self.catalog = EvidenceCatalog(self.state_root / "catalog.sqlite3")
        self.budgets = BudgetLedger(
            self.state_root / "budgets.sqlite3",
            self.policies,
        )
        self.sessions = SessionLedger(
            self.state_root / "sessions.sqlite3",
            self.store,
        )
        self.executor = executor

    def close(self) -> None:
        self.sessions.close()
        self.budgets.close()
        self.catalog.close()

    def __enter__(self) -> CampaignService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start_session(
        self,
        worker_id: str,
        *,
        seed: int,
        corpus_window: bytes | None,
        allow_unconditioned: bool = False,
    ) -> SessionRecord:
        try:
            worker = self.configuration.workers[worker_id]
        except KeyError as exc:
            raise CampaignServiceError(f"unknown campaign worker: {worker_id}") from exc
        if not worker.enabled:
            raise CampaignServiceError(f"campaign worker is disabled: {worker_id}")
        if corpus_window is None and not allow_unconditioned:
            raise CampaignServiceError(
                "a selected corpus window is required until an explicit unconditioned run"
            )
        plan = choose_session_plan(worker, seed)
        return self.sessions.start(worker, plan, corpus_window=corpus_window)

    def _worker_and_plan(self, session: SessionRecord):
        try:
            worker = self.configuration.workers[session.worker_id]
        except KeyError as exc:
            raise CampaignServiceError("session worker is no longer configured") from exc
        plan = SessionPlan(
            seed=session.seed,
            target_turns=session.target_turns,
            reasoning_effort=session.reasoning_effort,
            temperature=session.temperature,
        )
        return worker, plan

    def run_session(
        self,
        session_id: str,
        *,
        max_turns: int | None = None,
    ) -> SessionRunResult:
        if max_turns is not None and max_turns < 1:
            raise ValueError("max_turns must be positive")
        session = self.sessions.get(session_id)
        if session.status != "active":
            raise SessionStateError("only an active session can run")
        worker, plan = self._worker_and_plan(session)
        try:
            meter_policy = self.policies[worker.meter]
        except KeyError as exc:
            raise CampaignServiceError("worker budget meter is unavailable") from exc
        provider = getattr(self.credentials, worker.provider, None)
        if not isinstance(provider, ProviderCredentials):
            raise CampaignServiceError("worker provider credentials are unavailable")
        try:
            instructions = worker.prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CampaignServiceError("worker prompt cannot be loaded") from exc
        corpus_window = self.sessions.corpus_bytes(session_id)
        turn_runner = CampaignTurnRunner(
            repo_root=self.repo_root,
            state_root=self.state_root,
            store=self.store,
            catalog=self.catalog,
            budgets=self.budgets,
            meter_policy=meter_policy,
            max_response_bytes=self.caps.max_response_bytes,
            max_program_bytes=self.caps.max_program_bytes,
            max_feedback_bytes=self.caps.max_feedback_bytes,
            executor=self.executor,
        )
        completed: list[TurnResult] = []
        while session.status == "active" and (
            max_turns is None or len(completed) < max_turns
        ):
            history = self.sessions.history(
                session_id,
                limit=worker.history_turns,
            )
            input_bytes = build_turn_input(
                turn_index=session.next_turn,
                history=history,
                history_turns=worker.history_turns,
                corpus_window=corpus_window,
                max_context_bytes=self.caps.max_context_bytes,
            )
            client = self.client_factory(provider, self.caps.wall_seconds)
            result = turn_runner.run_turn(
                worker=worker,
                session_id=session_id,
                turn_index=session.next_turn,
                plan=plan,
                instructions=instructions,
                input_bytes=input_bytes,
                client=client,
            )
            completed.append(result)
            session = self.sessions.record_turn(session_id, result)
        return SessionRunResult(session=session, turns=tuple(completed))

    def resume_session(self, session_id: str) -> SessionRecord:
        return self.sessions.resume(session_id)
