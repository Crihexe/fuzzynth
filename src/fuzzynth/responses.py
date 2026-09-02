"""Small Responses API transport with explicit provider parameter control."""

from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
from typing import Any, Callable
from urllib.parse import urlsplit

from fuzzynth.accounting import TokenUsage
from fuzzynth.credentials import ProviderCredentials
from fuzzynth.sse import ResponsesStreamAssembler, SSEDecoder, StreamProtocolError


class ResponsesError(RuntimeError):
    """A safe-to-display Responses API failure."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str = "",
        raw_response: bytes = b"",
        partial_output: bytes = b"",
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.raw_response = raw_response
        self.partial_output = partial_output


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    model: str
    instructions: str
    input_text: str
    max_output_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    verbosity: str | None = None
    stream: bool = False

    def to_payload(self) -> dict[str, Any]:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": self.instructions,
            "input": self.input_text,
            "stream": self.stream,
            "store": False,
        }
        if self.max_output_tokens is not None:
            payload["max_output_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        if self.verbosity is not None:
            payload["text"] = {"verbosity": self.verbosity}
        return payload

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class StreamResult:
    raw_sse: bytes
    output: bytes
    terminal_type: str
    response: dict[str, Any] | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CreateResult:
    raw_response: bytes
    response: dict[str, Any]


def extract_output_text(response: dict[str, Any]) -> bytes:
    """Extract only semantic final-output text from a completed response."""

    output = response.get("output")
    if not isinstance(output, list):
        raise ResponsesError("provider response has no output array", code="invalid_output")
    fragments: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise ResponsesError(
                "provider message has invalid content", code="invalid_output"
            )
        for part in content:
            if not isinstance(part, dict):
                raise ResponsesError(
                    "provider output content is invalid", code="invalid_output"
                )
            if part.get("type") != "output_text":
                continue
            value = part.get("text")
            if not isinstance(value, str):
                raise ResponsesError(
                    "provider output text is invalid", code="invalid_output"
                )
            fragments.append(value)
    if not fragments:
        raise ResponsesError("provider response has no output text", code="missing_output")
    try:
        return "".join(fragments).encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ResponsesError(
            "provider output is not valid UTF-8", code="invalid_output"
        ) from exc


def extract_usage(response: dict[str, Any]) -> TokenUsage:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage(input_tokens=None, output_tokens=None)
    input_details = usage.get("input_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    output_details = usage.get("output_tokens_details")
    if not isinstance(output_details, dict):
        output_details = {}

    def integer(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return TokenUsage(
        input_tokens=integer(usage.get("input_tokens")),
        cached_input_tokens=integer(input_details.get("cached_tokens")),
        output_tokens=integer(usage.get("output_tokens")),
        reasoning_tokens=integer(output_details.get("reasoning_tokens")),
    )


@dataclass(frozen=True, slots=True)
class ResponsesClient:
    provider: ProviderCredentials = field(repr=False)
    timeout: float = 30.0

    def stream(
        self,
        request: GenerationRequest,
        *,
        max_stream_bytes: int = 4 * 1024 * 1024,
        on_raw_chunk: Callable[[bytes], None] | None = None,
    ) -> StreamResult:
        if not request.stream:
            raise ValueError("streaming requires GenerationRequest(stream=True)")
        if max_stream_bytes < 1:
            raise ValueError("max_stream_bytes must be positive")
        parsed = urlsplit(self.provider.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ResponsesError("provider base URL is not a valid HTTPS endpoint")

        endpoint_path = f"{parsed.path.rstrip('/')}/responses"
        payload = request.to_bytes()
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            port=parsed.port,
            timeout=self.timeout,
        )
        decoder = SSEDecoder()
        assembler = ResponsesStreamAssembler()
        raw = bytearray()

        try:
            connection.request(
                "POST",
                endpoint_path,
                body=payload,
                headers={
                    "Authorization": f"Bearer {self.provider.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "User-Agent": "fuzzynth-stream/0.1",
                },
            )
            response = connection.getresponse()
            if response.status < 200 or response.status >= 300:
                response_body = response.read(max_stream_bytes + 1)
                raise ResponsesError(
                    f"provider rejected stream request (HTTP {response.status})",
                    status=response.status,
                    code="http_error",
                    raw_response=response_body,
                )

            while chunk := response.read1(64 * 1024):
                if len(raw) + len(chunk) > max_stream_bytes:
                    raise ResponsesError(
                        "provider stream exceeded local byte limit",
                        status=response.status,
                        code="stream_too_large",
                        raw_response=bytes(raw) + chunk,
                    )
                raw.extend(chunk)
                if on_raw_chunk is not None:
                    on_raw_chunk(chunk)
                for event in decoder.feed(chunk):
                    assembler.accept(event)
            decoder.finish()
            assembled = assembler.finish()
        except ResponsesError as exc:
            raise ResponsesError(
                str(exc),
                status=exc.status,
                code=exc.code,
                raw_response=exc.raw_response or bytes(raw),
                partial_output=exc.partial_output or assembler.output_so_far,
            ) from exc
        except StreamProtocolError as exc:
            raise ResponsesError(
                f"provider stream protocol failed ({exc})",
                code="stream_protocol_error",
                raw_response=bytes(raw),
                partial_output=assembler.output_so_far,
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ResponsesError(
                f"provider stream failed ({type(exc).__name__})",
                code="network_error",
                raw_response=bytes(raw),
                partial_output=assembler.output_so_far,
            ) from exc
        finally:
            connection.close()

        return StreamResult(
            raw_sse=bytes(raw),
            output=assembled.output,
            terminal_type=assembled.terminal_type or "",
            response=assembled.response,
            error_code=assembled.error_code,
        )

    def create_raw(
        self,
        request: GenerationRequest,
        *,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> CreateResult:
        if request.stream:
            raise ValueError("non-streaming create requires stream=False")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        parsed = urlsplit(self.provider.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ResponsesError("provider base URL is not a valid HTTPS endpoint")

        endpoint_path = f"{parsed.path.rstrip('/')}/responses"
        payload = request.to_bytes()
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            port=parsed.port,
            timeout=self.timeout,
        )

        try:
            connection.request(
                "POST",
                endpoint_path,
                body=payload,
                headers={
                    "Authorization": f"Bearer {self.provider.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "fuzzynth-capability-probe/0.1",
                },
            )
            response = connection.getresponse()
            response_body = response.read(max_response_bytes + 1)
            if len(response_body) > max_response_bytes:
                raise ResponsesError(
                    "provider response exceeded local byte limit",
                    status=response.status,
                    code="response_too_large",
                    raw_response=response_body,
                )
        except (OSError, http.client.HTTPException) as exc:
            raise ResponsesError(
                f"provider request failed ({type(exc).__name__})",
                code="network_error",
            ) from exc
        finally:
            connection.close()

        try:
            decoded = json.loads(response_body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ResponsesError(
                f"provider returned invalid JSON (HTTP {response.status})",
                status=response.status,
                code="invalid_json",
                raw_response=response_body,
            ) from exc

        if not isinstance(decoded, dict):
            raise ResponsesError(
                f"provider returned an invalid response shape (HTTP {response.status})",
                status=response.status,
                code="invalid_shape",
                raw_response=response_body,
            )

        if response.status < 200 or response.status >= 300:
            error = decoded.get("error")
            code = "http_error"
            if isinstance(error, dict):
                candidate = error.get("code") or error.get("type")
                if isinstance(candidate, str) and candidate:
                    code = candidate[:80]
            raise ResponsesError(
                f"provider rejected request (HTTP {response.status}, code={code})",
                status=response.status,
                code=code,
                raw_response=response_body,
            )

        return CreateResult(raw_response=response_body, response=decoded)

    def create(
        self,
        request: GenerationRequest,
        *,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> dict[str, Any]:
        return self.create_raw(
            request,
            max_response_bytes=max_response_bytes,
        ).response
