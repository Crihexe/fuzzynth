#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
local_root=${FUZZYNTH_LOCAL_ROOT:-"$repo_root/.local"}
v8_root="$local_root/v8-workspace/v8"
target_config="$repo_root/config/v8-target.toml"

if [[ ! -d "$v8_root/.git" ]]; then
  printf 'V8 checkout is missing; run scripts/v8_checkout.sh first.\n' >&2
  exit 1
fi

target_revision=$(python3 - "$target_config" <<'PY'
from pathlib import Path
import sys
import tomllib

with Path(sys.argv[1]).open("rb") as stream:
    print(tomllib.load(stream)["v8_revision"])
PY
)
actual_revision=$(git -C "$v8_root" rev-parse HEAD)
if [[ "$actual_revision" != "$target_revision" ]]; then
  printf 'V8 revision mismatch: expected %s, got %s\n' \
    "$target_revision" "$actual_revision" >&2
  exit 1
fi

if ! command -v file >/dev/null 2>&1; then
  printf "The upstream installer requires the 'file' package.\n" >&2
  printf "Install it first (for Debian/Ubuntu: apt-get install file).\n" >&2
  exit 1
fi

exec "$v8_root/build/install-build-deps.sh" \
  --no-prompt \
  --no-arm \
  --no-chromeos-fonts \
  --no-syms
