from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.artifacts import ArtifactStore
from fuzzynth.catalog import (
    CatalogError,
    EvidenceCatalog,
    ExecutionRecord,
    GenerationRecord,
)


class EvidenceCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.store = ArtifactStore(root / "artifacts")
        self.catalog = EvidenceCatalog(root / "catalog.sqlite3")
        self.addCleanup(self.catalog.close)

    def generation(self) -> GenerationRecord:
        return GenerationRecord(
            generation_id="gen-1",
            campaign_id="raw-spark",
            session_id="session-1",
            provider="alternate",
            requested_model="gpt-test",
            actual_model="gpt-test",
            status="completed",
            request=self.store.put(b'{"request":true}'),
            raw_stream=self.store.put(b"data: raw\n\n"),
            program=self.store.put(b"print(1);"),
            response_id="response-1",
            requested_parameters={"temperature_sent": False},
            effective_parameters={"temperature": 1.0},
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=5,
            reasoning_tokens=2,
            cost_microusd=123,
            started_at="2026-09-01T00:00:00Z",
            finished_at="2026-09-01T00:00:01Z",
        )

    def test_records_generation_execution_and_summary(self) -> None:
        generation = self.generation()
        self.catalog.record_generation(generation)
        self.catalog.record_execution(
            ExecutionRecord(
                execution_id="exec-1",
                generation_id=generation.generation_id,
                program=generation.program,  # type: ignore[arg-type]
                stdout=self.store.put(b"1\n"),
                stderr=self.store.put(b""),
                profile="release_symbolized",
                image_id="sha256:" + "a" * 64,
                d8_sha256="b" * 64,
                flags=("--allow-natives-syntax",),
                outcome="ok",
                bug_candidate=False,
                exit_code=0,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                duration_ms=50,
                docker_error="",
                started_at="2026-09-01T00:00:02Z",
                details=self.store.put(b'{"container_state":{}}'),
            )
        )

        details_sha256 = self.catalog.connection.execute(
            "SELECT details_sha256 FROM execution WHERE id = 'exec-1'"
        ).fetchone()[0]
        self.assertIsNotNone(details_sha256)

        self.assertEqual(
            self.catalog.summary(),
            {
                "generations": 1,
                "executions": 1,
                "bug_candidates": 0,
                "known_cost_microusd": 123,
            },
        )

    def test_rejects_duplicate_generation_identity(self) -> None:
        generation = self.generation()
        self.catalog.record_generation(generation)

        with self.assertRaisesRegex(CatalogError, "generation"):
            self.catalog.record_generation(generation)

    def test_latest_bug_candidate_survives_continuing_session_policy(self) -> None:
        generation = self.generation()
        self.catalog.record_generation(generation)
        program = generation.program
        self.catalog.record_execution(
            ExecutionRecord(
                execution_id="exec-crash",
                generation_id=generation.generation_id,
                program=program,  # type: ignore[arg-type]
                stdout=self.store.put(b""),
                stderr=self.store.put(b"Check failed: synthetic"),
                profile="release_symbolized",
                image_id="sha256:" + "a" * 64,
                d8_sha256="b" * 64,
                flags=("--allow-natives-syntax",),
                outcome="v8_fatal",
                bug_candidate=True,
                exit_code=134,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                duration_ms=5,
                docker_error="",
                started_at="2026-09-01T00:00:02Z",
            )
        )

        candidate = self.catalog.latest_bug_candidate()

        self.assertEqual(candidate.execution_id, "exec-crash")
        self.assertEqual(candidate.session_id, "session-1")
        reference = self.catalog.artifact_reference(candidate.stderr_sha256)
        self.assertEqual(self.store.read(reference), b"Check failed: synthetic")

    def test_foreign_key_rejects_unknown_generation(self) -> None:
        program = self.store.put(b"0;")
        with self.assertRaisesRegex(CatalogError, "execution"):
            self.catalog.record_execution(
                ExecutionRecord(
                    execution_id="exec-orphan",
                    generation_id="missing",
                    program=program,
                    stdout=self.store.put(b""),
                    stderr=self.store.put(b""),
                    profile="release",
                    image_id="sha256:" + "a" * 64,
                    d8_sha256="b" * 64,
                    flags=(),
                    outcome="ok",
                    bug_candidate=False,
                    exit_code=0,
                    signal_name=None,
                    timed_out=False,
                    oom_killed=False,
                    output_truncated=False,
                    duration_ms=1,
                    docker_error="",
                    started_at="2026-09-01T00:00:00Z",
                )
            )

    def test_database_permissions_are_private(self) -> None:
        self.assertEqual(self.catalog.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
