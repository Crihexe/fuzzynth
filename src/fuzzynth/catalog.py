"""SQLite metadata catalog linking generations, executions, usage, and artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from fuzzynth.artifacts import ArtifactRef


class CatalogError(RuntimeError):
    """Evidence metadata could not be recorded without losing integrity."""


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    generation_id: str
    campaign_id: str
    session_id: str | None
    provider: str
    requested_model: str
    actual_model: str | None
    status: str
    request: ArtifactRef
    raw_stream: ArtifactRef | None
    program: ArtifactRef | None
    response_id: str | None
    requested_parameters: dict[str, Any]
    effective_parameters: dict[str, Any]
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_microusd: int | None
    started_at: str
    finished_at: str


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_id: str
    generation_id: str | None
    program: ArtifactRef
    stdout: ArtifactRef
    stderr: ArtifactRef
    profile: str
    image_id: str
    d8_sha256: str
    flags: tuple[str, ...]
    outcome: str
    bug_candidate: bool
    exit_code: int | None
    signal_name: str | None
    timed_out: bool
    oom_killed: bool
    output_truncated: bool
    duration_ms: int
    docker_error: str
    started_at: str
    details: ArtifactRef | None = None


@dataclass(frozen=True, slots=True)
class BugCandidateRecord:
    execution_id: str
    generation_id: str
    session_id: str | None
    worker_id: str
    outcome: str
    signal_name: str | None
    program_sha256: str
    stderr_sha256: str
    started_at: str


_SCHEMA = """
CREATE TABLE artifact (
  sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
  size INTEGER NOT NULL CHECK(size >= 0),
  relative_path TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE generation (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  session_id TEXT,
  provider TEXT NOT NULL,
  requested_model TEXT NOT NULL,
  actual_model TEXT,
  status TEXT NOT NULL CHECK(status IN ('completed', 'failed', 'incomplete', 'cancelled')),
  request_sha256 TEXT NOT NULL REFERENCES artifact(sha256),
  raw_stream_sha256 TEXT REFERENCES artifact(sha256),
  program_sha256 TEXT REFERENCES artifact(sha256),
  response_id TEXT,
  requested_parameters_json TEXT NOT NULL,
  effective_parameters_json TEXT NOT NULL,
  input_tokens INTEGER CHECK(input_tokens >= 0),
  cached_input_tokens INTEGER CHECK(cached_input_tokens >= 0),
  output_tokens INTEGER CHECK(output_tokens >= 0),
  reasoning_tokens INTEGER CHECK(reasoning_tokens >= 0),
  cost_microusd INTEGER CHECK(cost_microusd >= 0),
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL
) STRICT;

CREATE TABLE execution (
  id TEXT PRIMARY KEY,
  generation_id TEXT REFERENCES generation(id),
  program_sha256 TEXT NOT NULL REFERENCES artifact(sha256),
  stdout_sha256 TEXT NOT NULL REFERENCES artifact(sha256),
  stderr_sha256 TEXT NOT NULL REFERENCES artifact(sha256),
  profile TEXT NOT NULL,
  image_id TEXT NOT NULL CHECK(length(image_id) = 71),
  d8_sha256 TEXT NOT NULL CHECK(length(d8_sha256) = 64),
  flags_json TEXT NOT NULL,
  outcome TEXT NOT NULL,
  bug_candidate INTEGER NOT NULL CHECK(bug_candidate IN (0, 1)),
  exit_code INTEGER,
  signal_name TEXT,
  timed_out INTEGER NOT NULL CHECK(timed_out IN (0, 1)),
  oom_killed INTEGER NOT NULL CHECK(oom_killed IN (0, 1)),
  output_truncated INTEGER NOT NULL CHECK(output_truncated IN (0, 1)),
  duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
  docker_error TEXT NOT NULL,
  started_at TEXT NOT NULL,
  details_sha256 TEXT REFERENCES artifact(sha256)
) STRICT;

CREATE INDEX generation_campaign_idx ON generation(campaign_id, started_at);
CREATE INDEX generation_provider_idx ON generation(provider, requested_model, started_at);
CREATE INDEX execution_outcome_idx ON execution(outcome, bug_candidate, started_at);
PRAGMA user_version = 2;
"""

_MIGRATE_V1_TO_V2 = """
ALTER TABLE execution ADD COLUMN details_sha256 TEXT REFERENCES artifact(sha256);
PRAGMA user_version = 2;
"""


class EvidenceCatalog:
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=5)
        os.chmod(path, 0o600)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self.connection.executescript(_SCHEMA)
        elif version == 1:
            self.connection.executescript(_MIGRATE_V1_TO_V2)
        elif version != 2:
            self.connection.close()
            raise CatalogError(f"unsupported catalog schema version: {version}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> EvidenceCatalog:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    def _add_artifact(self, reference: ArtifactRef, created_at: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO artifact VALUES (?, ?, ?, ?)",
            (
                reference.sha256,
                reference.size,
                reference.relative_path,
                created_at,
            ),
        )
        existing = self.connection.execute(
            "SELECT size, relative_path FROM artifact WHERE sha256 = ?",
            (reference.sha256,),
        ).fetchone()
        if existing != (reference.size, reference.relative_path):
            raise CatalogError("artifact metadata conflicts with existing hash")

    def record_generation(self, record: GenerationRecord) -> None:
        references = tuple(
            ref
            for ref in (record.request, record.raw_stream, record.program)
            if ref is not None
        )
        try:
            with self.connection:
                for reference in references:
                    self._add_artifact(reference, record.finished_at)
                self.connection.execute(
                    """
                    INSERT INTO generation (
                      id, campaign_id, session_id, provider, requested_model,
                      actual_model, status, request_sha256, raw_stream_sha256,
                      program_sha256, response_id, requested_parameters_json,
                      effective_parameters_json, input_tokens, cached_input_tokens,
                      output_tokens, reasoning_tokens, cost_microusd, started_at,
                      finished_at
                    ) VALUES (
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        record.generation_id,
                        record.campaign_id,
                        record.session_id,
                        record.provider,
                        record.requested_model,
                        record.actual_model,
                        record.status,
                        record.request.sha256,
                        record.raw_stream.sha256 if record.raw_stream else None,
                        record.program.sha256 if record.program else None,
                        record.response_id,
                        self._canonical_json(record.requested_parameters),
                        self._canonical_json(record.effective_parameters),
                        record.input_tokens,
                        record.cached_input_tokens,
                        record.output_tokens,
                        record.reasoning_tokens,
                        record.cost_microusd,
                        record.started_at,
                        record.finished_at,
                    ),
                )
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise CatalogError("failed to record generation evidence") from exc

    def record_execution(self, record: ExecutionRecord) -> None:
        try:
            with self.connection:
                for reference in (
                    record.program,
                    record.stdout,
                    record.stderr,
                    record.details,
                ):
                    if reference is None:
                        continue
                    self._add_artifact(reference, record.started_at)
                self.connection.execute(
                    """
                    INSERT INTO execution (
                      id, generation_id, program_sha256, stdout_sha256,
                      stderr_sha256, profile, image_id, d8_sha256, flags_json,
                      outcome, bug_candidate, exit_code, signal_name, timed_out,
                      oom_killed, output_truncated, duration_ms, docker_error,
                      started_at, details_sha256
                    ) VALUES (
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        record.execution_id,
                        record.generation_id,
                        record.program.sha256,
                        record.stdout.sha256,
                        record.stderr.sha256,
                        record.profile,
                        record.image_id,
                        record.d8_sha256,
                        self._canonical_json(record.flags),
                        record.outcome,
                        int(record.bug_candidate),
                        record.exit_code,
                        record.signal_name,
                        int(record.timed_out),
                        int(record.oom_killed),
                        int(record.output_truncated),
                        record.duration_ms,
                        record.docker_error,
                        record.started_at,
                        record.details.sha256 if record.details else None,
                    ),
                )
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise CatalogError("failed to record execution evidence") from exc

    def summary(self) -> dict[str, int]:
        generation_count, known_cost = self.connection.execute(
            "SELECT count(*), coalesce(sum(cost_microusd), 0) FROM generation"
        ).fetchone()
        execution_count, candidates = self.connection.execute(
            "SELECT count(*), coalesce(sum(bug_candidate), 0) FROM execution"
        ).fetchone()
        return {
            "generations": generation_count,
            "executions": execution_count,
            "bug_candidates": candidates,
            "known_cost_microusd": known_cost,
        }

    def latest_bug_candidate(self) -> BugCandidateRecord | None:
        row = self.connection.execute(
            """
            SELECT e.id, e.generation_id, g.session_id, g.campaign_id,
                   e.outcome, e.signal_name, e.program_sha256,
                   e.stderr_sha256, e.started_at
            FROM execution e
            JOIN generation g ON g.id = e.generation_id
            WHERE e.bug_candidate = 1
            ORDER BY e.started_at DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return BugCandidateRecord(
            execution_id=row[0],
            generation_id=row[1],
            session_id=row[2],
            worker_id=row[3],
            outcome=row[4],
            signal_name=row[5],
            program_sha256=row[6],
            stderr_sha256=row[7],
            started_at=row[8],
        )

    def artifact_reference(self, sha256: str) -> ArtifactRef:
        row = self.connection.execute(
            "SELECT size, relative_path FROM artifact WHERE sha256 = ?",
            (sha256,),
        ).fetchone()
        if row is None:
            raise CatalogError("unknown artifact")
        return ArtifactRef(sha256=sha256, size=row[0], relative_path=row[1])
