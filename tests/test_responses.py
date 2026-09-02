from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fuzzynth.credentials import ProviderCredentials
from fuzzynth.responses import (
    GenerationRequest,
    ResponsesClient,
    ResponsesError,
    extract_output_text,
    extract_usage,
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
        self.assertNotIn("max_output_tokens", payload)
        self.assertNotIn("reasoning", payload)
        self.assertNotIn("text", payload)

    def test_serializes_explicit_experiment_parameters(self) -> None:
        payload = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            max_output_tokens=512,
            temperature=1.3,
            reasoning_effort="low",
            verbosity="high",
        ).to_payload()

        self.assertEqual(payload["temperature"], 1.3)
        self.assertEqual(payload["max_output_tokens"], 512)
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

    def test_request_bytes_are_canonical_utf8(self) -> None:
        request = GenerationRequest(
            model="gpt-test",
            instructions="caffè",
            input_text="generate",
        )

        self.assertIn("caffè".encode(), request.to_bytes())
        self.assertNotIn(b" ", request.to_bytes())


class ResponseExtractionTests(unittest.TestCase):
    def test_extracts_only_output_text_parts(self) -> None:
        response = {
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "let x = 1;"},
                        {"type": "output_text", "text": "\nprint(x);"},
                    ],
                },
            ]
        }

        self.assertEqual(extract_output_text(response), b"let x = 1;\nprint(x);")

    def test_rejects_missing_output_text(self) -> None:
        with self.assertRaises(ResponsesError) as raised:
            extract_output_text({"output": [{"type": "reasoning"}]})

        self.assertEqual(raised.exception.code, "missing_output")

    def test_extracts_cached_and_reasoning_usage(self) -> None:
        usage = extract_usage(
            {
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 80},
                    "output_tokens": 40,
                    "output_tokens_details": {"reasoning_tokens": 24},
                }
            }
        )

        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.cached_input_tokens, 80)
        self.assertEqual(usage.output_tokens, 40)
        self.assertEqual(usage.reasoning_tokens, 24)


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
        self.assertEqual(raised.exception.raw_response, b"data: oversized\n\n")
        self.assertTrue(connection.closed)

    def test_preserves_non_success_stream_response(self) -> None:
        raw = b'{"error":{"code":"bad_request"}}'
        connection = FakeConnection(FakeResponse([raw], status=400))

        with patch(
            "fuzzynth.responses.http.client.HTTPSConnection",
            return_value=connection,
        ):
            with self.assertRaises(ResponsesError) as raised:
                ResponsesClient(self.provider).stream(self.request())

        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(raised.exception.raw_response, raw)

    def test_custom_stream_payload_can_omit_unsupported_controls(self) -> None:
        terminal = {
            "id": "r1",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "print(1);"}],
                }
            ],
        }
        raw = (
            b'data: {"type":"response.output_text.delta","delta":"print(1);"}\n\n'
            + b"data: "
            + json.dumps(
                {"type": "response.completed", "response": terminal},
                separators=(",", ":"),
            ).encode()
            + b"\n\n"
        )
        connection = FakeConnection(FakeResponse([raw]))
        request = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            reasoning_effort="xhigh",
            verbosity="high",
            stream=True,
        )

        with patch(
            "fuzzynth.responses.http.client.HTTPSConnection",
            return_value=connection,
        ):
            ResponsesClient(self.provider).stream(request)

        sent = json.loads(connection.request_data[2])
        self.assertTrue(sent["stream"])
        self.assertNotIn("max_output_tokens", sent)
        self.assertNotIn("max_completion_tokens", sent)
        self.assertNotIn("temperature", sent)
        self.assertNotIn("top_p", sent)

    def test_requires_explicit_stream_request(self) -> None:
        request = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
        )

        with self.assertRaisesRegex(ValueError, "streaming requires"):
            ResponsesClient(self.provider).stream(request)


class CreateClientTests(unittest.TestCase):
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
        )

    def test_enforces_local_json_response_byte_limit(self) -> None:
        connection = FakeConnection(FakeResponse([b'{"oversized":"value"}']))

        with patch(
            "fuzzynth.responses.http.client.HTTPSConnection",
            return_value=connection,
        ):
            with self.assertRaises(ResponsesError) as raised:
                ResponsesClient(self.provider).create(
                    self.request(), max_response_bytes=4
                )

        self.assertEqual(raised.exception.code, "response_too_large")
        self.assertEqual(raised.exception.raw_response, b'{"ove')
        self.assertTrue(connection.closed)

    def test_rejects_stream_request(self) -> None:
        request = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            stream=True,
        )

        with self.assertRaisesRegex(ValueError, "stream=False"):
            ResponsesClient(self.provider).create(request)


if __name__ == "__main__":
    unittest.main()
