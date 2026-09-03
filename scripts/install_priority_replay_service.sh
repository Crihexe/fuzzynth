#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_unit="$repo_root/deploy/fuzzynth-priority-replay.service"
target_unit=/etc/systemd/system/fuzzynth-priority-replay.service

if [[ $repo_root != /root/fuzzynth ]]; then
  echo "fuzzynth: priority replay unit is pinned to /root/fuzzynth" >&2
  exit 1
fi
if [[ ! -f $source_unit ]]; then
  echo "fuzzynth: priority replay service unit is missing" >&2
  exit 1
fi
if [[ -e $target_unit ]] && ! cmp -s "$source_unit" "$target_unit"; then
  echo "fuzzynth: refusing to overwrite a different priority replay unit" >&2
  exit 1
fi

install -m 0644 "$source_unit" "$target_unit"
systemctl daemon-reload
systemctl start --no-block fuzzynth-priority-replay.service
systemctl --no-pager --full status fuzzynth-priority-replay.service
