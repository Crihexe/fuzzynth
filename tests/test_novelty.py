from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from fuzzynth.novelty import NoveltyError, SemanticNoveltyLedger


class SemanticNoveltyLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "novelty.sqlite3"

    @staticmethod
    def profile(
        signature: str,
        mechanisms: list[str],
        operations: list[str],
    ) -> dict[str, object]:
        return {
            "signature": signature,
            "mechanisms": mechanisms,
            "operations": operations,
        }

    def test_counts_signatures_and_new_features_per_worker(self) -> None:
        with SemanticNoveltyLedger(self.path) as ledger:
            first = ledger.record_success(
                worker_id="a",
                generation_id="g1",
                semantic_profile=self.profile("1" * 16, ["proxy"], ["get"]),
            )
            repeated = ledger.record_success(
                worker_id="a",
                generation_id="g2",
                semantic_profile=self.profile(
                    "1" * 16, ["proxy", "coercion"], ["get", "valueOf"]
                ),
            )
            other_worker = ledger.record_success(
                worker_id="b",
                generation_id="g3",
                semantic_profile=self.profile("1" * 16, ["proxy"], ["get"]),
            )

        self.assertEqual(first.signature_occurrence, 1)
        self.assertEqual(first.new_mechanisms, ("proxy",))
        self.assertEqual(repeated.signature_occurrence, 2)
        self.assertEqual(repeated.new_mechanisms, ("coercion",))
        self.assertEqual(repeated.new_operations, ("valueOf",))
        self.assertEqual(other_worker.signature_occurrence, 1)

    def test_duplicate_generation_is_idempotent(self) -> None:
        with SemanticNoveltyLedger(self.path) as ledger:
            first = ledger.record_success(
                worker_id="a",
                generation_id="g1",
                semantic_profile=self.profile("2" * 16, ["wasm"], ["instantiate"]),
            )
            duplicate = ledger.record_success(
                worker_id="a",
                generation_id="g1",
                semantic_profile=self.profile("2" * 16, ["wasm"], ["instantiate"]),
            )

        self.assertEqual(first, duplicate)
        with SemanticNoveltyLedger(self.path) as ledger:
            count = ledger.connection.execute(
                "SELECT observation_count FROM semantic_feature_count"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_private_database_and_strict_signature_validation(self) -> None:
        with SemanticNoveltyLedger(self.path) as ledger:
            with self.assertRaises(NoveltyError):
                ledger.record_success(
                    worker_id="a",
                    generation_id="g1",
                    semantic_profile=self.profile("not-a-hash", [], []),
                )

        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
