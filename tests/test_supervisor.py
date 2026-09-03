import unittest
import json
from unittest.mock import patch

from pathlib import Path

from fuzzynth.campaign_config import load_campaign_configuration
from fuzzynth.session_context import TurnContext
from fuzzynth.supervisor import (
    CampaignSupervisor,
    _stable_seed,
    adaptive_session_reset_reason,
    transient_provider_retry_delay,
)


class SupervisorSeedTests(unittest.TestCase):
    def test_stable_seed_always_fits_sqlite_signed_integer(self):
        seed = _stable_seed(
            20260902,
            "luna-official-high-temperature-js",
            1,
        )

        self.assertEqual(seed, 787_664_373_707_780_112)
        self.assertGreaterEqual(seed, 0)
        self.assertLessEqual(seed, (1 << 63) - 1)

    def test_stable_seed_is_reproducible_and_varies_by_ordinal(self):
        first = _stable_seed(7, "worker", 1)

        self.assertEqual(first, _stable_seed(7, "worker", 1))
        self.assertNotEqual(first, _stable_seed(7, "worker", 2))

    def test_prompt_variants_receive_identical_pair_seed(self):
        configuration = load_campaign_configuration(
            Path("config/campaign-workers.toml")
        )
        rich = configuration.workers[
            "luna-custom-low-compiler-optdebug-explicit-v3"
        ]
        lean = configuration.workers[
            "luna-custom-low-compiler-optdebug-lean-v3"
        ]

        self.assertEqual(rich.corpus_pair_id, lean.corpus_pair_id)
        self.assertEqual(
            _stable_seed(20260902, rich.corpus_pair_id, 17),
            _stable_seed(20260902, lean.corpus_pair_id, 17),
        )


class AdaptiveSessionTests(unittest.TestCase):
    @staticmethod
    def turn(index: int, program: bytes, **feedback) -> TurnContext:
        document = {
            "timed_out": False,
            "oom_killed": False,
            "stdout_tail": "",
            "stderr_tail": "",
            **feedback,
        }
        return TurnContext(index, program, json.dumps(document).encode())

    def test_rotates_immediately_after_timeout(self):
        history = (self.turn(1, b"while(1){}", timed_out=True),)
        self.assertEqual(adaptive_session_reset_reason(history), "timeout")

    def test_rotates_after_exact_duplicate(self):
        history = (self.turn(1, b"print(1)"), self.turn(2, b"print(1)"))
        self.assertEqual(
            adaptive_session_reset_reason(history),
            "duplicate_program",
        )

    def test_rotates_after_two_wasm_compile_errors(self):
        failure = "WebAssembly.CompileError: validation failed"
        history = (
            self.turn(1, b"a", stderr_tail=failure),
            self.turn(2, b"b", stderr_tail=failure),
        )
        self.assertEqual(
            adaptive_session_reset_reason(history),
            "repeated_wasm_compile_error",
        )

    def test_rotates_after_two_globally_repeated_semantic_paths(self):
        repeated = {
            "program_observation": {
                "semantic_novelty": {"repeated_globally": True}
            }
        }
        history = (
            self.turn(1, b"print(1)", **repeated),
            self.turn(2, b"print(2)", **repeated),
        )

        self.assertEqual(
            adaptive_session_reset_reason(history),
            "repeated_global_semantic_path",
        )

    def test_keeps_session_after_one_repeated_and_one_novel_path(self):
        repeated = {
            "program_observation": {
                "semantic_novelty": {"repeated_globally": True}
            }
        }
        novel = {
            "program_observation": {
                "semantic_novelty": {"repeated_globally": False}
            }
        }
        history = (
            self.turn(1, b"print(1)", **repeated),
            self.turn(2, b"print(2)", **novel),
        )

        self.assertIsNone(adaptive_session_reset_reason(history))

    def test_keeps_session_after_single_recoverable_error(self):
        history = (self.turn(1, b"a", stderr_tail="ReferenceError: x"),)
        self.assertIsNone(adaptive_session_reset_reason(history))


class ProviderRetryTests(unittest.TestCase):
    def test_generic_provider_errors_receive_bounded_backoff(self):
        self.assertEqual(transient_provider_retry_delay("provider_error", 0), 5.0)
        self.assertEqual(transient_provider_retry_delay("provider_error", 1), 15.0)
        self.assertEqual(transient_provider_retry_delay("provider_error", 2), 60.0)
        self.assertEqual(transient_provider_retry_delay("provider_error", 3), 300.0)
        self.assertEqual(transient_provider_retry_delay("provider_error", 99), 300.0)

    def test_quota_and_other_pauses_are_never_retried(self):
        self.assertIsNone(
            transient_provider_retry_delay("provider_quota_or_rate_limit", 0)
        )
        self.assertIsNone(transient_provider_retry_delay("unknown_usage", 0))


class SupervisorShutdownTests(unittest.TestCase):
    def test_inflight_error_during_shutdown_does_not_pause_worker(self):
        supervisor = CampaignSupervisor(
            repo_root=Path("."),
            state_root=Path("state"),
            credentials=object(),
            corpus=object(),
            worker_ids=("worker",),
        )

        def fail_during_shutdown(*_args, **_kwargs):
            supervisor.stop()
            raise RuntimeError("executor disappeared during shutdown")

        with patch.object(supervisor, "_run_worker", side_effect=fail_during_shutdown):
            summary = supervisor._guarded_worker(
                "worker",
                max_turns=None,
                max_sessions=None,
                exit_when_blocked=False,
            )

        self.assertEqual(summary.final_state, "stopped")
        self.assertEqual(summary.reason, "supervisor_stopped")


if __name__ == "__main__":
    unittest.main()
