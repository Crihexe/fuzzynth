"""One complete budgeted generation -> d8 execution -> feedback turn."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable
import uuid

from fuzzynth.accounting import TokenUsage, UsageAccountingError
from fuzzynth.artifacts import ArtifactStore
from fuzzynth.budgets import BudgetLedger, MeterPolicy
from fuzzynth.campaign_config import CampaignWorker, SessionPlan
from fuzzynth.catalog import EvidenceCatalog, GenerationRecord
from fuzzynth.corpus import CorpusReference
from fuzzynth.execution_service import RecordedExecution, execute_program
from fuzzynth.outcomes import diagnose_harness_misuse
from fuzzynth.responses import (
    GenerationRequest,
    ResponsesClient,
    ResponsesError,
    StreamResult,
    extract_output_text,
    extract_usage,
)
from fuzzynth.session_context import (
    ConversationMessage,
    ExecutionFeedback,
    build_execution_feedback,
)


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

    def _execute(
        self,
        worker: CampaignWorker,
        generation_id: str,
        program: bytes,
    ) -> tuple[RecordedExecution, bytes]:
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
        diagnostic = diagnose_harness_misuse(program, execution.stderr)
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
                suspected_harness_misuse=(
                    diagnostic.code if diagnostic is not None else None
                ),
                triage_guidance=(
                    diagnostic.guidance if diagnostic is not None else None
                ),
            ),
            max_feedback_bytes=self.max_feedback_bytes,
        )
        return execution, feedback

    def run_turn(
        self,
        *,
        worker: CampaignWorker,
        session_id: str,
        turn_index: int,
        plan: SessionPlan,
        instructions: str,
        input_messages: tuple[ConversationMessage, ...],
        client: ResponsesClient,
        corpus_window_sha256: str | None = None,
        corpus_sources: tuple[CorpusReference, ...] = (),
    ) -> TurnResult:
        generation_id = f"gen-{uuid.uuid4()}"
        streaming_transport = worker.provider == "alternate"
        request = GenerationRequest(
            model=worker.model,
            instructions=instructions,
            input_messages=input_messages,
            # The custom endpoint rejects remote output caps and sampling
            # controls. Local artifact/program limits and conservative budget
            # reservations remain enforced independently.
            max_output_tokens=(
                None if streaming_transport else worker.max_output_tokens
            ),
            temperature=None if streaming_transport else plan.temperature,
            reasoning_effort=(
                plan.reasoning_effort if worker.send_reasoning else None
            ),
            verbosity=worker.verbosity if worker.send_verbosity else None,
            stream=streaming_transport,
        )
        request_bytes = request.to_bytes()
        request_ref = self.store.put(request_bytes)
        reservation = self.budgets.reserve(
            worker.meter,
            campaign_id=worker.worker_id,
            worker_id=worker.worker_id,
            max_input_tokens=len(request_bytes),
            max_output_tokens=worker.reservation_output_tokens,
            pricing_profile=worker.pricing_profile,
        )
        started_at = datetime.now(timezone.utc).isoformat()
        requested_parameters = {
            "budget_reservation_id": reservation.reservation_id,
            "corpus_sources": [source.as_dict() for source in corpus_sources],
            "corpus_window_sha256": corpus_window_sha256,
            "max_output_tokens_sent": request.max_output_tokens,
            "max_program_bytes": self.max_program_bytes,
            "reasoning_effort": plan.reasoning_effort,
            "reasoning_effort_sent": request.reasoning_effort,
            "reservation_output_tokens": worker.reservation_output_tokens,
            "session_seed": plan.seed,
            "stream": request.stream,
            "temperature_sent": request.temperature,
            "transport": "sse" if streaming_transport else "json",
            "turn_index": turn_index,
            "verbosity": worker.verbosity,
            "verbosity_sent": request.verbosity,
            "pricing_profile": worker.pricing_profile,
            "prompt_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
            "prompt_variant": worker.prompt_variant,
            "corpus_pair_id": worker.corpus_pair_id,
        }
        if streaming_transport:
            requested_parameters["terminal_partial_output_policy"] = "execute_once"

        stream_partial_output = b""
        try:
            stream_terminal_type: str | None = None
            stream_error_code: str | None = None
            if streaming_transport:
                streamed: StreamResult = client.stream(
                    request,
                    max_stream_bytes=self.max_response_bytes,
                )
                stream_terminal_type = streamed.terminal_type
                stream_error_code = streamed.error_code
                stream_partial_output = streamed.output
                if streamed.terminal_type == "error":
                    raise ResponsesError(
                        "provider returned a terminal stream error",
                        code=streamed.error_code or "stream_error",
                        raw_response=streamed.raw_sse,
                        partial_output=streamed.output,
                    )
                if streamed.response is None:
                    raise ResponsesError(
                        "provider stream did not yield a terminal response",
                        code="incomplete_stream",
                        raw_response=streamed.raw_sse,
                        partial_output=streamed.output,
                    )
                response = streamed.response
                raw_response = streamed.raw_sse
                # The completed response is authoritative. Comparing it with
                # the assembled deltas prevents a truncated/malformed stream
                # from silently becoming executable code.
                if (
                    streamed.terminal_type == "response.completed"
                    and extract_output_text(response) != streamed.output
                ):
                    raise ResponsesError(
                        "provider stream output disagrees with terminal response",
                        code="stream_output_mismatch",
                        raw_response=streamed.raw_sse,
                    )
            else:
                created = client.create_raw(
                    request,
                    max_response_bytes=self.max_response_bytes,
                )
                response = created.response
                raw_response = created.raw_response
        except ResponsesError as exc:
            self.budgets.mark_uncertain(reservation.reservation_id)
            finished_at = datetime.now(timezone.utc).isoformat()
            raw_ref = self.store.put(exc.raw_response) if exc.raw_response else None
            partial_program = exc.partial_output if streaming_transport else b""
            partial_ref = self.store.put(partial_program) if partial_program else None
            partial_executable = bool(
                partial_program and len(partial_program) <= self.max_program_bytes
            )
            partial_continuable = bool(
                partial_executable and exc.code == "request_timeout"
            )
            self.catalog.record_generation(
                GenerationRecord(
                    generation_id=generation_id,
                    campaign_id=worker.worker_id,
                    session_id=session_id,
                    provider=worker.provider,
                    requested_model=worker.model,
                    actual_model=None,
                    status="incomplete" if partial_program else "failed",
                    request=request_ref,
                    raw_stream=raw_ref,
                    program=partial_ref,
                    response_id=None,
                    requested_parameters=requested_parameters,
                    effective_parameters={
                        "error_code": exc.code,
                        "http_status": exc.status,
                        "partial_output_bytes": len(partial_program),
                        "partial_output_continuable": partial_continuable,
                        "partial_output_executable": partial_executable,
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
            pause_reason = (
                "provider_quota_or_rate_limit"
                if exc.status == 429
                else "provider_error"
            )
            if partial_program and not partial_executable:
                pause_reason = "program_too_large"
            if partial_executable:
                execution, feedback = self._execute(
                    worker,
                    generation_id,
                    partial_program,
                )
                if partial_continuable and not execution.bug_candidate:
                    # The worst-case reservation remains charged. Continue the
                    # bounded session so the next turn sees the exact prefix and
                    # factual d8 observation instead of discarding useful work.
                    pause_reason = None
                return TurnResult(
                    generation_id=generation_id,
                    execution=execution,
                    program=partial_program,
                    feedback=feedback,
                    pause_reason=pause_reason,
                    stop_reason=(
                        "bug_candidate" if execution.bug_candidate else None
                    ),
                    response_status=None,
                    input_tokens=None,
                    cached_input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    actual_microunits=None,
                )
            return TurnResult(
                generation_id=generation_id,
                execution=None,
                program=partial_program or None,
                feedback=None,
                pause_reason=pause_reason,
                stop_reason=None,
                response_status=None,
                input_tokens=None,
                cached_input_tokens=None,
                output_tokens=None,
                reasoning_tokens=None,
                actual_microunits=None,
            )

        raw_ref = self.store.put(raw_response)
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
            if streaming_transport and stream_partial_output:
                program = stream_partial_output
            else:
                try:
                    # A terminal JSON response can also contain useful output
                    # when max_output_tokens ends generation. Preserve and run
                    # that exact bounded prefix just like terminal SSE output.
                    program = extract_output_text(response)
                except ResponsesError as exc:
                    output_error = exc
            generation_status = "incomplete"
            if program is not None:
                program_ref = self.store.put(program)
                if len(program) > self.max_program_bytes:
                    generation_status = "failed"
                    pause_reason = pause_reason or "program_too_large"
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
        terminal_partial_continuation = bool(
            generation_status == "incomplete"
            and program
            and len(program) <= self.max_program_bytes
            and pause_reason in {"incomplete_response", "unknown_usage"}
        )
        if terminal_partial_continuation:
            pause_reason = None
        effective = _effective_parameters(response)
        if streaming_transport:
            effective["stream_terminal_type"] = stream_terminal_type
            effective["stream_error_code"] = stream_error_code
        if generation_status == "incomplete":
            incomplete_details = response.get("incomplete_details")
            effective["incomplete_reason"] = (
                incomplete_details.get("reason")
                if isinstance(incomplete_details, dict)
                else None
            )
            effective["partial_output_bytes"] = (
                len(program)
                if generation_status == "incomplete" and program
                else 0
            )
            effective["partial_output_continuation"] = (
                terminal_partial_continuation
            )
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
        if (
            generation_status not in {"completed", "incomplete"}
            or program is None
            or len(program) > self.max_program_bytes
        ):
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

        execution, feedback = self._execute(worker, generation_id, program)
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
