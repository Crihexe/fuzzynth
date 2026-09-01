from __future__ import annotations

import json
import unittest

from fuzzynth.sse import (
    ResponsesStreamAssembler,
    SSEDecoder,
    StreamProtocolError,
)


class SSEDecoderTests(unittest.TestCase):
    def test_decodes_events_across_arbitrary_chunks(self) -> None:
        decoder = SSEDecoder()

        first = decoder.feed(b"event: response.output_text.delta\r\ndata: {\"type\":")
        second = decoder.feed(
            b'"response.output_text.delta","delta":"let "}\r\n\r\n'
        )
        decoder.finish()

        self.assertEqual(first, [])
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].event, "response.output_text.delta")

    def test_joins_multiline_data(self) -> None:
        decoder = SSEDecoder()

        events = decoder.feed(b"event: sample\ndata: first\ndata: second\n\n")

        self.assertEqual(events[0].data, b"first\nsecond")

    def test_rejects_incomplete_tail(self) -> None:
        decoder = SSEDecoder()
        decoder.feed(b"data: partial")

        with self.assertRaisesRegex(StreamProtocolError, "incomplete"):
            decoder.finish()


class ResponsesStreamAssemblerTests(unittest.TestCase):
    def test_assembles_exact_semantic_utf8_output(self) -> None:
        decoder = SSEDecoder()
        assembler = ResponsesStreamAssembler()
        payloads = (
            {"type": "response.output_text.delta", "delta": 'const x = "λ";'},
            {"type": "response.output_text.delta", "delta": "\nprint(x);"},
            {"type": "response.completed", "response": {"id": "r_1"}},
        )
        raw = b"".join(
            b"data: " + json.dumps(payload).encode() + b"\n\n"
            for payload in payloads
        )

        for event in decoder.feed(raw):
            assembler.accept(event)
        decoder.finish()
        result = assembler.finish()

        self.assertEqual(result.output, 'const x = "λ";\nprint(x);'.encode())
        self.assertEqual(result.terminal_type, "response.completed")
        self.assertEqual(result.response, {"id": "r_1"})

    def test_done_sentinel_is_terminal(self) -> None:
        decoder = SSEDecoder()
        assembler = ResponsesStreamAssembler()

        for event in decoder.feed(b"data: [DONE]\n\n"):
            assembler.accept(event)

        self.assertEqual(assembler.finish().terminal_type, "done")

    def test_rejects_non_string_delta(self) -> None:
        decoder = SSEDecoder()
        assembler = ResponsesStreamAssembler()
        event = decoder.feed(
            b'data: {"type":"response.output_text.delta","delta":7}\n\n'
        )[0]

        with self.assertRaisesRegex(StreamProtocolError, "not a string"):
            assembler.accept(event)

    def test_requires_terminal_event(self) -> None:
        assembler = ResponsesStreamAssembler()

        with self.assertRaisesRegex(StreamProtocolError, "terminal"):
            assembler.finish()


if __name__ == "__main__":
    unittest.main()
