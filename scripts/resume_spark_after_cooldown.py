#!/usr/bin/env python3
"""Resume only a supervisor-paused Spark session after its five-hour window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fuzzynth.artifacts import ArtifactStore
from fuzzynth.control import (
    PROVIDER_PAUSE_REASONS,
    ControlLedger,
    is_supervisor_provider_pause,
)
from fuzzynth.sessions import SessionLedger


STATE_ROOT = Path("/root/fuzzynth/state")
WORKER_IDS = (
    "spark-custom-iterative-js-rich",
    "spark-custom-iterative-js-lean",
)
MINIMUM_COOLDOWN = timedelta(hours=5)


def main() -> int:
    with ControlLedger(STATE_ROOT / "control.sqlite3") as control:
        if control.global_state() != "running":
            print("spark_cooldown=skipped reason=global_control")
            return 0
        with SessionLedger(
            STATE_ROOT / "sessions.sqlite3",
            ArtifactStore(STATE_ROOT / "artifacts"),
        ) as sessions:
            for worker_id in WORKER_IDS:
                if control.worker_state(worker_id) != "paused":
                    print(f"spark_cooldown={worker_id} skipped=worker_not_paused")
                    continue
                latest = control.latest_change(worker_id)
                if not is_supervisor_provider_pause(latest):
                    print(
                        f"spark_cooldown={worker_id} "
                        "skipped=owner_or_unknown_pause"
                    )
                    continue
                session = next(
                    (
                        item
                        for item in reversed(sessions.list_sessions())
                        if item.worker_id == worker_id and item.status == "paused"
                    ),
                    None,
                )
                if (
                    session is None
                    or session.pause_reason not in PROVIDER_PAUSE_REASONS
                ):
                    print(
                        f"spark_cooldown={worker_id} "
                        "skipped=no_provider_paused_session"
                    )
                    continue
                updated_at = datetime.fromisoformat(session.updated_at)
                now = datetime.now(timezone.utc)
                if now - updated_at < MINIMUM_COOLDOWN:
                    print(f"spark_cooldown={worker_id} skipped=cooldown_active")
                    continue
                sessions.resume(session.session_id)
                control.set_worker(
                    worker_id,
                    "running",
                    request_id=f"cooldown:{session.session_id}:{session.next_turn}",
                    source="cooldown",
                    actor="spark-cooldown-timer",
                    command="resume after five hour provider cooldown",
                )
                print(
                    f"spark_cooldown={worker_id} resumed={session.session_id} "
                    f"next_turn={session.next_turn}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
