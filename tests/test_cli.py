from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from fuzzynth.cli import main


class OfflineCliTests(unittest.TestCase):
    def invoke(self, arguments: list[str]):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(arguments)
        return exit_code, json.loads(output.getvalue())

    def test_workers_reports_matrix_without_credentials(self) -> None:
        exit_code, document = self.invoke(["workers", "--seed", "7"])

        self.assertEqual(exit_code, 0)
        self.assertFalse(document["dataset_enabled"])
        self.assertEqual(
            sum(worker["enabled"] for worker in document["workers"]),
            3,
        )

    def test_budget_status_starts_at_only_configured_safety_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            exit_code, document = self.invoke(
                ["budget-status", "--state-root", temporary]
            )

        self.assertEqual(exit_code, 0)
        by_id = {meter["meter_id"]: meter for meter in document["meters"]}
        self.assertEqual(by_id["luna_alternate"]["total_microunits"], 1_000_000)
        self.assertEqual(by_id["luna_official"]["hard_total_microunits"], 4_900_000)

    def test_session_status_is_empty_for_new_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            exit_code, document = self.invoke(
                ["session-status", "--state-root", temporary]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(document, {"sessions": []})

    def test_control_status_defaults_all_workers_to_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            exit_code, document = self.invoke(
                ["control-status", "--state-root", temporary]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(document["global_state"], "running")
        self.assertEqual(len(document["workers"]), 5)
        self.assertTrue(
            all(
                worker["effective_state"] == "running"
                for worker in document["workers"].values()
            )
        )

    def test_telegram_control_requires_explicit_live_switch(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            exit_code = main(["telegram-control", "--once"])

        self.assertEqual(exit_code, 2)
        self.assertIn("requires --live", error.getvalue())

    def test_campaign_run_requires_explicit_live_switch(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            exit_code = main(
                ["campaign-run", "--corpus-file", "does-not-need-to-exist.js"]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("requires --live", error.getvalue())


if __name__ == "__main__":
    unittest.main()
