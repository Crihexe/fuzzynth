#!/usr/bin/env python3
"""Resume only a supervisor-paused Spark session after its five-hour window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fuzzynth.artifacts import ArtifactStore
from fuzzynth.control import ControlLedger
from fuzzynth.sessions import SessionLedger


STATE_ROOT = Path("/root/fuzzynth/state")
WORKER_ID = "spark-custom-iterative-js"
MINIMUM_COOLDOWN = timedelta(hours=5)


def main() -> int:
    with ControlLedger(STATE_ROOT / "control.sqlite3") as control:
        if control.global_state() != "running":
            print("spark_cooldown=skipped reason=global_control")
            return 0
        if control.worker_state(WORKER_ID) != "paused":
            print("spark_cooldown=skipped reason=worker_not_paused")
            return 0
        latest = control.latest_change(WORKER_ID)
        if (
            latest is None
            or latest.source != "supervisor"
            or latest.new_state != "paused"
            or latest.command != "pause after provider_error"
        ):
            print("spark_cooldown=skipped reason=owner_or_unknown_pause")
            return 0

        with SessionLedger(
            STATE_ROOT / "sessions.sqlite3",
            ArtifactStore(STATE_ROOT / "artifacts"),
        ) as sessions:
            session = next(
                (
                    item
                    for item in reversed(sessions.list_sessions())
                    if item.worker_id == WORKER_ID and item.status == "paused"
                ),
                None,
            )
            if session is None or session.pause_reason != "provider_error":
                print("spark_cooldown=skipped reason=no_provider_paused_session")
                return 0
            updated_at = datetime.fromisoformat(session.updated_at)
            now = datetime.now(timezone.utc)
            if now - updated_at < MINIMUM_COOLDOWN:
                print("spark_cooldown=skipped reason=cooldown_active")
                return 0
            sessions.resume(session.session_id)

        control.set_worker(
            WORKER_ID,
            "running",
            request_id=f"cooldown:{session.session_id}:{session.next_turn}",
            source="cooldown",
            actor="spark-cooldown-timer",
            command="resume after five hour provider cooldown",
        )
        print(
            f"spark_cooldown=resumed session={session.session_id} "
            f"next_turn={session.next_turn}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
