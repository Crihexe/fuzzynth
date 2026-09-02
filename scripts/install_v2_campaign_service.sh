#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_unit="$repo_root/deploy/fuzzynth-v2-campaign.service"
target_unit=/etc/systemd/system/fuzzynth-v2-campaign.service
legacy_source="$repo_root/deploy/fuzzynth-spark-campaign.service"
legacy_target=/etc/systemd/system/fuzzynth-spark-campaign.service
state_root="$repo_root/state"

if [[ ! -f "$source_unit" ]]; then
  echo "fuzzynth: v2 campaign service unit is missing" >&2
  exit 1
fi
if [[ $repo_root != /root/fuzzynth ]]; then
  echo "fuzzynth: service unit is pinned to /root/fuzzynth" >&2
  exit 1
fi
if [[ -e $target_unit ]] && ! cmp -s "$source_unit" "$target_unit"; then
  echo "fuzzynth: refusing to overwrite a different installed v2 unit" >&2
  exit 1
fi
if [[ -e $legacy_target ]] && ! cmp -s "$legacy_source" "$legacy_target"; then
  echo "fuzzynth: refusing to stop an unrecognized legacy unit" >&2
  exit 1
fi

install -d -m 0700 "$state_root"
install -m 0644 "$source_unit" "$target_unit"
systemctl daemon-reload
if [[ -e $legacy_target ]]; then
  systemctl disable --now fuzzynth-spark-campaign.service
fi
systemctl enable --now fuzzynth-v2-campaign.service
systemctl --no-pager --full status fuzzynth-v2-campaign.service
