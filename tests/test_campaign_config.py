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

    def test_enables_custom_lanes_and_two_official_baselines(self) -> None:
        enabled = {worker.worker_id for worker in self.config.enabled_workers()}
        self.assertEqual(
            enabled,
            {
                "spark-custom-iterative-js-rich",
                "spark-custom-iterative-js-lean",
                "luna-custom-high-iterative-js-rich",
                "luna-custom-high-iterative-js-lean",
                "luna-custom-low-iterative-js-rich",
                "luna-custom-low-iterative-js-lean",
                "luna-custom-low-current-rich-8ctx-js",
                "luna-custom-low-explicit-v1-8ctx-js",
                "luna-custom-none-spark-fallback-js-rich",
                "luna-custom-none-spark-fallback-js-lean",
                "gpt-4o-mini-official-temperature-js-rich",
                "gpt-4o-mini-official-temperature-js-lean",
                "gpt-4.1-nano-official-temperature-js-rich",
                "gpt-4.1-nano-official-temperature-js-lean",
            },
        )

    def test_custom_workers_omit_temperature(self) -> None:
        for worker in self.config.enabled_workers():
            if worker.provider == "alternate":
                self.assertEqual(worker.temperatures, ())

    def test_every_worker_executes_d8_in_fuzzing_mode(self) -> None:
        for worker in self.config.workers.values():
            self.assertIn("--fuzzing", worker.d8_flags)

    def test_enabled_workers_are_exact_two_variant_pairs(self) -> None:
        pairs = {}
        for worker in self.config.enabled_workers():
            pairs.setdefault(worker.corpus_pair_id, []).append(worker)
        self.assertEqual(len(pairs), 7)
        for variants in pairs.values():
            self.assertEqual(len({worker.prompt_variant for worker in variants}), 2)
            self.assertEqual(len({worker.model for worker in variants}), 1)
        experiment = pairs["luna-custom-low-rich-vs-explicit-v1-8ctx"]
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
        worker = self.config.workers["luna-custom-low-iterative-js-rich"]
        self.assertEqual(worker.reasoning_efforts, ("low",))
        self.assertEqual(worker.max_output_tokens, 2048)
        self.assertEqual(worker.reservation_output_tokens, 128_000)
        high = self.config.workers["luna-custom-high-iterative-js-rich"]
        self.assertTrue(high.enabled)
        self.assertEqual(high.reasoning_efforts, ("high",))
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
        worker = self.config.workers["spark-custom-iterative-js-rich"]
        first = choose_session_plan(worker, 12345)
        second = choose_session_plan(worker, 12345)

        self.assertEqual(first, second)
        self.assertGreaterEqual(first.target_turns, 8)
        self.assertLessEqual(first.target_turns, 16)

    def test_v3_preview_dataset_is_enabled_from_private_extraction(self) -> None:
        self.assertTrue(self.config.context.dataset_enabled)
        self.assertEqual(
            self.config.context.dataset_root,
            ".local/datasets/v8_js_pocs_preview_v3/javascript_corpus",
        )


if __name__ == "__main__":
    unittest.main()
