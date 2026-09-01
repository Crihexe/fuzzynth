#!/usr/bin/env python3
"""Send a concise Fuzzynth development update through Telegram."""

from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlencode


DEFAULT_CREDENTIALS_PATH = Path("/root/fuzzynth_telegram_credentials")
MAX_MESSAGE_CHARS = 4_000


class NotificationError(RuntimeError):
    """A safe-to-display notification failure."""


def _parse_env_file(path: Path) -> dict[str, str]:
    try:
        file_mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise NotificationError(f"credentials file is unavailable: {path}") from exc

    if file_mode & 0o077:
        raise NotificationError(
            f"credentials file must not be group/world accessible: {path}"
        )

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise NotificationError(f"credentials file cannot be read: {path}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise NotificationError(
                f"invalid credentials entry at {path}:{line_number}"
            )

        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    return values


def load_credentials(path: Path) -> tuple[str, str]:
    values = _parse_env_file(path)
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = values.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        raise NotificationError(
            "credentials file must define TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        )
    if any(character.isspace() for character in token):
        raise NotificationError("TELEGRAM_BOT_TOKEN has an invalid format")

    return token, chat_id


def read_message(parts: list[str]) -> str:
    if parts:
        message = " ".join(parts)
    elif not sys.stdin.isatty():
        message = sys.stdin.read()
    else:
        raise NotificationError("provide a message as arguments or standard input")

    message = message.strip()
    if not message:
        raise NotificationError("message is empty")
    if len(message) > MAX_MESSAGE_CHARS:
        raise NotificationError(
            f"message exceeds the {MAX_MESSAGE_CHARS}-character safety limit"
        )
    return message


def send_message(
    *, token: str, chat_id: str, message: str, silent: bool, timeout: float
) -> int:
    payload = urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
            "disable_notification": "true" if silent else "false",
        }
    ).encode("utf-8")

    connection = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
    try:
        connection.request(
            "POST",
            f"/bot{token}/sendMessage",
            body=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response_body = response.read()
    except (OSError, http.client.HTTPException) as exc:
        raise NotificationError(
            f"Telegram network request failed ({type(exc).__name__})"
        ) from exc
    finally:
        connection.close()

    try:
        decoded = json.loads(response_body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NotificationError(
            f"Telegram returned an unreadable response (HTTP {response.status})"
        ) from exc

    if response.status != 200 or decoded.get("ok") is not True:
        description = str(decoded.get("description", "request rejected"))
        description = description.replace(token, "[REDACTED]")[:240]
        raise NotificationError(
            f"Telegram rejected the message (HTTP {response.status}): {description}"
        )

    try:
        return int(decoded["result"]["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NotificationError(
            "Telegram accepted the request but omitted the message identifier"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*", help="message text; stdin is used if omitted")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path(
            os.environ.get(
                "FUZZYNTH_TELEGRAM_CREDENTIALS", str(DEFAULT_CREDENTIALS_PATH)
            )
        ),
        help="dotenv credentials file (default: %(default)s)",
    )
    parser.add_argument(
        "--silent", action="store_true", help="send without a notification sound"
    )
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="network timeout in seconds"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate credentials and message without contacting Telegram",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token = ""
    try:
        message = read_message(args.message)
        token, chat_id = load_credentials(args.credentials)
        if args.dry_run:
            print("telegram_notification=dry-run-ok")
            return 0

        message_id = send_message(
            token=token,
            chat_id=chat_id,
            message=message,
            silent=args.silent,
            timeout=args.timeout,
        )
        print(f"telegram_notification=sent message_id={message_id}")
        return 0
    except NotificationError as exc:
        safe_error = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        print(f"telegram_notification=failed error={safe_error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
