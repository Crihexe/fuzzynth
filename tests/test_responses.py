from __future__ import annotations

import unittest
from unittest.mock import patch

from fuzzynth.credentials import ProviderCredentials
from fuzzynth.responses import (
    GenerationRequest,
    ResponsesClient,
    ResponsesError,
)


class FakeResponse:
    def __init__(self, chunks: list[bytes], status: int = 200):
        self.chunks = chunks
        self.status = status

    def read1(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def read(self, size: int | None = None) -> bytes:
        data = b"".join(self.chunks)
        self.chunks.clear()
        return data if size is None else data[:size]


class FakeConnection:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.request_data = None
        self.closed = False

    def request(self, method, path, body, headers) -> None:
        self.request_data = (method, path, body, headers)

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class GenerationRequestTests(unittest.TestCase):
    def test_omits_unsupported_optional_parameters(self) -> None:
        payload = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
        ).to_payload()

        self.assertNotIn("temperature", payload)
        self.assertNotIn("reasoning", payload)
        self.assertNotIn("text", payload)

    def test_serializes_explicit_experiment_parameters(self) -> None:
        payload = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            temperature=1.3,
            reasoning_effort="low",
            verbosity="high",
        ).to_payload()

        self.assertEqual(payload["temperature"], 1.3)
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertEqual(payload["text"], {"verbosity": "high"})

    def test_rejects_invalid_temperature(self) -> None:
        request = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            temperature=2.1,
        )

        with self.assertRaisesRegex(ValueError, "temperature"):
            request.to_payload()


class StreamingClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ProviderCredentials(
            name="test",
            base_url="https://provider.invalid/v1",
            api_key="secret-test-key",
        )

    def request(self) -> GenerationRequest:
        return GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            stream=True,
        )

    def test_captures_raw_sse_and_canonical_output_separately(self) -> None:
        raw = (
            b'data: {"type":"response.output_text.delta","delta":"let x=1;"}\n\n'
            b'data: {"type":"response.completed","response":{"id":"r1"}}\n\n'
        )
        connection = FakeConnection(FakeResponse([raw[:19], raw[19:]]))
        captured_chunks: list[bytes] = []

        with patch(
            "fuzzynth.responses.http.client.HTTPSConnection",
            return_value=connection,
        ):
            result = ResponsesClient(self.provider).stream(
                self.request(), on_raw_chunk=captured_chunks.append
            )

        self.assertEqual(result.raw_sse, raw)
        self.assertEqual(b"".join(captured_chunks), raw)
        self.assertEqual(result.output, b"let x=1;")
        self.assertEqual(result.terminal_type, "response.completed")
        self.assertTrue(connection.closed)

    def test_enforces_local_stream_byte_limit(self) -> None:
        connection = FakeConnection(FakeResponse([b"data: oversized\n\n"]))

        with patch(
            "fuzzynth.responses.http.client.HTTPSConnection",
            return_value=connection,
        ):
            with self.assertRaises(ResponsesError) as raised:
                ResponsesClient(self.provider).stream(
                    self.request(), max_stream_bytes=4
                )

        self.assertEqual(raised.exception.code, "stream_too_large")
        self.assertTrue(connection.closed)

    def test_requires_explicit_stream_request(self) -> None:
        request = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
        )

        with self.assertRaisesRegex(ValueError, "streaming requires"):
            ResponsesClient(self.provider).stream(request)


if __name__ == "__main__":
    unittest.main()
