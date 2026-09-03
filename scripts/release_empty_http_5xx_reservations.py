#!/usr/bin/env python3
"""Release old uncertain reservations proven to be empty HTTP 5xx rejects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=Path("state"))
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    state_root = args.state_root.resolve()
    catalog_uri = f"file:{state_root / 'catalog.sqlite3'}?mode=ro"
    with sqlite3.connect(catalog_uri, uri=True) as catalog:
        generations = catalog.execute(
            """
            SELECT id, requested_parameters_json, effective_parameters_json
            FROM generation
            WHERE status = 'failed' AND program_sha256 IS NULL
            """
        ).fetchall()

    eligible: dict[str, str] = {}
    for generation_id, requested_json, effective_json in generations:
        requested = json.loads(requested_json)
        effective = json.loads(effective_json)
        reservation_id = requested.get("budget_reservation_id")
        if (
            isinstance(reservation_id, str)
            and effective.get("error_code") == "http_error"
            and isinstance(effective.get("http_status"), int)
            and 500 <= effective["http_status"] <= 599
            and effective.get("partial_output_bytes", 0) == 0
        ):
            eligible[reservation_id] = generation_id

    budget_path = state_root / "budgets.sqlite3"
    released_by_meter: dict[str, dict[str, int]] = {}
    released = 0
    with sqlite3.connect(budget_path) as budgets:
        for reservation_id, _generation_id in eligible.items():
            row = budgets.execute(
                """
                SELECT meter_id, status, reserved_uncached_input_tokens,
                       reserved_cached_input_tokens, reserved_output_tokens,
                       reserved_microunits
                FROM reservation WHERE id = ?
                """,
                (reservation_id,),
            ).fetchone()
            if row is None or row[1] != "uncertain":
                continue
            meter = released_by_meter.setdefault(
                row[0],
                {
                    "reservations": 0,
                    "uncached_input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "microunits": 0,
                },
            )
            meter["reservations"] += 1
            meter["uncached_input_tokens"] += row[2]
            meter["cached_input_tokens"] += row[3]
            meter["output_tokens"] += row[4]
            meter["microunits"] += row[5]
            if args.apply:
                cursor = budgets.execute(
                    """
                    UPDATE reservation SET status = 'released'
                    WHERE id = ? AND status = 'uncertain'
                    """,
                    (reservation_id,),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("reservation changed during recovery")
            released += 1
        if args.apply:
            budgets.commit()

    print(
        json.dumps(
            {
                "applied": args.apply,
                "eligible_uncertain_reservations": released,
                "released_by_meter": released_by_meter,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
