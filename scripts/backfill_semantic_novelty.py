#!/usr/bin/env python3
"""Populate cross-session semantic novelty from preserved successful programs."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sqlite3

from fuzzynth.novelty import SemanticNoveltyLedger
from fuzzynth.program_observations import semantic_profile


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=Path("state"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    state_root = args.state_root.resolve()
    catalog_path = state_root / "catalog.sqlite3"
    artifacts_root = state_root / "artifacts"
    with sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True) as catalog:
        rows = catalog.execute(
            """
            SELECT g.id, g.campaign_id, g.program_sha256, a.relative_path
            FROM generation g
            JOIN artifact a ON a.sha256 = g.program_sha256
            WHERE g.id IN (
              SELECT DISTINCT generation_id FROM execution
              WHERE generation_id IS NOT NULL AND outcome = 'ok'
            )
            ORDER BY g.finished_at, g.id
            """
        ).fetchall()
    print(f"semantic_novelty_backfill_plan={len(rows)}", flush=True)
    if args.dry_run:
        return 0

    with SemanticNoveltyLedger(
        state_root / "semantic-novelty.sqlite3"
    ) as ledger:
        before = ledger.connection.execute(
            "SELECT count(*) FROM semantic_observation"
        ).fetchone()[0]
        for ordinal, (generation_id, worker_id, digest, relative_path) in enumerate(
            rows,
            start=1,
        ):
            program = (artifacts_root / relative_path).read_bytes()
            if hashlib.sha256(program).hexdigest() != digest:
                raise RuntimeError("program artifact failed SHA-256 validation")
            source = program.decode("utf-8", errors="replace")
            ledger.record_success(
                worker_id=worker_id,
                generation_id=generation_id,
                semantic_profile=semantic_profile(source),
            )
            if ordinal % 1000 == 0:
                print(f"semantic_novelty_backfill_progress={ordinal}", flush=True)
        after = ledger.connection.execute(
            "SELECT count(*) FROM semantic_observation"
        ).fetchone()[0]
    print(
        f"semantic_novelty_backfill_complete={after} added={after - before}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
