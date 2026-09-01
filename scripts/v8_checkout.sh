#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
local_root=${FUZZYNTH_LOCAL_ROOT:-"$repo_root/.local"}
depot_tools="$local_root/depot_tools"
workspace="$local_root/v8-workspace"

mkdir -p "$local_root" "$workspace"

if [[ ! -d "$depot_tools/.git" ]]; then
  git clone \
    https://chromium.googlesource.com/chromium/tools/depot_tools.git \
    "$depot_tools"
fi

export PATH="$depot_tools:$PATH"

if [[ ! -d "$workspace/v8/.git" ]]; then
  (
    cd "$workspace"
    fetch v8
  )
else
  (
    cd "$workspace/v8"
    gclient sync --with_branch_heads
  )
fi

printf 'v8_checkout=%s\n' "$workspace/v8"
