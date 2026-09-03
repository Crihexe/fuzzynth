from __future__ import annotations

import json
import unittest

from fuzzynth.session_context import (
    ExecutionFeedback,
    SessionContextError,
    TurnContext,
    build_execution_feedback,
    build_turn_input,
)


class ExecutionFeedbackTests(unittest.TestCase):
    def test_feedback_is_canonical_bounded_and_factual(self) -> None:
        encoded = build_execution_feedback(
            ExecutionFeedback(
                outcome="javascript_exception",
                exit_code=1,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                duration_ms=42,
                stdout=b"x" * 20_000,
                stderr=b"prefix\nTypeError: bad\n",
            ),
            max_feedback_bytes=512,
        )

        self.assertLessEqual(len(encoded), 512)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["outcome"], "javascript_exception")
        self.assertIn("TypeError", decoded["stderr_tail"])

    def test_binary_output_is_representable(self) -> None:
        encoded = build_execution_feedback(
            ExecutionFeedback(
                outcome="ok",
                exit_code=0,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                duration_ms=1,
                stdout=b"\xff\xfe",
                stderr=b"",
            ),
            max_feedback_bytes=512,
        )

        self.assertIn("\ufffd", encoded.decode())

    def test_suspected_harness_misuse_guidance_is_bounded_feedback(self) -> None:
        encoded = build_execution_feedback(
            ExecutionFeedback(
                outcome="v8_fatal",
                exit_code=134,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                duration_ms=2,
                stdout=b"",
                stderr=b"Check failed: EnsureCompiledAndFeedbackVector",
                suspected_harness_misuse="invalid_percent_gc_intrinsic",
                triage_guidance="Use gc() instead.",
            ),
            max_feedback_bytes=1024,
        )

        decoded = json.loads(encoded)
        self.assertEqual(
            decoded["triage_hint"]["code"],
            "invalid_percent_gc_intrinsic",
        )

    def test_program_observation_is_preserved_as_structured_feedback(self) -> None:
        encoded = build_execution_feedback(
            ExecutionFeedback(
                outcome="ok",
                exit_code=0,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                duration_ms=2,
                stdout=b"",
                stderr=b"",
                program_observation={
                    "subsystem": "wasm",
                    "runtime_path_completed": True,
                },
            ),
            max_feedback_bytes=1024,
        )

        decoded = json.loads(encoded)
        self.assertTrue(
            decoded["program_observation"]["runtime_path_completed"]
        )

    def test_semantic_identity_survives_small_feedback_compaction(self) -> None:
        encoded = build_execution_feedback(
            ExecutionFeedback(
                outcome="ok",
                exit_code=0,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                duration_ms=2,
                stdout=b"x" * 1000,
                stderr=b"y" * 1000,
                program_observation={
                    "static_features": {"proxy": True},
                    "semantic_profile": {
                        "signature": "1" * 16,
                        "mechanisms": ["proxy_traps"] * 24,
                        "operations": ["get"] * 24,
                    },
                    "semantic_novelty": {
                        "new_mechanisms": [],
                        "new_operations": [],
                        "repeated_globally": True,
                        "signature_occurrence": 4,
                    },
                },
            ),
            max_feedback_bytes=512,
        )

        decoded = json.loads(encoded)
        observation = decoded["program_observation"]
        self.assertEqual(observation["semantic_profile"]["signature"], "1" * 16)
        self.assertTrue(observation["semantic_novelty"]["repeated_globally"])


class TurnInputTests(unittest.TestCase):
    @staticmethod
    def turn(index: int, size: int = 20) -> TurnContext:
        return TurnContext(
            turn_index=index,
            program=(f"// turn {index}\n" + "x" * size).encode(),
            feedback=b'{"outcome":"ok"}',
        )

    def test_includes_only_configured_recent_turns(self) -> None:
        result = build_turn_input(
            turn_index=4,
            history=(self.turn(1), self.turn(2), self.turn(3)),
            history_turns=2,
            corpus_window=None,
            max_context_bytes=4096,
        )

        payload = [message.as_dict() for message in result]
        encoded = json.dumps(payload).encode()
        self.assertNotIn(b"// turn 1", encoded)
        self.assertIn(b"// turn 2", encoded)
        self.assertIn(b"// turn 3", encoded)
        self.assertEqual(
            [message.role for message in result],
            ["user", "assistant", "user", "assistant", "user"],
        )
        self.assertEqual(result[1].content, self.turn(2).program.decode())
        self.assertNotIn("program-data", result[2].content)

    def test_drops_oldest_turns_to_fit_byte_limit(self) -> None:
        result = build_turn_input(
            turn_index=4,
            history=(self.turn(1, 900), self.turn(2, 900), self.turn(3, 100)),
            history_turns=3,
            corpus_window=None,
            max_context_bytes=1200,
        )

        encoded = json.dumps([message.as_dict() for message in result]).encode()
        self.assertNotIn(b"// turn 1", encoded)
        self.assertNotIn(b"// turn 2", encoded)
        self.assertIn(b"// turn 3", encoded)

    def test_corpus_is_delimited_but_never_loaded_from_a_path(self) -> None:
        result = build_turn_input(
            turn_index=1,
            history=(),
            history_turns=4,
            corpus_window=b"// public PoC example",
            max_context_bytes=4096,
        )

        self.assertIn("<historical-poc-corpus-data>", result[0].content)
        self.assertIn("// public PoC example", result[0].content)

    def test_rejects_oversized_corpus_instead_of_silently_truncating(self) -> None:
        with self.assertRaisesRegex(SessionContextError, "corpus"):
            build_turn_input(
                turn_index=1,
                history=(),
                history_turns=0,
                corpus_window=b"x" * 4096,
                max_context_bytes=1024,
            )


if __name__ == "__main__":
    unittest.main()
