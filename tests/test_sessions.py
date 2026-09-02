from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.artifacts import ArtifactStore
from fuzzynth.campaign_config import SessionPlan, load_campaign_configuration
from fuzzynth.campaign_turn import TurnResult
from fuzzynth.execution_service import RecordedExecution
from fuzzynth.sessions import SessionLedger, SessionStateError


class SessionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.store = ArtifactStore(root / "artifacts")
        self.ledger = SessionLedger(root / "sessions.sqlite3", self.store)
        self.addCleanup(self.ledger.close)
        config = load_campaign_configuration(Path("config/campaign-workers.toml"))
        self.worker = config.workers["spark-custom-iterative-js"]

    @staticmethod
    def execution(*, candidate: bool = False) -> RecordedExecution:
        return RecordedExecution(
            execution_id="exec-test",
            profile="release_symbolized",
            image_id="sha256:" + "a" * 64,
            d8_sha256="b" * 64,
            program_sha256="c" * 64,
            stdout_sha256="d" * 64,
            stderr_sha256="e" * 64,
            duration_ms=5,
            outcome="signal" if candidate else "ok",
            bug_candidate=candidate,
            exit_code=139 if candidate else 0,
            signal_name="SIGSEGV" if candidate else None,
            timed_out=False,
            oom_killed=False,
            output_truncated=False,
            stdout=b"",
            stderr=b"",
        )

    def result(
        self,
        index: int,
        *,
        pause_reason: str | None = None,
        candidate: bool = False,
    ) -> TurnResult:
        return TurnResult(
            generation_id=f"gen-{index}",
            execution=self.execution(candidate=candidate),
            program=f"print({index});".encode(),
            feedback=b'{"outcome":"ok"}',
            pause_reason=pause_reason,
            stop_reason="bug_candidate" if candidate else None,
            response_status="completed",
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=5,
            reasoning_tokens=0,
            actual_microunits=0,
        )

    def test_persists_corpus_plan_turn_and_history(self) -> None:
        session = self.ledger.start(
            self.worker,
            SessionPlan(seed=7, target_turns=2, reasoning_effort="none", temperature=None),
            corpus_window=b"// selected PoC data",
        )
        session = self.ledger.record_turn(session.session_id, self.result(1))

        self.assertEqual(session.status, "active")
        self.assertEqual(session.next_turn, 2)
        self.assertEqual(self.ledger.corpus_bytes(session.session_id), b"// selected PoC data")
        history = self.ledger.history(session.session_id, limit=4)
        self.assertEqual(history[0].program, b"print(1);")

        session = self.ledger.record_turn(session.session_id, self.result(2))
        self.assertEqual(session.status, "completed")

    def test_provider_pause_is_durable_and_resumable(self) -> None:
        session = self.ledger.start(
            self.worker,
            SessionPlan(7, 4, "none", None),
            corpus_window=None,
        )
        session = self.ledger.record_turn(
            session.session_id,
            self.result(1, pause_reason="provider_quota_or_rate_limit"),
        )

        self.assertEqual(session.status, "paused")
        self.assertEqual(session.pause_reason, "provider_quota_or_rate_limit")
        resumed = self.ledger.resume(session.session_id)
        self.assertEqual(resumed.status, "active")
        self.assertEqual(resumed.next_turn, 2)

    def test_bug_candidate_stops_without_replay(self) -> None:
        session = self.ledger.start(
            self.worker,
            SessionPlan(7, 4, "none", None),
            corpus_window=None,
        )
        session = self.ledger.record_turn(
            session.session_id,
            self.result(1, candidate=True),
        )

        self.assertEqual(session.status, "crash")
        self.assertEqual(session.pause_reason, "bug_candidate")
        with self.assertRaisesRegex(SessionStateError, "paused"):
            self.ledger.resume(session.session_id)

    def test_only_one_open_session_per_worker(self) -> None:
        plan = SessionPlan(7, 4, "none", None)
        self.ledger.start(self.worker, plan, corpus_window=None)

        with self.assertRaisesRegex(SessionStateError, "open session"):
            self.ledger.start(self.worker, plan, corpus_window=None)


if __name__ == "__main__":
    unittest.main()
