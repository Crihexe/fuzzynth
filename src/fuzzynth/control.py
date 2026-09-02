"""Durable owner control state enforced at campaign turn boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


class ControlStateError(RuntimeError):
    """A control transition or ledger is invalid."""


GLOBAL_STATES = frozenset({"running", "paused", "stopped"})
WORKER_STATES = frozenset({"running", "paused"})


@dataclass(frozen=True, slots=True)
class ControlChange:
    request_id: str
    target: str
    previous_state: str
    new_state: str
    applied: bool


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    global_state: str
    workers: dict[str, str]

    def effective_state(self, worker_id: str) -> str:
        if self.global_state != "running":
            return self.global_state
        return self.workers.get(worker_id, "running")

    def as_dict(self) -> dict[str, object]:
        return {
            "global_state": self.global_state,
            "workers": {
                worker_id: {
                    "configured_state": state,
                    "effective_state": self.effective_state(worker_id),
                }
                for worker_id, state in sorted(self.workers.items())
            },
        }


_SCHEMA = """
CREATE TABLE setting (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE worker_control (
  worker_id TEXT PRIMARY KEY,
  desired_state TEXT NOT NULL CHECK(desired_state IN ('running', 'paused')),
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE control_audit (
  id INTEGER PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  actor TEXT NOT NULL,
  command TEXT NOT NULL,
  target TEXT NOT NULL,
  previous_state TEXT NOT NULL,
  new_state TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

INSERT INTO setting(key, value, updated_at)
VALUES ('global_state', 'running', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

PRAGMA user_version = 1;
"""


def _validated_identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty bounded string")
    return value


class ControlLedger:
    """SQLite-backed dispatch state shared by the scheduler and control plane."""

    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA busy_timeout = 10000")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self.connection.executescript(_SCHEMA)
        elif version != 1:
            self.connection.close()
            raise ControlStateError("unsupported control ledger version")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ControlLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def global_state(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM setting WHERE key = 'global_state'"
        ).fetchone()
        if row is None or row[0] not in GLOBAL_STATES:
            raise ControlStateError("global control state is missing or invalid")
        return str(row[0])

    def worker_state(self, worker_id: str) -> str:
        _validated_identifier(worker_id, "worker_id")
        row = self.connection.execute(
            "SELECT desired_state FROM worker_control WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        if row is None:
            return "running"
        if row[0] not in WORKER_STATES:
            raise ControlStateError("worker control state is invalid")
        return str(row[0])

    def effective_state(self, worker_id: str) -> str:
        global_state = self.global_state()
        if global_state != "running":
            return global_state
        return self.worker_state(worker_id)

    def dispatch_allowed(self, worker_id: str) -> bool:
        return self.effective_state(worker_id) == "running"

    def snapshot(self, worker_ids: tuple[str, ...] = ()) -> ControlSnapshot:
        configured = {
            str(row[0]): str(row[1])
            for row in self.connection.execute(
                "SELECT worker_id, desired_state FROM worker_control"
            ).fetchall()
        }
        for worker_id in worker_ids:
            _validated_identifier(worker_id, "worker_id")
            configured.setdefault(worker_id, "running")
        return ControlSnapshot(
            global_state=self.global_state(),
            workers=configured,
        )

    def _existing_change(self, request_id: str) -> ControlChange | None:
        row = self.connection.execute(
            """
            SELECT target, previous_state, new_state
            FROM control_audit WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        return ControlChange(
            request_id=request_id,
            target=str(row[0]),
            previous_state=str(row[1]),
            new_state=str(row[2]),
            applied=False,
        )

    def set_global(
        self,
        state: str,
        *,
        request_id: str,
        source: str,
        actor: str,
        command: str,
    ) -> ControlChange:
        if state not in GLOBAL_STATES:
            raise ValueError("invalid global control state")
        return self._set(
            target="global",
            state=state,
            request_id=request_id,
            source=source,
            actor=actor,
            command=command,
        )

    def set_worker(
        self,
        worker_id: str,
        state: str,
        *,
        request_id: str,
        source: str,
        actor: str,
        command: str,
    ) -> ControlChange:
        _validated_identifier(worker_id, "worker_id")
        if state not in WORKER_STATES:
            raise ValueError("invalid worker control state")
        return self._set(
            target=worker_id,
            state=state,
            request_id=request_id,
            source=source,
            actor=actor,
            command=command,
        )

    def _set(
        self,
        *,
        target: str,
        state: str,
        request_id: str,
        source: str,
        actor: str,
        command: str,
    ) -> ControlChange:
        request_id = _validated_identifier(request_id, "request_id")
        source = _validated_identifier(source, "source")
        actor = _validated_identifier(actor, "actor")
        command = _validated_identifier(command, "command")
        target = _validated_identifier(target, "target")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self._existing_change(request_id)
            if existing is not None:
                self.connection.rollback()
                return existing
            previous = (
                self.global_state() if target == "global" else self.worker_state(target)
            )
            now = datetime.now(timezone.utc).isoformat()
            if target == "global":
                self.connection.execute(
                    """
                    UPDATE setting SET value = ?, updated_at = ?
                    WHERE key = 'global_state'
                    """,
                    (state, now),
                )
            else:
                self.connection.execute(
                    """
                    INSERT INTO worker_control(worker_id, desired_state, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(worker_id) DO UPDATE SET
                      desired_state = excluded.desired_state,
                      updated_at = excluded.updated_at
                    """,
                    (target, state, now),
                )
            self.connection.execute(
                """
                INSERT INTO control_audit(
                  request_id, source, actor, command, target,
                  previous_state, new_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (request_id, source, actor, command, target, previous, state, now),
            )
            self.connection.commit()
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise ControlStateError("control transition failed") from exc
        return ControlChange(
            request_id=request_id,
            target=target,
            previous_state=previous,
            new_state=state,
            applied=True,
        )
