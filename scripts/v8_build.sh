#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/v8_build.sh [--configure-only] [PROFILE]

Builds a profile from config/v8-builds.toml against the exact V8 revision in
config/v8-target.toml. PROFILE defaults to release_symbolized.
EOF
}

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
local_root=${FUZZYNTH_LOCAL_ROOT:-"$repo_root/.local"}
v8_root="$local_root/v8-workspace/v8"
depot_tools="$local_root/depot_tools"
target_config="$repo_root/config/v8-target.toml"
build_config="$repo_root/config/v8-builds.toml"
profile=release_symbolized
configure_only=false

while (($#)); do
  case "$1" in
    --configure-only)
      configure_only=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -* )
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      profile=$1
      ;;
  esac
  shift
done

if [[ ! "$profile" =~ ^[a-z0-9_]+$ ]]; then
  printf 'Invalid profile name: %s\n' "$profile" >&2
  exit 2
fi
if [[ ! -d "$v8_root/.git" || ! -d "$depot_tools/.git" ]]; then
  printf 'V8 checkout is missing; run scripts/v8_checkout.sh first.\n' >&2
  exit 1
fi
if [[ ! -f "$depot_tools/python3_bin_reldir.txt" ]]; then
  (
    cd "$depot_tools"
    ./ensure_bootstrap
  )
fi
if [[ ! -f "$depot_tools/python3_bin_reldir.txt" ]]; then
  printf 'depot_tools Python bootstrap did not complete.\n' >&2
  exit 1
fi

mapfile -t target_data < <(python3 - "$target_config" <<'PY'
from pathlib import Path
import sys
import tomllib

with Path(sys.argv[1]).open("rb") as stream:
    config = tomllib.load(stream)
print(config["v8_revision"])
print(config["chrome_version"])
PY
)
target_revision=${target_data[0]}
chrome_version=${target_data[1]}
actual_revision=$(git -C "$v8_root" rev-parse HEAD)
if [[ "$actual_revision" != "$target_revision" ]]; then
  printf 'V8 revision mismatch: expected %s, got %s\n' \
    "$target_revision" "$actual_revision" >&2
  exit 1
fi

mapfile -t profile_data < <(python3 - "$build_config" "$profile" <<'PY'
from pathlib import Path
import sys
import tomllib

with Path(sys.argv[1]).open("rb") as stream:
    config = tomllib.load(stream)
try:
    profile = config["profiles"][sys.argv[2]]
except KeyError:
    choices = ", ".join(sorted(config.get("profiles", {})))
    raise SystemExit(f"Unknown build profile {sys.argv[2]!r}; choose from: {choices}")

for field in ("output_dir", "base_builder", "target"):
    value = profile.get(field)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Profile field {field!r} must be a non-empty string")
    print(value)
for value in profile.get("extra_gn_args", []):
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise SystemExit("Each extra GN argument must be a non-empty, whitespace-free string")
    print(value)
PY
)
output_dir=${profile_data[0]}
base_builder=${profile_data[1]}
target=${profile_data[2]}
extra_gn_args=("${profile_data[@]:3}")
out_path="$v8_root/out.gn/$output_dir"

export PATH="$depot_tools:$PATH"
(
  cd "$v8_root"
  tools/dev/v8gen.py gen -b "$base_builder" "$output_dir" -- \
    "${extra_gn_args[@]}"
  # Use the GN binary pinned by the V8 checkout. The depot_tools `gn` wrapper
  # is a Chromium convenience and can require a separate Python bootstrap.
  buildtools/linux64/gn gen "out.gn/$output_dir" --check
)

printf 'profile=%s\n' "$profile"
printf 'chrome_version=%s\n' "$chrome_version"
printf 'v8_revision=%s\n' "$actual_revision"
printf 'gn_args=%s\n' "$out_path/args.gn"

if [[ "$configure_only" == true ]]; then
  printf 'configured_only=true\n'
  exit 0
fi

build_jobs=${FUZZYNTH_BUILD_JOBS:-8}
if [[ ! "$build_jobs" =~ ^[1-9][0-9]*$ ]]; then
  printf 'FUZZYNTH_BUILD_JOBS must be a positive integer.\n' >&2
  exit 2
fi
(
  cd "$v8_root"
  autoninja -j "$build_jobs" -C "$out_path" "$target"
)

manifest_dir="$local_root/build-manifests"
manifest_path="$manifest_dir/$profile-$actual_revision.json"
mkdir -p "$manifest_dir"
python3 - \
  "$manifest_path" "$profile" "$chrome_version" "$actual_revision" \
  "$depot_tools" "$out_path" "$target" "$base_builder" "$build_jobs" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

(
    manifest_path,
    profile,
    chrome_version,
    v8_revision,
    depot_tools,
    out_path,
    target,
    base_builder,
    build_jobs,
) = sys.argv[1:]
binary = Path(out_path) / target
run_cwd = Path(out_path).parents[1]

def capture(command):
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=run_cwd,
    ).stdout.strip()

digest = hashlib.sha256()
with binary.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)

help_result = subprocess.run(
    [str(binary), "--help"],
    check=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd=run_cwd,
)
elf_notes = capture(["readelf", "-n", str(binary)])
build_id_match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", elf_notes)
gn_args = (Path(out_path) / "args.gn").read_text(encoding="utf-8")

clang = Path(out_path).parents[1] / "third_party/llvm-build/Release+Asserts/bin/clang"
if not clang.exists():
    clang = Path(out_path).parents[2] / "third_party/llvm-build/Release+Asserts/bin/clang"

manifest = {
    "schema_version": 1,
    "built_at": datetime.now(timezone.utc).isoformat(),
    "profile": profile,
    "base_builder": base_builder,
    "build_jobs": int(build_jobs),
    "chrome_version": chrome_version,
    "v8_revision": v8_revision,
    "depot_tools_revision": capture(["git", "-C", depot_tools, "rev-parse", "HEAD"]),
    "target": target,
    "binary": str(binary),
    "binary_sha256": digest.hexdigest(),
    "binary_size": binary.stat().st_size,
    "elf_build_id": build_id_match.group(1) if build_id_match else None,
    "d8_version": capture([str(binary), "--version"]),
    "help_sha256": hashlib.sha256(
        help_result.stdout + b"\0stderr\0" + help_result.stderr
    ).hexdigest(),
    "gn_args": gn_args,
    "gn_args_sha256": hashlib.sha256(gn_args.encode("utf-8")).hexdigest(),
}
if clang.exists():
    manifest["compiler"] = capture([str(clang), "--version"]).splitlines()[0]

destination = Path(manifest_path)
temporary = destination.with_suffix(destination.suffix + ".tmp")
temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(destination)
print(f"binary={binary}")
print(f"binary_sha256={manifest['binary_sha256']}")
print(f"manifest={destination}")
PY
