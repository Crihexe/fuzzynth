"""Small Responses API transport with explicit provider parameter control."""

from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
from typing import Any, Callable
from urllib.parse import urlsplit

from fuzzynth.credentials import ProviderCredentials
from fuzzynth.sse import ResponsesStreamAssembler, SSEDecoder, StreamProtocolError


class ResponsesError(RuntimeError):
    """A safe-to-display Responses API failure."""

    def __init__(self, message: str, *, status: int | None = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    model: str
    instructions: str
    input_text: str
    max_output_tokens: int = 64
    temperature: float | None = None
    reasoning_effort: str | None = None
    verbosity: str | None = None
    stream: bool = False

    def to_payload(self) -> dict[str, Any]:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": self.instructions,
            "input": self.input_text,
            "max_output_tokens": self.max_output_tokens,
            "stream": self.stream,
            "store": False,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        if self.verbosity is not None:
            payload["text"] = {"verbosity": self.verbosity}
        return payload


@dataclass(frozen=True, slots=True)
class StreamResult:
    raw_sse: bytes
    output: bytes
    terminal_type: str
    response: dict[str, Any] | None


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
        payload = json.dumps(request.to_payload()).encode("utf-8")
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
                response.read(max_stream_bytes)
                raise ResponsesError(
                    f"provider rejected stream request (HTTP {response.status})",
                    status=response.status,
                    code="http_error",
                )

            while chunk := response.read1(64 * 1024):
                if len(raw) + len(chunk) > max_stream_bytes:
                    raise ResponsesError(
                        "provider stream exceeded local byte limit",
                        status=response.status,
                        code="stream_too_large",
                    )
                raw.extend(chunk)
                if on_raw_chunk is not None:
                    on_raw_chunk(chunk)
                for event in decoder.feed(chunk):
                    assembler.accept(event)
            decoder.finish()
            assembled = assembler.finish()
        except ResponsesError:
            raise
        except StreamProtocolError as exc:
            raise ResponsesError(
                f"provider stream protocol failed ({exc})",
                code="stream_protocol_error",
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ResponsesError(
                f"provider stream failed ({type(exc).__name__})",
                code="network_error",
            ) from exc
        finally:
            connection.close()

        return StreamResult(
            raw_sse=bytes(raw),
            output=assembled.output,
            terminal_type=assembled.terminal_type or "",
            response=assembled.response,
        )

    def create(
        self,
        request: GenerationRequest,
        *,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> dict[str, Any]:
        if request.stream:
            raise ValueError("non-streaming create requires stream=False")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        parsed = urlsplit(self.provider.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ResponsesError("provider base URL is not a valid HTTPS endpoint")

        endpoint_path = f"{parsed.path.rstrip('/')}/responses"
        payload = json.dumps(request.to_payload()).encode("utf-8")
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
            ) from exc

        if not isinstance(decoded, dict):
            raise ResponsesError(
                f"provider returned an invalid response shape (HTTP {response.status})",
                status=response.status,
                code="invalid_shape",
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
            )

        return decoded
