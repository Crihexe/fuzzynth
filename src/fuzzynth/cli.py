"""Command-line entry point for local Fuzzynth administration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fuzzynth import __version__
from fuzzynth.credentials import CredentialError, load_credentials
from fuzzynth.probe import PROBE_INPUT, PROBE_INSTRUCTIONS, run_probe
from fuzzynth.responses import GenerationRequest


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

    probe = subparsers.add_parser(
        "probe", help="make one bounded live Responses API capability request"
    )
    probe.add_argument("--live", action="store_true", help="confirm network usage")
    probe.add_argument("--provider", choices=("alternate", "official"), required=True)
    probe.add_argument("--model", required=True)
    probe.add_argument("--temperature", type=float)
    probe.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
    )
    probe.add_argument("--verbosity", choices=("low", "medium", "high"))
    probe.add_argument("--max-output-tokens", type=int, default=64)
    probe.add_argument("--timeout", type=float, default=30.0)
    probe.add_argument("--credentials", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args.credentials)
        if args.command == "probe":
            if not args.live:
                print("fuzzynth: probe requires --live", file=sys.stderr)
                return 2
            credentials = load_credentials(args.credentials)
            provider = getattr(credentials, args.provider)
            request = GenerationRequest(
                model=args.model,
                instructions=PROBE_INSTRUCTIONS,
                input_text=PROBE_INPUT,
                max_output_tokens=args.max_output_tokens,
                temperature=args.temperature,
                reasoning_effort=args.reasoning_effort,
                verbosity=args.verbosity,
            )
            result = run_probe(provider, request, timeout=args.timeout)
            print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
            return 0 if result.supported else 1
    except (CredentialError, ValueError) as exc:
        print(f"fuzzynth: configuration error: {exc}", file=sys.stderr)
        return 2
    return 2
