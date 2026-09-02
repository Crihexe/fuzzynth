#!/usr/bin/env python3
"""Toggle the managed Luna fallback without overriding owner/provider control."""

from __future__ import annotations

from pathlib import Path

from fuzzynth.control import ControlLedger, is_supervisor_provider_pause


STATE_ROOT = Path("/root/fuzzynth/state")
SPARK_WORKER = "spark-custom-iterative-js"
FALLBACK_WORKER = "luna-custom-none-spark-fallback-js"
MANAGER_SOURCE = "spark-fallback"


def main() -> int:
    with ControlLedger(STATE_ROOT / "control.sqlite3") as control:
        spark_change = control.latest_change(SPARK_WORKER)
        fallback_change = control.latest_change(FALLBACK_WORKER)
        managed = fallback_change is None or fallback_change.source == MANAGER_SOURCE
        if not managed:
            print("spark_fallback=skipped reason=owner_or_provider_override")
            return 0

        spark_provider_paused = (
            control.global_state() == "running"
            and control.worker_state(SPARK_WORKER) == "paused"
            and is_supervisor_provider_pause(spark_change)
        )
        desired = "running" if spark_provider_paused else "paused"
        current = control.worker_state(FALLBACK_WORKER)
        if fallback_change is not None and current == desired:
            print(f"spark_fallback=unchanged state={current}")
            return 0

        spark_request = spark_change.request_id if spark_change is not None else "none"
        control.set_worker(
            FALLBACK_WORKER,
            desired,
            request_id=f"fallback:{spark_request}:{desired}",
            source=MANAGER_SOURCE,
            actor="spark-fallback-reconciler",
            command=f"set fallback {desired} for Spark availability",
        )
        print(f"spark_fallback=changed previous={current} state={desired}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
