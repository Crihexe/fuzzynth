"""Persistent cross-session semantic novelty accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3


class NoveltyError(RuntimeError):
    """Semantic novelty evidence could not be recorded safely."""


@dataclass(frozen=True, slots=True)
class SemanticNovelty:
    signature_occurrence: int
    new_mechanisms: tuple[str, ...]
    new_operations: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "new_mechanisms": list(self.new_mechanisms),
            "new_operations": list(self.new_operations),
            "repeated_globally": self.signature_occurrence > 1,
            "signature_occurrence": self.signature_occurrence,
        }


_SCHEMA = """
CREATE TABLE semantic_observation (
  generation_id TEXT PRIMARY KEY,
  worker_id TEXT NOT NULL,
  signature TEXT NOT NULL CHECK(length(signature) = 16),
  signature_occurrence INTEGER NOT NULL CHECK(signature_occurrence > 0),
  mechanisms_json TEXT NOT NULL,
  operations_json TEXT NOT NULL,
  new_mechanisms_json TEXT NOT NULL,
  new_operations_json TEXT NOT NULL,
  observed_at TEXT NOT NULL
) STRICT;
CREATE INDEX semantic_signature_idx
ON semantic_observation(worker_id, signature, signature_occurrence);

CREATE TABLE semantic_feature_count (
  worker_id TEXT NOT NULL,
  feature_kind TEXT NOT NULL CHECK(feature_kind IN ('mechanism', 'operation')),
  feature TEXT NOT NULL,
  observation_count INTEGER NOT NULL CHECK(observation_count > 0),
  PRIMARY KEY (worker_id, feature_kind, feature)
) STRICT;
PRAGMA user_version = 1;
"""


def _canonical_json(values: tuple[str, ...]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _string_tuple(profile: dict[str, object], name: str, *, limit: int) -> tuple[str, ...]:
    value = profile.get(name)
    if not isinstance(value, list) or len(value) > limit:
        raise NoveltyError(f"semantic profile has invalid {name}")
    result = tuple(value)
    if any(
        not isinstance(item, str)
        or not item
        or len(item.encode("utf-8")) > 80
        for item in result
    ):
        raise NoveltyError(f"semantic profile has invalid {name}")
    return result


class SemanticNoveltyLedger:
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
            raise NoveltyError(f"unsupported novelty schema version: {version}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SemanticNoveltyLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> SemanticNovelty:
        try:
            occurrence = int(row[0])
            new_mechanisms = tuple(json.loads(str(row[1])))
            new_operations = tuple(json.loads(str(row[2])))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise NoveltyError("stored semantic novelty evidence is invalid") from exc
        return SemanticNovelty(occurrence, new_mechanisms, new_operations)

    def record_success(
        self,
        *,
        worker_id: str,
        generation_id: str,
        semantic_profile: dict[str, object],
    ) -> SemanticNovelty:
        signature = semantic_profile.get("signature")
        if not isinstance(signature, str) or re.fullmatch(r"[0-9a-f]{16}", signature) is None:
            raise NoveltyError("semantic profile has invalid signature")
        mechanisms = _string_tuple(semantic_profile, "mechanisms", limit=32)
        operations = _string_tuple(semantic_profile, "operations", limit=32)
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                """
                SELECT signature_occurrence, new_mechanisms_json,
                       new_operations_json
                FROM semantic_observation WHERE generation_id = ?
                """,
                (generation_id,),
            ).fetchone()
            if existing is not None:
                self.connection.commit()
                return self._from_row(existing)
            occurrence = 1 + self.connection.execute(
                """
                SELECT count(*) FROM semantic_observation
                WHERE worker_id = ? AND signature = ?
                """,
                (worker_id, signature),
            ).fetchone()[0]

            def new_features(kind: str, values: tuple[str, ...]) -> tuple[str, ...]:
                return tuple(
                    value
                    for value in values
                    if self.connection.execute(
                        """
                        SELECT 1 FROM semantic_feature_count
                        WHERE worker_id = ? AND feature_kind = ? AND feature = ?
                        """,
                        (worker_id, kind, value),
                    ).fetchone()
                    is None
                )

            new_mechanisms = new_features("mechanism", mechanisms)
            new_operations = new_features("operation", operations)
            self.connection.execute(
                """
                INSERT INTO semantic_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    worker_id,
                    signature,
                    occurrence,
                    _canonical_json(mechanisms),
                    _canonical_json(operations),
                    _canonical_json(new_mechanisms),
                    _canonical_json(new_operations),
                    now,
                ),
            )
            for kind, values in (
                ("mechanism", mechanisms),
                ("operation", operations),
            ):
                self.connection.executemany(
                    """
                    INSERT INTO semantic_feature_count VALUES (?, ?, ?, 1)
                    ON CONFLICT(worker_id, feature_kind, feature) DO UPDATE SET
                      observation_count = observation_count + 1
                    """,
                    ((worker_id, kind, value) for value in values),
                )
            self.connection.commit()
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise NoveltyError("failed to record semantic novelty evidence") from exc
        return SemanticNovelty(occurrence, new_mechanisms, new_operations)
