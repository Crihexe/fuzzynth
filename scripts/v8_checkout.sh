#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
local_root=${FUZZYNTH_LOCAL_ROOT:-"$repo_root/.local"}
depot_tools="$local_root/depot_tools"
workspace="$local_root/v8-workspace"
target_config="$repo_root/config/v8-target.toml"

target_revision=$(python3 - "$target_config" <<'PY'
from pathlib import Path
import sys
import tomllib

with Path(sys.argv[1]).open("rb") as stream:
    config = tomllib.load(stream)
revision = config.get("v8_revision", "")
if not isinstance(revision, str) or len(revision) != 40:
    raise SystemExit("config/v8-target.toml does not contain a resolved V8 revision")
print(revision)
PY
)

mkdir -p "$local_root" "$workspace"

if [[ ! -d "$depot_tools/.git" ]]; then
  git clone \
    https://chromium.googlesource.com/chromium/tools/depot_tools.git \
    "$depot_tools"
fi

export PATH="$depot_tools:$PATH"
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=http.version
export GIT_CONFIG_VALUE_0=HTTP/1.1

if [[ ! -f "$workspace/.gclient" ]]; then
  (
    cd "$workspace"
    gclient config --spec 'solutions = [
      {
        "name": "v8",
        "url": "https://chromium.googlesource.com/v8/v8.git",
        "deps_file": "DEPS",
        "custom_deps": {},
      },
    ]'
  )
fi

(
  cd "$workspace"
  gclient sync --no-history --revision "v8@$target_revision"
)

actual_revision=$(git -C "$workspace/v8" rev-parse HEAD)
if [[ "$actual_revision" != "$target_revision" ]]; then
  printf 'V8 revision mismatch: expected %s, got %s\n' \
    "$target_revision" "$actual_revision" >&2
  exit 1
fi

printf 'v8_checkout=%s\n' "$workspace/v8"
printf 'v8_revision=%s\n' "$actual_revision"
