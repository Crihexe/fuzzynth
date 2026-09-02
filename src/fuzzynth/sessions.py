"""Durable iterative campaign session and turn-history state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid

from fuzzynth.artifacts import ArtifactRef, ArtifactStore
from fuzzynth.campaign_config import CampaignWorker, SessionPlan
from fuzzynth.campaign_turn import TurnResult
from fuzzynth.session_context import TurnContext


class SessionStateError(RuntimeError):
    """A durable session transition would violate its state machine."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    worker_id: str
    seed: int
    target_turns: int
    reasoning_effort: str
    temperature: float | None
    status: str
    next_turn: int
    pause_reason: str | None
    corpus: ArtifactRef | None
    created_at: str
    updated_at: str


_SCHEMA = """
CREATE TABLE session (
  id TEXT PRIMARY KEY,
  worker_id TEXT NOT NULL,
  seed INTEGER NOT NULL,
  target_turns INTEGER NOT NULL CHECK(target_turns >= 1),
  reasoning_effort TEXT NOT NULL,
  temperature REAL,
  status TEXT NOT NULL CHECK(status IN ('active', 'paused', 'completed', 'crash')),
  next_turn INTEGER NOT NULL CHECK(next_turn >= 1),
  pause_reason TEXT,
  corpus_sha256 TEXT,
  corpus_size INTEGER CHECK(corpus_size >= 0),
  corpus_relative_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK((corpus_sha256 IS NULL) = (corpus_size IS NULL)),
  CHECK((corpus_sha256 IS NULL) = (corpus_relative_path IS NULL))
) STRICT;

CREATE UNIQUE INDEX one_open_session_per_worker
ON session(worker_id) WHERE status IN ('active', 'paused');

CREATE TABLE attempt (
  session_id TEXT NOT NULL REFERENCES session(id),
  turn_index INTEGER NOT NULL CHECK(turn_index >= 1),
  generation_id TEXT NOT NULL,
  execution_id TEXT,
  program_sha256 TEXT,
  program_size INTEGER CHECK(program_size >= 0),
  program_relative_path TEXT,
  feedback_sha256 TEXT,
  feedback_size INTEGER CHECK(feedback_size >= 0),
  feedback_relative_path TEXT,
  response_status TEXT,
  pause_reason TEXT,
  stop_reason TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(session_id, turn_index),
  CHECK((program_sha256 IS NULL) = (program_size IS NULL)),
  CHECK((program_sha256 IS NULL) = (program_relative_path IS NULL)),
  CHECK((feedback_sha256 IS NULL) = (feedback_size IS NULL)),
  CHECK((feedback_sha256 IS NULL) = (feedback_relative_path IS NULL))
) STRICT;

CREATE INDEX attempt_generation_idx ON attempt(generation_id);
PRAGMA user_version = 1;
"""


class SessionLedger:
    def __init__(self, path: Path, store: ArtifactStore):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        self.store = store
        self.connection = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA busy_timeout = 10000")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self.connection.executescript(_SCHEMA)
        elif version != 1:
            self.connection.close()
            raise SessionStateError("unsupported session ledger version")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SessionLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start(
        self,
        worker: CampaignWorker,
        plan: SessionPlan,
        *,
        corpus_window: bytes | None,
    ) -> SessionRecord:
        corpus = self.store.put(corpus_window) if corpus_window else None
        now = datetime.now(timezone.utc).isoformat()
        session_id = f"session-{uuid.uuid4()}"
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO session VALUES (
                      ?, ?, ?, ?, ?, ?, 'active', 1, NULL, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        session_id,
                        worker.worker_id,
                        plan.seed,
                        plan.target_turns,
                        plan.reasoning_effort,
                        plan.temperature,
                        corpus.sha256 if corpus else None,
                        corpus.size if corpus else None,
                        corpus.relative_path if corpus else None,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SessionStateError(
                f"worker already has an open session: {worker.worker_id}"
            ) from exc
        return self.get(session_id)

    @staticmethod
    def _artifact_from_row(
        sha256: str | None,
        size: int | None,
        relative_path: str | None,
    ) -> ArtifactRef | None:
        if sha256 is None:
            return None
        if size is None or relative_path is None:
            raise SessionStateError("session has incomplete artifact metadata")
        return ArtifactRef(sha256=sha256, size=size, relative_path=relative_path)

    def get(self, session_id: str) -> SessionRecord:
        row = self.connection.execute(
            """
            SELECT id, worker_id, seed, target_turns, reasoning_effort,
                   temperature, status, next_turn, pause_reason, corpus_sha256,
                   corpus_size, corpus_relative_path, created_at, updated_at
            FROM session WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise SessionStateError("unknown session")
        return SessionRecord(
            session_id=row[0],
            worker_id=row[1],
            seed=row[2],
            target_turns=row[3],
            reasoning_effort=row[4],
            temperature=row[5],
            status=row[6],
            next_turn=row[7],
            pause_reason=row[8],
            corpus=self._artifact_from_row(row[9], row[10], row[11]),
            created_at=row[12],
            updated_at=row[13],
        )

    def corpus_bytes(self, session_id: str) -> bytes | None:
        corpus = self.get(session_id).corpus
        return self.store.read(corpus) if corpus else None

    def record_turn(self, session_id: str, result: TurnResult) -> SessionRecord:
        session = self.get(session_id)
        if session.status != "active":
            raise SessionStateError("only an active session can record a turn")
        program = self.store.put(result.program) if result.program is not None else None
        feedback = (
            self.store.put(result.feedback) if result.feedback is not None else None
        )
        next_turn = session.next_turn + 1
        if result.stop_reason == "bug_candidate":
            status = "crash"
            reason = "bug_candidate"
        elif result.pause_reason:
            status = "paused"
            reason = result.pause_reason
        elif session.next_turn >= session.target_turns:
            status = "completed"
            reason = None
        else:
            status = "active"
            reason = None
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO attempt VALUES (
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        session_id,
                        session.next_turn,
                        result.generation_id,
                        result.execution.execution_id if result.execution else None,
                        program.sha256 if program else None,
                        program.size if program else None,
                        program.relative_path if program else None,
                        feedback.sha256 if feedback else None,
                        feedback.size if feedback else None,
                        feedback.relative_path if feedback else None,
                        result.response_status,
                        result.pause_reason,
                        result.stop_reason,
                        now,
                    ),
                )
                cursor = self.connection.execute(
                    """
                    UPDATE session SET status = ?, next_turn = ?, pause_reason = ?,
                                       updated_at = ?
                    WHERE id = ? AND status = 'active' AND next_turn = ?
                    """,
                    (
                        status,
                        next_turn,
                        reason,
                        now,
                        session_id,
                        session.next_turn,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SessionStateError("concurrent session transition detected")
        except sqlite3.IntegrityError as exc:
            raise SessionStateError("turn already exists or is invalid") from exc
        return self.get(session_id)

    def history(self, session_id: str, *, limit: int) -> tuple[TurnContext, ...]:
        if limit < 0:
            raise ValueError("history limit must be non-negative")
        if limit == 0:
            return ()
        rows = self.connection.execute(
            """
            SELECT turn_index, program_sha256, program_size, program_relative_path,
                   feedback_sha256, feedback_size, feedback_relative_path
            FROM attempt
            WHERE session_id = ? AND program_sha256 IS NOT NULL
                                 AND feedback_sha256 IS NOT NULL
            ORDER BY turn_index DESC LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        history: list[TurnContext] = []
        for row in reversed(rows):
            program = self._artifact_from_row(row[1], row[2], row[3])
            feedback = self._artifact_from_row(row[4], row[5], row[6])
            if program is None or feedback is None:
                raise SessionStateError("turn history has incomplete artifacts")
            history.append(
                TurnContext(
                    turn_index=row[0],
                    program=self.store.read(program),
                    feedback=self.store.read(feedback),
                )
            )
        return tuple(history)

    def resume(self, session_id: str) -> SessionRecord:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE session SET
                    status = CASE
                        WHEN next_turn > target_turns THEN 'completed'
                        ELSE 'active'
                    END,
                    pause_reason = NULL,
                                   updated_at = ?
                WHERE id = ? AND status = 'paused'
                """,
                (now, session_id),
            )
        if cursor.rowcount != 1:
            raise SessionStateError("only a paused session can be resumed")
        return self.get(session_id)

    def list_sessions(self) -> tuple[SessionRecord, ...]:
        session_ids = self.connection.execute(
            "SELECT id FROM session ORDER BY created_at"
        ).fetchall()
        return tuple(self.get(row[0]) for row in session_ids)
