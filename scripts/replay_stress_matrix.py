#!/usr/bin/env python3
"""Replay successful generated programs under feature-routed V8 stress flags."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from fuzzynth.execution_service import execute_program
from fuzzynth.campaign_config import load_campaign_configuration
from fuzzynth.stress_replay import applicable_profiles


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--state-root", type=Path, default=Path("state"))
    parser.add_argument("--jobs", type=int, default=min(12, os.cpu_count() or 1))
    parser.add_argument("--max-programs", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _load_programs(
    state_root: Path,
    *,
    max_programs: int | None,
) -> list[tuple[str, str, str, bytes]]:
    catalog_path = state_root / "catalog.sqlite3"
    artifacts_root = state_root / "artifacts"
    with sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT g.id, g.campaign_id, g.program_sha256, a.relative_path
            FROM generation g
            JOIN artifact a ON a.sha256 = g.program_sha256
            WHERE EXISTS (
              SELECT 1 FROM execution e
              WHERE e.generation_id = g.id AND e.outcome = 'ok'
            )
            ORDER BY g.finished_at
            """
        ).fetchall()
    programs: list[tuple[str, str, str, bytes]] = []
    seen: set[str] = set()
    for generation_id, campaign_id, sha256, relative_path in rows:
        if sha256 in seen:
            continue
        data = (artifacts_root / relative_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError("program artifact failed SHA-256 validation")
        seen.add(sha256)
        programs.append((generation_id, campaign_id, sha256, data))
        if max_programs is not None and len(programs) >= max_programs:
            break
    return programs


def _existing_keys(state_root: Path) -> set[tuple[str, str, str]]:
    with sqlite3.connect(
        f"file:{state_root / 'catalog.sqlite3'}?mode=ro",
        uri=True,
    ) as connection:
        return {
            (program_sha256, profile, flags_json)
            for program_sha256, profile, flags_json in connection.execute(
                "SELECT program_sha256, profile, flags_json FROM execution"
            )
        }


def _notify(repo_root: Path, message: str) -> None:
    subprocess.run(
        [sys.executable, str(repo_root / "scripts/notify_telegram.py"), message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )


def main() -> int:
    args = _arguments()
    if args.jobs < 1 or args.jobs > 32:
        raise SystemExit("--jobs must be between 1 and 32")
    if args.max_programs is not None and args.max_programs < 1:
        raise SystemExit("--max-programs must be positive")
    repo_root = args.repo_root.resolve()
    state_root = args.state_root.resolve()
    programs = _load_programs(state_root, max_programs=args.max_programs)
    configuration = load_campaign_configuration(
        repo_root / "config/campaign-workers.toml",
        repo_root=repo_root,
    )
    existing = _existing_keys(state_root)
    work = []
    by_profile: dict[str, int] = {}
    for generation_id, campaign_id, sha256, program in programs:
        worker = configuration.workers.get(campaign_id)
        support_files = worker.support_files if worker is not None else ()
        for profile in applicable_profiles(program):
            flags_json = json.dumps(profile.flags, separators=(",", ":"))
            if (sha256, profile.build_profile, flags_json) in existing:
                continue
            work.append((generation_id, program, profile, support_files))
            by_profile[profile.name] = by_profile.get(profile.name, 0) + 1
    print(
        json.dumps(
            {
                "event": "stress_replay_plan",
                "programs": len(programs),
                "executions": len(work),
                "by_profile": by_profile,
                "jobs": args.jobs,
                "dry_run": args.dry_run,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.dry_run:
        return 0

    completed = 0
    candidates = 0
    errors = 0

    def run(item):
        generation_id, program, profile, support_files = item
        return profile, execute_program(
            program,
            generation_id=generation_id,
            build_profile=profile.build_profile,
            worker_profile="standard",
            flags=profile.flags,
            repo_root=repo_root,
            state_root=state_root,
            support_files=support_files,
        )

    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run, item): item[2].name for item in work}
        for future in as_completed(futures):
            completed += 1
            profile_name = futures[future]
            try:
                profile, result = future.result()
            except Exception as exc:
                errors += 1
                print(
                    json.dumps(
                        {
                            "event": "stress_replay_error",
                            "profile": profile_name,
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                continue
            if result.bug_candidate:
                candidates += 1
                print(
                    json.dumps(
                        {
                            "event": "stress_replay_candidate",
                            "profile": profile.name,
                            "execution_id": result.execution_id,
                            "outcome": result.outcome,
                            "program_sha256": result.program_sha256,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                _notify(
                    repo_root,
                    "FUZZYNTH STRESS REPLAY — candidate captured\n"
                    f"profile={profile.name}\n"
                    f"execution={result.execution_id}\n"
                    f"outcome={result.outcome}\n"
                    f"program_sha256={result.program_sha256}",
                )
            if completed % 100 == 0:
                print(
                    json.dumps(
                        {
                            "event": "stress_replay_progress",
                            "completed": completed,
                            "total": len(work),
                            "candidates": candidates,
                            "errors": errors,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    print(
        json.dumps(
            {
                "event": "stress_replay_complete",
                "completed": completed,
                "candidates": candidates,
                "errors": errors,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
