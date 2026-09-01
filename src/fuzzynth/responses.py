"""Small Responses API transport with explicit provider parameter control."""

from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
from typing import Any
from urllib.parse import urlsplit

from fuzzynth.credentials import ProviderCredentials


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
class ResponsesClient:
    provider: ProviderCredentials = field(repr=False)
    timeout: float = 30.0

    def create(self, request: GenerationRequest) -> dict[str, Any]:
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
            response_body = response.read()
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
