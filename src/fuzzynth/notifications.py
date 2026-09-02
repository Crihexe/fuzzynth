"""Secret-safe Telegram alerts for crash and automatic worker pauses."""

from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import os
from pathlib import Path
import stat
from typing import Callable
from urllib.parse import urlencode

from fuzzynth.campaign_turn import TurnResult
from fuzzynth.sessions import SessionRecord


DEFAULT_TELEGRAM_CREDENTIALS = Path("/root/fuzzynth_telegram_credentials")
MAX_MESSAGE_CHARS = 4_000


class NotificationError(RuntimeError):
    """A safe-to-display Telegram alert failure."""


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    token: str = field(repr=False)
    chat_id: str = field(repr=False)


def load_telegram_credentials(path: Path | None = None) -> TelegramCredentials:
    selected = path or Path(
        os.environ.get(
            "FUZZYNTH_TELEGRAM_CREDENTIALS",
            str(DEFAULT_TELEGRAM_CREDENTIALS),
        )
    )
    try:
        mode = stat.S_IMODE(selected.stat().st_mode)
    except OSError as exc:
        raise NotificationError("Telegram credentials file is unavailable") from exc
    if mode & 0o077:
        raise NotificationError("Telegram credentials file permissions are unsafe")
    try:
        lines = selected.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise NotificationError("Telegram credentials file cannot be read") from exc
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise NotificationError(
                f"invalid Telegram credentials entry at line {line_number}"
            )
        name, value = line.split("=", maxsplit=1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name.strip()] = value
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = values.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id or any(character.isspace() for character in token):
        raise NotificationError("Telegram credentials are incomplete or invalid")
    return TelegramCredentials(token=token, chat_id=chat_id)


def send_telegram_message(
    credentials: TelegramCredentials,
    message: str,
    *,
    silent: bool = False,
    timeout: float = 15.0,
) -> int:
    if not message or len(message) > MAX_MESSAGE_CHARS:
        raise NotificationError("Telegram alert has an invalid length")
    payload = urlencode(
        {
            "chat_id": credentials.chat_id,
            "text": message,
            "disable_web_page_preview": "true",
            "disable_notification": "true" if silent else "false",
        }
    ).encode()
    connection = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
    try:
        connection.request(
            "POST",
            f"/bot{credentials.token}/sendMessage",
            body=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response_body = response.read(256 * 1024 + 1)
        if len(response_body) > 256 * 1024:
            raise NotificationError("Telegram response exceeded local byte limit")
    except (OSError, http.client.HTTPException) as exc:
        raise NotificationError(
            f"Telegram request failed ({type(exc).__name__})"
        ) from exc
    finally:
        connection.close()
    try:
        decoded = json.loads(response_body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NotificationError(
            f"Telegram returned invalid JSON (HTTP {response.status})"
        ) from exc
    if response.status != 200 or decoded.get("ok") is not True:
        raise NotificationError(f"Telegram rejected alert (HTTP {response.status})")
    try:
        return int(decoded["result"]["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NotificationError("Telegram response omitted message ID") from exc


Sender = Callable[[TelegramCredentials, str], int]


def build_campaign_alert(session: SessionRecord, result: TurnResult) -> str | None:
    execution = result.execution
    if result.stop_reason == "bug_candidate" and execution is not None:
        return "\n".join(
            (
                "FUZZYNTH CRASH CANDIDATE — worker stopped",
                f"worker={session.worker_id}",
                f"session={session.session_id}",
                f"generation={result.generation_id}",
                f"execution={execution.execution_id}",
                f"outcome={execution.outcome}",
                f"signal={execution.signal_name or 'none'}",
                f"profile={execution.profile}",
                f"program_sha256={execution.program_sha256}",
                "action=evidence_saved_no_automatic_replay",
            )
        )
    if result.pause_reason:
        return "\n".join(
            (
                "FUZZYNTH WORKER PAUSED",
                f"worker={session.worker_id}",
                f"session={session.session_id}",
                f"generation={result.generation_id}",
                f"reason={result.pause_reason}",
                "action=manual_review_required",
            )
        )
    return None


class TelegramCampaignNotifier:
    def __init__(
        self,
        credentials: TelegramCredentials,
        *,
        sender: Sender | None = None,
    ):
        self.credentials = credentials
        self.sender = sender or (
            lambda creds, message: send_telegram_message(creds, message)
        )

    def __call__(self, session: SessionRecord, result: TurnResult) -> None:
        message = build_campaign_alert(session, result)
        if message is not None:
            self.sender(self.credentials, message)
