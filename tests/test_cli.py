from __future__ import annotations

from contextlib import redirect_stdout
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


if __name__ == "__main__":
    unittest.main()
