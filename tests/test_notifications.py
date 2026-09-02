from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.artifacts import ArtifactRef
from fuzzynth.campaign_turn import TurnResult
from fuzzynth.corpus import CorpusReference
from fuzzynth.execution_service import RecordedExecution
from fuzzynth.notifications import (
    TelegramCampaignNotifier,
    TelegramCredentials,
    build_campaign_alert,
    load_telegram_credentials,
)
from fuzzynth.sessions import SessionRecord


class CampaignNotificationTests(unittest.TestCase):
    @staticmethod
    def session(status: str = "active") -> SessionRecord:
        return SessionRecord(
            session_id="session-test",
            worker_id="spark-custom-iterative-js",
            seed=7,
            target_turns=8,
            reasoning_effort="none",
            temperature=None,
            status=status,
            next_turn=2,
            pause_reason=None,
            corpus=ArtifactRef(
                sha256="f" * 64,
                size=100,
                relative_path="ff/" + "f" * 62,
            ),
            created_at="2026-09-02T00:00:00Z",
            updated_at="2026-09-02T00:00:01Z",
        )

    @staticmethod
    def result(*, pause_reason=None, candidate=True) -> TurnResult:
        execution = RecordedExecution(
            execution_id="exec-test",
            profile="release_symbolized",
            image_id="sha256:" + "a" * 64,
            d8_sha256="b" * 64,
            program_sha256="c" * 64,
            stdout_sha256="d" * 64,
            stderr_sha256="e" * 64,
            duration_ms=10,
            outcome="signal" if candidate else "ok",
            bug_candidate=candidate,
            exit_code=139 if candidate else 0,
            signal_name="SIGSEGV" if candidate else None,
            timed_out=False,
            oom_killed=False,
            output_truncated=False,
            stdout=b"sensitive stdout",
            stderr=b"sensitive stderr",
        )
        return TurnResult(
            generation_id="gen-test",
            execution=execution,
            program=b"sensitive program body",
            feedback=b"sensitive feedback",
            pause_reason=pause_reason,
            stop_reason="bug_candidate" if candidate else None,
            response_status="completed",
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=5,
            reasoning_tokens=0,
            actual_microunits=0,
        )

    def test_crash_alert_contains_identity_but_no_sensitive_body(self) -> None:
        alert = build_campaign_alert(
            self.session(),
            self.result(),
            corpus_sources=(
                CorpusReference(name="sample.js", sha256="a" * 64),
            ),
        )

        self.assertIsNotNone(alert)
        self.assertIn("program_sha256=" + "c" * 64, alert)
        self.assertIn("worker continuing", alert)
        self.assertIn("corpus_window_sha256=" + "f" * 64, alert)
        self.assertIn("corpus_source=sample.js@" + "a" * 64, alert)
        self.assertIn("no_automatic_replay", alert)
        self.assertNotIn("sensitive", alert)

    def test_pause_alert_names_reason(self) -> None:
        alert = build_campaign_alert(
            self.session(status="paused"),
            self.result(pause_reason="provider_quota_or_rate_limit", candidate=False),
        )

        self.assertIn("provider_quota_or_rate_limit", alert)
        self.assertNotIn("sensitive", alert)

    def test_notifier_uses_injected_sender_without_exposing_credentials(self) -> None:
        sent = []
        credentials = TelegramCredentials(token="secret-token", chat_id="secret-chat")
        notifier = TelegramCampaignNotifier(
            credentials,
            sender=lambda _credentials, message: sent.append(message) or 1,
        )

        notifier(self.session(), self.result())

        self.assertEqual(len(sent), 1)
        self.assertNotIn("secret-token", sent[0])
        self.assertNotIn("secret-chat", sent[0])

    def test_credentials_accept_optional_owner_user_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "telegram.env"
            path.write_text(
                "TELEGRAM_BOT_TOKEN=test-token\n"
                "TELEGRAM_CHAT_ID=00100\n"
                "TELEGRAM_USER_ID=00200\n",
                encoding="utf-8",
            )
            path.chmod(0o600)

            credentials = load_telegram_credentials(path)

        self.assertEqual(credentials.chat_id, "100")
        self.assertEqual(credentials.user_id, "200")


if __name__ == "__main__":
    unittest.main()
