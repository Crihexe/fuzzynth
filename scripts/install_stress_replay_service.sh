#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_unit="$repo_root/deploy/fuzzynth-stress-replay.service"
target_unit=/etc/systemd/system/fuzzynth-stress-replay.service

if [[ $repo_root != /root/fuzzynth ]]; then
  echo "fuzzynth: stress replay unit is pinned to /root/fuzzynth" >&2
  exit 1
fi
if [[ ! -f $source_unit ]]; then
  echo "fuzzynth: stress replay service unit is missing" >&2
  exit 1
fi
if [[ -e $target_unit ]] && ! cmp -s "$source_unit" "$target_unit"; then
  echo "fuzzynth: refusing to overwrite a different stress replay unit" >&2
  exit 1
fi

install -m 0644 "$source_unit" "$target_unit"
systemctl daemon-reload
systemctl start --no-block fuzzynth-stress-replay.service
systemctl --no-pager --full status fuzzynth-stress-replay.service
