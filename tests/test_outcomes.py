from __future__ import annotations

import signal
import unittest

from fuzzynth.outcomes import (
    OutcomeKind,
    ProcessObservation,
    classify,
    diagnose_harness_misuse,
)


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

    def test_detects_invalid_percent_gc_contract_misuse(self) -> None:
        diagnostic = diagnose_harness_misuse(
            b"function f(){ %GC(); }",
            b"Check failed: EnsureCompiledAndFeedbackVector(isolate, function)",
        )

        self.assertEqual(diagnostic.code, "invalid_percent_gc_intrinsic")
        self.assertIn("gc()", diagnostic.guidance)

    def test_detects_distinct_inline_optimization_targets(self) -> None:
        diagnostic = diagnose_harness_misuse(
            b"%PrepareFunctionForOptimization(() => 1);\n"
            b"%OptimizeFunctionOnNextCall(() => 1);",
            b"Check failed: CheckMarkedForManualOptimization(isolate, function)",
        )

        self.assertEqual(diagnostic.code, "fresh_function_optimization_target")

    def test_does_not_downgrade_same_named_function_without_specific_pattern(self) -> None:
        diagnostic = diagnose_harness_misuse(
            b"function f(){}\n%PrepareFunctionForOptimization(f);\n"
            b"%OptimizeFunctionOnNextCall(f);",
            b"Check failed: CheckMarkedForManualOptimization(isolate, function)",
        )

        self.assertIsNone(diagnostic)

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

    def test_ubsan_stderr_is_a_candidate_but_stdout_cannot_forge_one(self) -> None:
        native = classify(
            ProcessObservation(
                exit_code=1,
                stderr=b"engine.cc:7: runtime error: member access within null pointer",
            )
        )
        printed = classify(
            ProcessObservation(exit_code=0, stdout=b"runtime error: pretend")
        )

        self.assertEqual(native.kind, OutcomeKind.SANITIZER)
        self.assertTrue(native.bug_candidate)
        self.assertEqual(printed.kind, OutcomeKind.OK)
        self.assertFalse(printed.bug_candidate)

    def test_tsan_stderr_is_a_candidate_but_stdout_cannot_forge_one(self) -> None:
        native = classify(
            ProcessObservation(
                exit_code=66,
                stderr=b"WARNING: ThreadSanitizer: data race",
            )
        )
        printed = classify(
            ProcessObservation(exit_code=0, stdout=b"WARNING: ThreadSanitizer")
        )

        self.assertEqual(native.kind, OutcomeKind.SANITIZER)
        self.assertTrue(native.bug_candidate)
        self.assertIn("tsan", native.markers)
        self.assertEqual(printed.kind, OutcomeKind.OK)
        self.assertFalse(printed.bug_candidate)

    def test_oom_and_timeout_are_not_native_crashes(self) -> None:
        oom = classify(ProcessObservation(exit_code=137, oom_killed=True))
        timeout = classify(ProcessObservation(exit_code=None, timed_out=True))

        self.assertEqual(oom.kind, OutcomeKind.OOM)
        self.assertEqual(timeout.kind, OutcomeKind.TIMEOUT)
        self.assertFalse(oom.bug_candidate)
        self.assertFalse(timeout.bug_candidate)

    def test_output_limit_is_not_a_native_crash(self) -> None:
        outcome = classify(
            ProcessObservation(
                exit_code=137,
                stdout=b"x" * 10,
                output_truncated=True,
            )
        )

        self.assertEqual(outcome.kind, OutcomeKind.OUTPUT_LIMIT)
        self.assertFalse(outcome.bug_candidate)

    def test_negative_exit_code_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "signal"):
            ProcessObservation(exit_code=-11)


if __name__ == "__main__":
    unittest.main()
