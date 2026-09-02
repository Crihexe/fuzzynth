from __future__ import annotations

from pathlib import Path
import unittest

from fuzzynth.campaign_config import (
    choose_session_plan,
    load_campaign_configuration,
)


class CampaignConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_campaign_configuration(
            Path("config/campaign-workers.toml")
        )

    def test_enables_four_lanes_plus_managed_fallback(self) -> None:
        enabled = {worker.worker_id for worker in self.config.enabled_workers()}
        self.assertEqual(
            enabled,
            {
                "spark-custom-iterative-js",
                "luna-custom-high-iterative-js",
                "luna-custom-low-iterative-js",
                "luna-custom-none-spark-fallback-js",
                "luna-official-high-temperature-none-js",
            },
        )

    def test_custom_workers_omit_temperature(self) -> None:
        for worker in self.config.enabled_workers():
            if worker.provider == "alternate":
                self.assertEqual(worker.temperatures, ())

    def test_spark_requests_none_and_high_verbosity(self) -> None:
        worker = self.config.workers["spark-custom-iterative-js"]
        self.assertEqual(worker.reasoning_efforts, ("none",))
        self.assertEqual(worker.verbosity, "high")
        self.assertEqual((worker.min_turns_per_session, worker.max_turns_per_session), (8, 16))

    def test_custom_luna_streaming_effort_lanes_are_enabled(self) -> None:
        worker = self.config.workers["luna-custom-low-iterative-js"]
        self.assertEqual(worker.reasoning_efforts, ("low",))
        self.assertEqual(worker.max_output_tokens, 2048)
        self.assertEqual(worker.reservation_output_tokens, 128_000)
        high = self.config.workers["luna-custom-high-iterative-js"]
        self.assertTrue(high.enabled)
        self.assertEqual(high.reasoning_efforts, ("high",))
        self.assertFalse(self.config.workers["luna-custom-xhigh-iterative-js"].enabled)

    def test_spark_fallback_requests_none_with_high_verbosity(self) -> None:
        worker = self.config.workers["luna-custom-none-spark-fallback-js"]
        self.assertEqual(worker.reasoning_efforts, ("none",))
        self.assertEqual(worker.verbosity, "high")
        self.assertEqual(worker.max_output_tokens, 2048)
        self.assertEqual(worker.reservation_output_tokens, 128_000)

    def test_official_worker_rotates_high_temperature_with_no_reasoning(self) -> None:
        worker = self.config.workers["luna-official-high-temperature-none-js"]
        self.assertEqual(worker.temperatures, (1.2, 1.5, 1.8))
        self.assertEqual(worker.reasoning_efforts, ("none",))
        self.assertFalse(
            self.config.workers["luna-official-high-temperature-js"].enabled
        )

    def test_session_choice_is_reproducible_and_in_range(self) -> None:
        worker = self.config.workers["spark-custom-iterative-js"]
        first = choose_session_plan(worker, 12345)
        second = choose_session_plan(worker, 12345)

        self.assertEqual(first, second)
        self.assertGreaterEqual(first.target_turns, 8)
        self.assertLessEqual(first.target_turns, 16)

    def test_dataset_remains_disabled_during_owner_concurrent_work(self) -> None:
        self.assertFalse(self.config.context.dataset_enabled)
        self.assertEqual(self.config.context.dataset_root, "poc_dataset")


if __name__ == "__main__":
    unittest.main()
