from __future__ import annotations

import signal
import unittest

from fuzzynth.outcomes import OutcomeKind, ProcessObservation, classify


class OutcomeClassificationTests(unittest.TestCase):
    def test_success_is_not_a_candidate(self) -> None:
        outcome = classify(ProcessObservation(exit_code=0, stdout=b"done"))

        self.assertEqual(outcome.kind, OutcomeKind.OK)
        self.assertFalse(outcome.bug_candidate)

    def test_javascript_exception_is_expected(self) -> None:
        outcome = classify(
            ProcessObservation(exit_code=1, stderr=b"Uncaught TypeError: nope")
        )

        self.assertEqual(outcome.kind, OutcomeKind.JS_EXCEPTION)
        self.assertFalse(outcome.bug_candidate)

    def test_wasm_trap_is_expected(self) -> None:
        outcome = classify(
            ProcessObservation(
                exit_code=1,
                stderr=b"WebAssembly.RuntimeError: unreachable",
            )
        )

        self.assertEqual(outcome.kind, OutcomeKind.WASM_TRAP)
        self.assertFalse(outcome.bug_candidate)

    def test_native_signal_is_candidate(self) -> None:
        outcome = classify(
            ProcessObservation(exit_code=None, signal_number=signal.SIGSEGV)
        )

        self.assertEqual(outcome.kind, OutcomeKind.SIGNAL)
        self.assertTrue(outcome.bug_candidate)
        self.assertEqual(outcome.signal_name, "SIGSEGV")

    def test_v8_check_beats_signal(self) -> None:
        outcome = classify(
            ProcessObservation(
                exit_code=None,
                signal_number=signal.SIGABRT,
                stderr=b"Check failed: value != nullptr.",
            )
        )

        self.assertEqual(outcome.kind, OutcomeKind.V8_FATAL)
        self.assertTrue(outcome.bug_candidate)
        self.assertIn("check_failed", outcome.markers)

    def test_sanitizer_beats_timeout(self) -> None:
        outcome = classify(
            ProcessObservation(
                exit_code=None,
                stderr=b"ERROR: AddressSanitizer: heap-buffer-overflow",
                timed_out=True,
            )
        )

        self.assertEqual(outcome.kind, OutcomeKind.SANITIZER)
        self.assertTrue(outcome.bug_candidate)

    def test_oom_and_timeout_are_not_native_crashes(self) -> None:
        oom = classify(ProcessObservation(exit_code=137, oom_killed=True))
        timeout = classify(ProcessObservation(exit_code=None, timed_out=True))

        self.assertEqual(oom.kind, OutcomeKind.OOM)
        self.assertEqual(timeout.kind, OutcomeKind.TIMEOUT)
        self.assertFalse(oom.bug_candidate)
        self.assertFalse(timeout.bug_candidate)

    def test_negative_exit_code_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "signal"):
            ProcessObservation(exit_code=-11)


if __name__ == "__main__":
    unittest.main()
