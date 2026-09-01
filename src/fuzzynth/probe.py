"""Bounded, redacted capability probes for provider/model combinations."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from fuzzynth.credentials import ProviderCredentials
from fuzzynth.responses import GenerationRequest, ResponsesClient, ResponsesError


PROBE_INSTRUCTIONS = (
    "Return exactly one valid JavaScript expression statement. "
    "Output code only, with no Markdown or explanation."
)
PROBE_INPUT = "Produce the smallest program now."


@dataclass(frozen=True, slots=True)
class ProbeResult:
    provider: str
    requested_model: str
    actual_model: str | None
    supported: bool
    status: int | None
    response_status: str | None
    incomplete_reason: str | None
    error_code: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    temperature_sent: bool
    reasoning_effort_sent: str | None
    verbosity_sent: str | None
    effective_temperature: float | None
    effective_reasoning_effort: str | None
    effective_verbosity: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "supported": self.supported,
            "status": self.status,
            "response_status": self.response_status,
            "incomplete_reason": self.incomplete_reason,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens,
            },
            "parameters": {
                "temperature_sent": self.temperature_sent,
                "reasoning_effort": self.reasoning_effort_sent,
                "verbosity": self.verbosity_sent,
                "effective_temperature": self.effective_temperature,
                "effective_reasoning_effort": self.effective_reasoning_effort,
                "effective_verbosity": self.effective_verbosity,
            },
        }


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def run_probe(
    provider: ProviderCredentials,
    request: GenerationRequest,
    *,
    timeout: float = 30.0,
) -> ProbeResult:
    started = time.monotonic()
    try:
        response = ResponsesClient(provider=provider, timeout=timeout).create(request)
    except ResponsesError as exc:
        elapsed = round((time.monotonic() - started) * 1_000)
        return ProbeResult(
            provider=provider.name,
            requested_model=request.model,
            actual_model=None,
            supported=False,
            status=exc.status,
            response_status=None,
            incomplete_reason=None,
            error_code=exc.code or "request_error",
            latency_ms=elapsed,
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            temperature_sent=request.temperature is not None,
            reasoning_effort_sent=request.reasoning_effort,
            verbosity_sent=request.verbosity,
            effective_temperature=None,
            effective_reasoning_effort=None,
            effective_verbosity=None,
        )

    elapsed = round((time.monotonic() - started) * 1_000)
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    output_details = usage.get("output_tokens_details")
    if not isinstance(output_details, dict):
        output_details = {}
    actual_model = response.get("model")
    response_status = response.get("status")
    incomplete_details = response.get("incomplete_details")
    incomplete_reason = None
    if isinstance(incomplete_details, dict):
        candidate = incomplete_details.get("reason")
        if isinstance(candidate, str):
            incomplete_reason = candidate
    reasoning = response.get("reasoning")
    if not isinstance(reasoning, dict):
        reasoning = {}
    text = response.get("text")
    if not isinstance(text, dict):
        text = {}
    effective_temperature = response.get("temperature")

    return ProbeResult(
        provider=provider.name,
        requested_model=request.model,
        actual_model=actual_model if isinstance(actual_model, str) else None,
        supported=True,
        status=200,
        response_status=response_status if isinstance(response_status, str) else None,
        incomplete_reason=incomplete_reason,
        error_code=None,
        latency_ms=elapsed,
        input_tokens=_integer(usage.get("input_tokens")),
        output_tokens=_integer(usage.get("output_tokens")),
        reasoning_tokens=_integer(output_details.get("reasoning_tokens")),
        temperature_sent=request.temperature is not None,
        reasoning_effort_sent=request.reasoning_effort,
        verbosity_sent=request.verbosity,
        effective_temperature=(
            float(effective_temperature)
            if isinstance(effective_temperature, (int, float))
            and not isinstance(effective_temperature, bool)
            else None
        ),
        effective_reasoning_effort=(
            reasoning.get("effort")
            if isinstance(reasoning.get("effort"), str)
            else None
        ),
        effective_verbosity=(
            text.get("verbosity") if isinstance(text.get("verbosity"), str) else None
        ),
    )
