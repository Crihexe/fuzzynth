from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest

from fuzzynth.control import ControlLedger


class ControlLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state" / "control.sqlite3"
        self.ledger = ControlLedger(self.path)
        self.addCleanup(self.ledger.close)

    def change_global(self, state: str, request_id: str = "request-1"):
        return self.ledger.set_global(
            state,
            request_id=request_id,
            source="test",
            actor="owner",
            command=f"set global {state}",
        )

    def change_worker(self, state: str, request_id: str = "request-2"):
        return self.ledger.set_worker(
            "spark-custom-iterative-js",
            state,
            request_id=request_id,
            source="test",
            actor="owner",
            command=f"set worker {state}",
        )

    def test_defaults_to_running_with_private_database(self) -> None:
        self.assertEqual(self.ledger.global_state(), "running")
        self.assertTrue(self.ledger.dispatch_allowed("unknown-worker"))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_global_state_overrides_worker_state(self) -> None:
        self.change_worker("running")
        self.change_global("paused")

        self.assertEqual(
            self.ledger.effective_state("spark-custom-iterative-js"),
            "paused",
        )

    def test_worker_pause_is_independent(self) -> None:
        self.change_worker("paused")

        snapshot = self.ledger.snapshot(
            ("spark-custom-iterative-js", "luna-custom-xhigh-iterative-js")
        )
        self.assertEqual(
            snapshot.effective_state("spark-custom-iterative-js"), "paused"
        )
        self.assertEqual(
            snapshot.effective_state("luna-custom-xhigh-iterative-js"), "running"
        )

    def test_request_id_makes_mutation_idempotent(self) -> None:
        first = self.change_global("paused")
        second = self.change_global("stopped")

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(second.new_state, "paused")
        self.assertEqual(self.ledger.global_state(), "paused")

    def test_state_persists_across_connections(self) -> None:
        self.change_global("stopped")

        with ControlLedger(self.path) as second:
            self.assertEqual(second.global_state(), "stopped")

    def test_rejects_invalid_states(self) -> None:
        with self.assertRaises(ValueError):
            self.change_global("invalid")
        with self.assertRaises(ValueError):
            self.change_worker("stopped")

    def test_telegram_offset_is_persistent_and_monotonic(self) -> None:
        self.assertEqual(self.ledger.telegram_offset(), 0)
        self.assertEqual(self.ledger.advance_telegram_offset(42), 42)
        self.assertEqual(self.ledger.advance_telegram_offset(30), 42)

        with ControlLedger(self.path) as second:
            self.assertEqual(second.telegram_offset(), 42)


if __name__ == "__main__":
    unittest.main()
