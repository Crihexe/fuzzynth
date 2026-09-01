"""Command-line entry point for local Fuzzynth administration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fuzzynth import __version__
from fuzzynth.credentials import CredentialError, load_credentials


def _doctor(credentials_path: Path | None) -> int:
    credentials = load_credentials(credentials_path)
    result = {
        "status": "ok",
        "version": __version__,
        "providers": credentials.safe_status(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fuzzynth")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="validate local configuration without network calls"
    )
    doctor.add_argument(
        "--credentials",
        type=Path,
        help="external provider credentials file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args.credentials)
    except CredentialError as exc:
        print(f"fuzzynth: configuration error: {exc}", file=sys.stderr)
        return 2
    return 2
