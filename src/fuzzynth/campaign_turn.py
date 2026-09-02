"""One budgeted non-streaming generation -> d8 execution -> feedback turn."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import uuid

from fuzzynth.accounting import TokenUsage, UsageAccountingError
from fuzzynth.artifacts import ArtifactStore
from fuzzynth.budgets import BudgetLedger, MeterPolicy
from fuzzynth.campaign_config import CampaignWorker, SessionPlan
from fuzzynth.catalog import EvidenceCatalog, GenerationRecord
from fuzzynth.execution_service import RecordedExecution, execute_program
from fuzzynth.responses import (
    GenerationRequest,
    ResponsesClient,
    ResponsesError,
    extract_output_text,
    extract_usage,
)
from fuzzynth.session_context import ExecutionFeedback, build_execution_feedback


@dataclass(frozen=True, slots=True)
class TurnResult:
    generation_id: str
    execution: RecordedExecution | None
    program: bytes | None
    feedback: bytes | None
    pause_reason: str | None
    stop_reason: str | None
    response_status: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    actual_microunits: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "execution": self.execution.as_dict() if self.execution else None,
            "pause_reason": self.pause_reason,
            "stop_reason": self.stop_reason,
            "response_status": self.response_status,
            "usage": {
                "input_tokens": self.input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens,
            },
            "actual_microunits": self.actual_microunits,
        }


Executor = Callable[..., RecordedExecution]


def _string(response: dict[str, object], name: str) -> str | None:
    value = response.get(name)
    return value if isinstance(value, str) else None


def _effective_parameters(response: dict[str, object]) -> dict[str, object]:
    reasoning = response.get("reasoning")
    text = response.get("text")
    return {
        "temperature": response.get("temperature"),
        "reasoning_effort": (
            reasoning.get("effort") if isinstance(reasoning, dict) else None
        ),
        "verbosity": text.get("verbosity") if isinstance(text, dict) else None,
    }


class CampaignTurnRunner:
    def __init__(
        self,
        *,
        repo_root: Path,
        state_root: Path,
        store: ArtifactStore,
        catalog: EvidenceCatalog,
        budgets: BudgetLedger,
        meter_policy: MeterPolicy,
        max_response_bytes: int,
        max_program_bytes: int,
        max_feedback_bytes: int,
        executor: Executor = execute_program,
    ):
        self.repo_root = repo_root
        self.state_root = state_root
        self.store = store
        self.catalog = catalog
        self.budgets = budgets
        self.meter_policy = meter_policy
        self.max_response_bytes = max_response_bytes
        self.max_program_bytes = max_program_bytes
        self.max_feedback_bytes = max_feedback_bytes
        self.executor = executor

    def run_turn(
        self,
        *,
        worker: CampaignWorker,
        session_id: str,
        turn_index: int,
        plan: SessionPlan,
        instructions: str,
        input_bytes: bytes,
        client: ResponsesClient,
    ) -> TurnResult:
        try:
            input_text = input_bytes.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("turn input must be valid UTF-8") from exc
        generation_id = f"gen-{uuid.uuid4()}"
        request = GenerationRequest(
            model=worker.model,
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=worker.max_output_tokens,
            temperature=plan.temperature,
            reasoning_effort=plan.reasoning_effort,
            verbosity=worker.verbosity,
            stream=False,
        )
        request_bytes = request.to_bytes()
        request_ref = self.store.put(request_bytes)
        reservation = self.budgets.reserve(
            worker.meter,
            campaign_id=worker.worker_id,
            worker_id=worker.worker_id,
            max_input_tokens=len(request_bytes),
            max_output_tokens=worker.reservation_output_tokens,
        )
        started_at = datetime.now(timezone.utc).isoformat()
        requested_parameters = {
            "budget_reservation_id": reservation.reservation_id,
            "max_output_tokens": worker.max_output_tokens,
            "reasoning_effort": plan.reasoning_effort,
            "session_seed": plan.seed,
            "temperature": plan.temperature,
            "turn_index": turn_index,
            "verbosity": worker.verbosity,
        }

        try:
            created = client.create_raw(
                request,
                max_response_bytes=self.max_response_bytes,
            )
        except ResponsesError as exc:
            self.budgets.mark_uncertain(reservation.reservation_id)
            finished_at = datetime.now(timezone.utc).isoformat()
            raw_ref = self.store.put(exc.raw_response) if exc.raw_response else None
            self.catalog.record_generation(
                GenerationRecord(
                    generation_id=generation_id,
                    campaign_id=worker.worker_id,
                    session_id=session_id,
                    provider=worker.provider,
                    requested_model=worker.model,
                    actual_model=None,
                    status="failed",
                    request=request_ref,
                    raw_stream=raw_ref,
                    program=None,
                    response_id=None,
                    requested_parameters=requested_parameters,
                    effective_parameters={
                        "error_code": exc.code,
                        "http_status": exc.status,
                    },
                    input_tokens=None,
                    cached_input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    cost_microusd=None,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
            return TurnResult(
                generation_id=generation_id,
                execution=None,
                program=None,
                feedback=None,
                pause_reason=(
                    "provider_quota_or_rate_limit"
                    if exc.status == 429
                    else "provider_error"
                ),
                stop_reason=None,
                response_status=None,
                input_tokens=None,
                cached_input_tokens=None,
                output_tokens=None,
                reasoning_tokens=None,
                actual_microunits=None,
            )

        response = created.response
        raw_ref = self.store.put(created.raw_response)
        response_status = _string(response, "status")
        usage: TokenUsage
        usage_known = True
        try:
            usage = extract_usage(response)
            if usage.input_tokens is None or usage.output_tokens is None:
                raise UsageAccountingError("provider usage is incomplete")
            settlement = self.budgets.settle(reservation.reservation_id, usage)
        except (UsageAccountingError, ValueError):
            usage = TokenUsage(input_tokens=None, output_tokens=None)
            settlement = None
            usage_known = False
            self.budgets.mark_uncertain(reservation.reservation_id)

        pause_reason = None if usage_known else "unknown_usage"
        if settlement is not None and (
            settlement.reservation_overrun or settlement.exhausted_by
        ):
            pause_reason = "budget_reservation_overrun"

        program: bytes | None = None
        program_ref = None
        generation_status = "completed"
        output_error: ResponsesError | None = None
        if response_status != "completed":
            generation_status = "incomplete"
            pause_reason = pause_reason or "incomplete_response"
        else:
            try:
                program = extract_output_text(response)
                program_ref = self.store.put(program)
                if len(program) > self.max_program_bytes:
                    generation_status = "failed"
                    pause_reason = pause_reason or "program_too_large"
            except ResponsesError as exc:
                output_error = exc
                generation_status = "failed"
                pause_reason = pause_reason or "invalid_output"

        finished_at = datetime.now(timezone.utc).isoformat()
        effective = _effective_parameters(response)
        if output_error is not None:
            effective["output_error"] = output_error.code
        actual_microunits = (
            settlement.actual_microunits if settlement is not None else None
        )
        self.catalog.record_generation(
            GenerationRecord(
                generation_id=generation_id,
                campaign_id=worker.worker_id,
                session_id=session_id,
                provider=worker.provider,
                requested_model=worker.model,
                actual_model=_string(response, "model"),
                status=generation_status,
                request=request_ref,
                raw_stream=raw_ref,
                program=program_ref,
                response_id=_string(response, "id"),
                requested_parameters=requested_parameters,
                effective_parameters=effective,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cost_microusd=(
                    actual_microunits
                    if self.meter_policy.unit == "USD"
                    else None
                ),
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        if generation_status != "completed" or program is None:
            return TurnResult(
                generation_id=generation_id,
                execution=None,
                program=program,
                feedback=None,
                pause_reason=pause_reason,
                stop_reason=None,
                response_status=response_status,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                actual_microunits=actual_microunits,
            )

        execution = self.executor(
            program,
            generation_id=generation_id,
            build_profile=worker.v8_build_profile,
            worker_profile=worker.v8_worker_profile,
            flags=worker.d8_flags,
            repo_root=self.repo_root,
            state_root=self.state_root,
            max_program_bytes=self.max_program_bytes,
        )
        feedback = build_execution_feedback(
            ExecutionFeedback(
                outcome=execution.outcome,
                exit_code=execution.exit_code,
                signal_name=execution.signal_name,
                timed_out=execution.timed_out,
                oom_killed=execution.oom_killed,
                output_truncated=execution.output_truncated,
                duration_ms=execution.duration_ms,
                stdout=execution.stdout,
                stderr=execution.stderr,
            ),
            max_feedback_bytes=self.max_feedback_bytes,
        )
        return TurnResult(
            generation_id=generation_id,
            execution=execution,
            program=program,
            feedback=feedback,
            pause_reason=pause_reason,
            stop_reason="bug_candidate" if execution.bug_candidate else None,
            response_status=response_status,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            actual_microunits=actual_microunits,
        )
