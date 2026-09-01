#!/usr/bin/env python3
"""Run fixed, benign smoke checks against a pinned local d8 build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any


JS_SMOKE = """
const values = [1, 2, 3].map(x => x * 2).join(',');
if (values !== '2,4,6') throw new Error('unexpected JS result');
print('js-ok');
"""

WASM_SMOKE = """
const bytes = new Uint8Array([
  0x00,0x61,0x73,0x6d,0x01,0x00,0x00,0x00,
  0x01,0x05,0x01,0x60,0x00,0x01,0x7f,
  0x03,0x02,0x01,0x00,
  0x07,0x07,0x01,0x03,0x72,0x75,0x6e,0x00,0x00,
  0x0a,0x06,0x01,0x04,0x00,0x41,0x2a,0x0b,
]);
const instance = new WebAssembly.Instance(new WebAssembly.Module(bytes));
if (instance.exports.run() !== 42) throw new Error('unexpected Wasm result');
print('wasm-ok');
"""

NATIVES_SMOKE = """
function addOne(value) { return value + 1; }
%PrepareFunctionForOptimization(addOne);
addOne(1);
%OptimizeFunctionOnNextCall(addOne);
if (addOne(2) !== 3) throw new Error('unexpected optimized result');
print('natives-ok');
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(binary: Path, arguments: list[str], expected: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(binary), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "reason": "timeout"}

    passed = completed.returncode == 0 and expected in completed.stdout
    result: dict[str, Any] = {
        "passed": passed,
        "returncode": completed.returncode,
    }
    if not passed:
        result["stdout_tail"] = completed.stdout[-500:]
        result["stderr_tail"] = completed.stderr[-500:]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", nargs="?", default="release_symbolized")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    local_root = repo_root / ".local"
    with (repo_root / "config/v8-target.toml").open("rb") as stream:
        target_config = tomllib.load(stream)
    with (repo_root / "config/v8-builds.toml").open("rb") as stream:
        build_config = tomllib.load(stream)

    try:
        profile = build_config["profiles"][args.profile]
    except KeyError:
        parser.error(f"unknown build profile: {args.profile}")
    binary = (
        local_root
        / "v8-workspace/v8/out.gn"
        / profile["output_dir"]
        / profile["target"]
    )
    if not binary.is_file():
        parser.error(f"d8 binary does not exist for profile {args.profile}")

    v8_root = local_root / "v8-workspace/v8"
    revision = subprocess.run(
        ["git", "-C", str(v8_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != target_config["v8_revision"]:
        parser.error("local V8 revision does not match config/v8-target.toml")

    version = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    help_result = subprocess.run(
        [str(binary), "--help"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    report = {
        "schema_version": 1,
        "profile": args.profile,
        "chrome_version": target_config["chrome_version"],
        "v8_revision": revision,
        "d8_version": version,
        "binary_sha256": _sha256(binary),
        "help_sha256": hashlib.sha256(
            help_result.stdout + b"\0stderr\0" + help_result.stderr
        ).hexdigest(),
        "checks": {
            "javascript": _run(binary, ["-e", JS_SMOKE], "js-ok"),
            "webassembly": _run(binary, ["-e", WASM_SMOKE], "wasm-ok"),
            "natives_syntax": _run(
                binary,
                ["--allow-natives-syntax", "-e", NATIVES_SMOKE],
                "natives-ok",
            ),
        },
    }
    report["passed"] = all(check["passed"] for check in report["checks"].values())
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.report)
    sys.stdout.write(encoded)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
