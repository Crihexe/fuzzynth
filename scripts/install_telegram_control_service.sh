#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_unit="$repo_root/deploy/fuzzynth-telegram-control.service"
target_unit=/etc/systemd/system/fuzzynth-telegram-control.service
state_root="$repo_root/state"

if [[ ! -f "$source_unit" ]]; then
  echo "fuzzynth: service unit is missing" >&2
  exit 1
fi
if [[ $repo_root != /root/fuzzynth ]]; then
  echo "fuzzynth: service unit is pinned to /root/fuzzynth" >&2
  exit 1
fi
if [[ -e $target_unit ]] && ! cmp -s "$source_unit" "$target_unit"; then
  echo "fuzzynth: refusing to overwrite a different installed unit" >&2
  exit 1
fi

install -d -m 0700 "$state_root"
install -m 0644 "$source_unit" "$target_unit"
systemctl daemon-reload
systemctl enable --now fuzzynth-telegram-control.service
systemctl --no-pager --full status fuzzynth-telegram-control.service
