#!/usr/bin/env python3
"""Toggle the managed Luna fallback without overriding owner/provider control."""

from __future__ import annotations

from pathlib import Path

from fuzzynth.control import ControlLedger, is_supervisor_provider_pause


STATE_ROOT = Path("/root/fuzzynth/state")
WORKER_PAIRS = (
    (
        "spark-custom-iterative-js-rich",
        "luna-custom-none-spark-fallback-js-rich",
    ),
    (
        "spark-custom-iterative-js-lean",
        "luna-custom-none-spark-fallback-js-lean",
    ),
)
MANAGER_SOURCE = "spark-fallback"


def main() -> int:
    with ControlLedger(STATE_ROOT / "control.sqlite3") as control:
        for spark_worker, fallback_worker in WORKER_PAIRS:
            spark_change = control.latest_change(spark_worker)
            fallback_change = control.latest_change(fallback_worker)
            managed = (
                fallback_change is None or fallback_change.source == MANAGER_SOURCE
            )
            if not managed:
                print(
                    f"spark_fallback={fallback_worker} "
                    "skipped=owner_or_provider_override"
                )
                continue

            spark_provider_paused = (
                control.global_state() == "running"
                and control.worker_state(spark_worker) == "paused"
                and is_supervisor_provider_pause(spark_change)
            )
            desired = "running" if spark_provider_paused else "paused"
            current = control.worker_state(fallback_worker)
            if fallback_change is not None and current == desired:
                print(f"spark_fallback={fallback_worker} unchanged={current}")
                continue

            spark_request = (
                spark_change.request_id if spark_change is not None else "none"
            )
            control.set_worker(
                fallback_worker,
                desired,
                request_id=f"fallback:{spark_request}:{desired}",
                source=MANAGER_SOURCE,
                actor="spark-fallback-reconciler",
                command=f"set fallback {desired} for Spark availability",
            )
            print(
                f"spark_fallback={fallback_worker} "
                f"previous={current} state={desired}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
