"""Incremental Server-Sent Events decoding for raw Responses API streams."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


class StreamProtocolError(RuntimeError):
    """A stream event cannot be interpreted without changing its semantics."""


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str
    data: bytes
    event_id: str | None


class SSEDecoder:
    """Decode arbitrary byte chunks while leaving raw capture to the caller."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        if not isinstance(chunk, bytes):
            raise TypeError("SSE chunks must be bytes")
        self._buffer.extend(chunk)
        events: list[SSEEvent] = []
        while True:
            boundary = self._find_boundary()
            if boundary is None:
                break
            end, separator_length = boundary
            block = bytes(self._buffer[:end])
            del self._buffer[: end + separator_length]
            event = self._decode_block(block)
            if event is not None:
                events.append(event)
        return events

    def finish(self) -> None:
        if self._buffer and bytes(self._buffer).strip(b"\r\n"):
            raise StreamProtocolError("SSE stream ended with an incomplete event")
        self._buffer.clear()

    def _find_boundary(self) -> tuple[int, int] | None:
        data = bytes(self._buffer)
        candidates = []
        for separator in (b"\r\n\r\n", b"\n\n", b"\r\r"):
            position = data.find(separator)
            if position >= 0:
                candidates.append((position, len(separator)))
        return min(candidates) if candidates else None

    @staticmethod
    def _decode_block(block: bytes) -> SSEEvent | None:
        event_name = "message"
        event_id: str | None = None
        data_lines: list[bytes] = []
        for line in block.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
            if not line or line.startswith(b":"):
                continue
            field, separator, value = line.partition(b":")
            if separator and value.startswith(b" "):
                value = value[1:]
            if field == b"data":
                data_lines.append(value)
            elif field == b"event":
                event_name = value.decode("utf-8", errors="strict")
            elif field == b"id":
                event_id = value.decode("utf-8", errors="strict")
        if not data_lines:
            return None
        return SSEEvent(event=event_name, data=b"\n".join(data_lines), event_id=event_id)


@dataclass(frozen=True, slots=True)
class AssembledResponse:
    output: bytes
    terminal_type: str | None
    response: dict[str, Any] | None
    error_code: str | None = None


class ResponsesStreamAssembler:
    """Assemble only explicit output-text deltas into canonical program bytes."""

    def __init__(self) -> None:
        self._output = bytearray()
        self._terminal_type: str | None = None
        self._response: dict[str, Any] | None = None
        self._error_code: str | None = None

    def accept(self, event: SSEEvent) -> None:
        if event.data == b"[DONE]":
            self._terminal_type = self._terminal_type or "done"
            return
        try:
            payload = json.loads(event.data)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise StreamProtocolError("SSE data is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise StreamProtocolError("SSE JSON payload is not an object")

        event_type = payload.get("type")
        if not isinstance(event_type, str):
            event_type = event.event
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if not isinstance(delta, str):
                raise StreamProtocolError("output-text delta is not a string")
            self._output.extend(delta.encode("utf-8"))
        elif event_type in {
            "response.completed",
            "response.failed",
            "response.incomplete",
            "error",
        }:
            self._terminal_type = event_type
            response = payload.get("response")
            if isinstance(response, dict):
                self._response = response
            code = payload.get("code")
            if not isinstance(code, str) and isinstance(response, dict):
                error = response.get("error")
                if isinstance(error, dict):
                    code = error.get("code") or error.get("type")
            if isinstance(code, str) and code:
                self._error_code = code[:80]

    def finish(self) -> AssembledResponse:
        if self._terminal_type is None:
            raise StreamProtocolError("Responses stream has no terminal event")
        return AssembledResponse(
            output=bytes(self._output),
            terminal_type=self._terminal_type,
            response=self._response,
            error_code=self._error_code,
        )
