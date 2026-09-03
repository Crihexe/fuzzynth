from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.campaign_config import (
    CampaignConfigurationError,
    choose_session_plan,
    load_campaign_configuration,
)


class CampaignConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_campaign_configuration(
            Path("config/campaign-workers.toml")
        )

    def test_enables_only_custom_adaptive_lanes(self) -> None:
        enabled = {worker.worker_id for worker in self.config.enabled_workers()}
        self.assertEqual(
            enabled,
            {
                "luna-custom-low-compiler-optdebug-explicit-v3",
                "luna-custom-low-memory-asan-explicit-v3",
                "luna-custom-low-security-asan-explicit-v3",
                "terra-custom-high-security-asan-explicit-v3",
                "terra-custom-high-wasm-builder-advanced-asan-v1",
                "luna-custom-low-wasm-staging-builder-asan-v1",
                "luna-custom-low-wasm-builder-asan-v2",
                "luna-custom-low-wasm-builder-advanced-asan-v1",
                "luna-custom-low-concurrency-message-asan-v2",
                "luna-custom-low-buffers-asan-focused-v1",
                "luna-custom-none-regexp-asan-focused-v1",
                "luna-custom-none-language-asan-focused-v1",
            },
        )
        self.assertTrue(
            all(
                worker.provider == "alternate"
                for worker in self.config.enabled_workers()
            )
        )

    def test_custom_workers_omit_temperature(self) -> None:
        for worker in self.config.enabled_workers():
            if worker.provider == "alternate":
                self.assertEqual(worker.temperatures, ())

    def test_wasm_builder_lane_mounts_only_the_pinned_helper(self) -> None:
        worker = self.config.workers["luna-custom-low-wasm-builder-asan-v2"]
        self.assertEqual(worker.support_files, ("wasm_module_builder",))
        self.assertEqual(worker.corpus_strategy, "focus_wasm_builder")

    def test_every_worker_executes_d8_in_fuzzing_mode(self) -> None:
        for worker in self.config.workers.values():
            self.assertIn("--fuzzing", worker.d8_flags)

    def test_active_specialized_workers_are_independent_lanes(self) -> None:
        groups = {}
        for worker in self.config.enabled_workers():
            groups.setdefault(worker.corpus_pair_id, []).append(worker)
        pairs = [workers for workers in groups.values() if len(workers) == 2]
        singles = [workers[0] for workers in groups.values() if len(workers) == 1]
        self.assertEqual(pairs, [])
        self.assertEqual(
            {worker.worker_id for worker in singles},
            {
                "terra-custom-high-security-asan-explicit-v3",
                "terra-custom-high-wasm-builder-advanced-asan-v1",
                "luna-custom-low-compiler-optdebug-explicit-v3",
                "luna-custom-low-memory-asan-explicit-v3",
                "luna-custom-low-security-asan-explicit-v3",
                "luna-custom-low-wasm-staging-builder-asan-v1",
                "luna-custom-low-wasm-builder-asan-v2",
                "luna-custom-low-wasm-builder-advanced-asan-v1",
                "luna-custom-low-concurrency-message-asan-v2",
                "luna-custom-low-buffers-asan-focused-v1",
                "luna-custom-none-regexp-asan-focused-v1",
                "luna-custom-none-language-asan-focused-v1",
            },
        )

    def test_interaction_experiment_is_one_turn_and_focused(self) -> None:
        workers = [
            worker
            for worker in self.config.workers.values()
            if worker.prompt_variant == "interaction_v1"
        ]
        self.assertEqual(len(workers), 5)
        self.assertTrue(all(not worker.enabled for worker in workers))
        self.assertTrue(
            all(
                (worker.min_turns_per_session, worker.max_turns_per_session)
                == (1, 1)
                for worker in workers
            )
        )
        self.assertEqual(
            {worker.corpus_strategy for worker in workers},
            {
                "focus_compiler",
                "focus_wasm",
                "focus_memory",
                "focus_security",
                "focus_concurrency",
            },
        )

    def test_bounded_explicit_comparison_is_retained_but_disabled(self) -> None:
        experiment = [
            self.config.workers["luna-custom-low-current-rich-8ctx-js"],
            self.config.workers["luna-custom-low-explicit-v1-8ctx-js"],
        ]
        self.assertTrue(all(not worker.enabled for worker in experiment))
        self.assertEqual(
            {worker.corpus_pair_id for worker in experiment},
            {"luna-custom-low-rich-vs-explicit-v1-8ctx"},
        )
        self.assertEqual(
            {worker.prompt_variant for worker in experiment},
            {"current_rich", "explicit_v1"},
        )

    def test_configuration_rejects_worker_without_fuzzing_mode(self) -> None:
        source = Path("config/campaign-workers.toml").read_text(encoding="utf-8")
        unsafe = source.replace(
            '["--allow-natives-syntax", "--expose-gc", "--fuzzing"]',
            '["--allow-natives-syntax", "--expose-gc"]',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign-workers.toml"
            path.write_text(unsafe, encoding="utf-8")
            with self.assertRaisesRegex(
                CampaignConfigurationError, "must execute d8 with --fuzzing"
            ):
                load_campaign_configuration(path)

    def test_spark_requests_none_and_high_verbosity(self) -> None:
        worker = self.config.workers["spark-custom-iterative-js-rich"]
        self.assertEqual(worker.reasoning_efforts, ("none",))
        self.assertEqual(worker.verbosity, "high")
        self.assertEqual((worker.min_turns_per_session, worker.max_turns_per_session), (8, 16))

    def test_custom_luna_streaming_effort_lanes_are_enabled(self) -> None:
        worker = self.config.workers[
            "luna-custom-low-compiler-optdebug-explicit-v3"
        ]
        self.assertEqual(worker.reasoning_efforts, ("low",))
        self.assertEqual(worker.max_output_tokens, 2048)
        self.assertEqual(worker.reservation_output_tokens, 128_000)
        no_reasoning = self.config.workers[
            "luna-custom-none-language-asan-focused-v1"
        ]
        self.assertTrue(no_reasoning.enabled)
        self.assertEqual(no_reasoning.reasoning_efforts, ("none",))
        self.assertFalse(self.config.workers["luna-custom-xhigh-iterative-js"].enabled)

    def test_spark_fallback_requests_none_with_high_verbosity(self) -> None:
        worker = self.config.workers[
            "luna-custom-none-spark-fallback-js-rich"
        ]
        self.assertEqual(worker.reasoning_efforts, ("none",))
        self.assertEqual(worker.verbosity, "high")
        self.assertEqual(worker.max_output_tokens, 2048)
        self.assertEqual(worker.reservation_output_tokens, 128_000)

    def test_official_workers_rotate_full_temperature_range_without_reasoning(self) -> None:
        mini = self.config.workers["gpt-4o-mini-official-temperature-js-rich"]
        nano = self.config.workers["gpt-4.1-nano-official-temperature-js-rich"]
        expected_temperatures = (0.0, 0.5, 1.0, 1.5, 2.0)
        self.assertEqual(mini.temperatures, expected_temperatures)
        self.assertEqual(nano.temperatures, expected_temperatures)
        self.assertFalse(mini.send_reasoning)
        self.assertFalse(mini.send_verbosity)
        self.assertEqual((mini.min_turns_per_session, mini.max_turns_per_session), (1, 3))
        self.assertFalse(nano.send_reasoning)
        self.assertFalse(nano.send_verbosity)
        self.assertEqual((nano.min_turns_per_session, nano.max_turns_per_session), (6, 6))
        self.assertEqual(nano.pricing_profile, "gpt_4_1_nano")
        self.assertFalse(
            self.config.workers["luna-official-high-temperature-none-js"].enabled
        )

    def test_session_choice_is_reproducible_and_in_range(self) -> None:
        worker = self.config.workers[
            "luna-custom-low-compiler-optdebug-explicit-v3"
        ]
        first = choose_session_plan(worker, 12345)
        second = choose_session_plan(worker, 12345)

        self.assertEqual(first, second)
        self.assertGreaterEqual(first.target_turns, 2)
        self.assertLessEqual(first.target_turns, 4)

    def test_focused_corpus_and_sensitive_build_lanes_are_explicit(self) -> None:
        wasm = self.config.workers[
            "luna-custom-low-wasm-staging-builder-asan-v1"
        ]
        memory = self.config.workers[
            "luna-custom-low-memory-asan-explicit-v3"
        ]
        self.assertEqual(wasm.corpus_strategy, "focus_wasm_staging_builder")
        self.assertEqual(memory.corpus_strategy, "focus_memory")
        self.assertEqual(wasm.v8_build_profile, "asan")
        self.assertIn("--jit-fuzzing", wasm.d8_flags)
        self.assertEqual(wasm.prompt_variant, "wasm_staging_v1")
        self.assertEqual(wasm.support_files, ("wasm_module_builder",))
        self.assertIn("--wasm-wasmfx", wasm.d8_flags)
        self.assertIn("--wasm-stringref", wasm.d8_flags)
        self.assertFalse(
            self.config.workers["terra-custom-high-wasm-asan-explicit-v3"].enabled
        )
        self.assertIn("--stress-compaction", memory.d8_flags)
        self.assertEqual(memory.prompt_variant, "gc_lifetime_v1")
        self.assertIn("--minor-ms", memory.d8_flags)
        compiler = self.config.workers[
            "luna-custom-low-compiler-optdebug-explicit-v3"
        ]
        self.assertEqual(compiler.prompt_variant, "compiler_turbolev_v1")
        self.assertIn("--turbolev-future", compiler.d8_flags)
        self.assertEqual(
            compiler.v8_build_profile,
            "optdebug",
        )

    def test_v3_preview_dataset_is_enabled_from_private_extraction(self) -> None:
        self.assertTrue(self.config.context.dataset_enabled)
        self.assertEqual(
            self.config.context.dataset_root,
            ".local/datasets/v8_js_pocs_preview_v3/javascript_corpus",
        )


if __name__ == "__main__":
    unittest.main()
