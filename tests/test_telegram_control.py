from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from fuzzynth.notifications import NotificationError, TelegramCredentials
from fuzzynth.telegram_control import TelegramControlService, authorize_command


def update(
    update_id: int,
    text: str,
    *,
    chat_id: int = 100,
    user_id: int = 200,
    chat_type: str = "private",
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id},
        },
    }


class TelegramAuthorizationTests(unittest.TestCase):
    def test_explicit_user_and_chat_must_both_match(self) -> None:
        credentials = TelegramCredentials(
            token="secret", chat_id="100", user_id="200"
        )

        self.assertIsNotNone(authorize_command(update(1, "/status"), credentials))
        self.assertIsNone(
            authorize_command(update(2, "/status", chat_id=101), credentials)
        )
        self.assertIsNone(
            authorize_command(update(3, "/status", user_id=201), credentials)
        )

    def test_private_chat_can_use_matching_chat_as_owner(self) -> None:
        credentials = TelegramCredentials(token="secret", chat_id="100")

        self.assertIsNotNone(
            authorize_command(update(1, "/status", user_id=100), credentials)
        )
        self.assertIsNone(
            authorize_command(
                update(2, "/status", user_id=100, chat_type="group"),
                credentials,
            )
        )


class TelegramControlServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state_root = Path(temporary.name) / "state"
        self.credentials = TelegramCredentials(
            token="secret", chat_id="100", user_id="200"
        )
        self.service = TelegramControlService(
            repo_root=Path("."),
            state_root=self.state_root,
            credentials=self.credentials,
        )
        self.addCleanup(self.service.close)

    def test_status_cost_and_worker_replies_are_bounded_and_safe(self) -> None:
        for index, command in enumerate(("/status", "/cost", "/workers"), start=1):
            reply = self.service.handle_update(update(index, command))
            self.assertIsNotNone(reply)
            self.assertLess(len(reply), 4000)
            self.assertNotIn("secret", reply)

    def test_workers_distinguishes_config_control_and_effective_state(self) -> None:
        reply = self.service.handle_update(update(4, "/workers"))

        self.assertIn(
            "terra-custom-xhigh-tool-investigator: config=disabled, "
            "control=running, effective=disabled",
            reply,
        )

    def test_each_authorized_command_reloads_worker_configuration(self) -> None:
        current = self.service.configuration
        with patch(
            "fuzzynth.telegram_control.load_campaign_configuration",
            return_value=current,
        ) as loader:
            self.service.handle_update(update(5, "/workers"))

        loader.assert_called_once()

    def test_pause_worker_is_idempotent_and_enforced(self) -> None:
        worker_id = "spark-custom-iterative-js"
        command = update(10, f"/pause {worker_id}")

        first = self.service.handle_update(command)
        second = self.service.handle_update(command)

        self.assertIn("applied=true", first)
        self.assertIn("applied=false", second)
        self.assertEqual(self.service.control.effective_state(worker_id), "paused")

    def test_stop_and_start_require_confirmation(self) -> None:
        rejected = self.service.handle_update(update(20, "/stop"))
        stopped = self.service.handle_update(update(21, "/stop CONFIRM"))
        resume_rejected = self.service.handle_update(update(22, "/resume all"))
        started = self.service.handle_update(update(23, "/start CONFIRM"))

        self.assertIn("Confirmation required", rejected)
        self.assertIn("global=stopped", stopped)
        self.assertIn("use /start CONFIRM", resume_rejected)
        self.assertIn("global=running", started)

    def test_resume_all_clears_individual_worker_pauses(self) -> None:
        worker_id = "spark-custom-iterative-js"
        self.service.handle_update(update(24, f"/pause {worker_id}"))

        reply = self.service.handle_update(update(25, "/resume all"))

        self.assertIn("effective=running", reply)
        self.assertEqual(self.service.control.effective_state(worker_id), "running")

    def test_unknown_worker_and_text_do_not_execute_arbitrary_input(self) -> None:
        unknown = self.service.handle_update(update(30, "/pause not-a-worker"))
        plain = self.service.handle_update(update(31, "rm -rf something"))

        self.assertIn("Unknown worker", unknown)
        self.assertIn("FUZZYNTH COMMANDS", plain)
        self.assertEqual(self.service.control.global_state(), "running")

    def test_polling_advances_offset_after_reply(self) -> None:
        requested_offsets = []
        replies = []

        def fetcher(_credentials, offset, _timeout):
            requested_offsets.append(offset)
            return [update(40, "/status")]

        processed = self.service.poll_once(
            poll_timeout=0,
            fetcher=fetcher,
            sender=lambda _credentials, reply: replies.append(reply) or 1,
        )

        self.assertEqual(processed, 1)
        self.assertEqual(requested_offsets, [0])
        self.assertEqual(len(replies), 1)
        self.assertEqual(self.service.control.telegram_offset(), 41)

    def test_failed_reply_does_not_lose_mutating_update(self) -> None:
        command = update(50, "/pause all")

        with self.assertRaises(NotificationError):
            self.service.poll_once(
                poll_timeout=0,
                fetcher=lambda *_args: [command],
                sender=lambda *_args: (_ for _ in ()).throw(
                    NotificationError("synthetic failure")
                ),
            )

        self.assertEqual(self.service.control.global_state(), "paused")
        self.assertEqual(self.service.control.telegram_offset(), 0)
        replies = []
        self.service.poll_once(
            poll_timeout=0,
            fetcher=lambda *_args: [command],
            sender=lambda _credentials, reply: replies.append(reply) or 1,
        )
        self.assertIn("applied=false", replies[0])
        self.assertEqual(self.service.control.telegram_offset(), 51)

    def test_unauthorized_update_is_ignored_but_consumed(self) -> None:
        replies = []
        processed = self.service.poll_once(
            poll_timeout=0,
            fetcher=lambda *_args: [update(60, "/stop CONFIRM", user_id=999)],
            sender=lambda _credentials, reply: replies.append(reply) or 1,
        )

        self.assertEqual(processed, 1)
        self.assertEqual(replies, [])
        self.assertEqual(self.service.control.global_state(), "running")
        self.assertEqual(self.service.control.telegram_offset(), 61)


if __name__ == "__main__":
    unittest.main()
