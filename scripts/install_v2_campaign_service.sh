#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_unit="$repo_root/deploy/fuzzynth-v2-campaign.service"
target_unit=/etc/systemd/system/fuzzynth-v2-campaign.service
cooldown_source="$repo_root/deploy/fuzzynth-spark-cooldown.service"
cooldown_target=/etc/systemd/system/fuzzynth-spark-cooldown.service
timer_source="$repo_root/deploy/fuzzynth-spark-cooldown.timer"
timer_target=/etc/systemd/system/fuzzynth-spark-cooldown.timer
fallback_source="$repo_root/deploy/fuzzynth-spark-fallback.service"
fallback_target=/etc/systemd/system/fuzzynth-spark-fallback.service
fallback_timer_source="$repo_root/deploy/fuzzynth-spark-fallback.timer"
fallback_timer_target=/etc/systemd/system/fuzzynth-spark-fallback.timer
legacy_source="$repo_root/deploy/fuzzynth-spark-campaign.service"
legacy_target=/etc/systemd/system/fuzzynth-spark-campaign.service
state_root="$repo_root/state"

if [[ ! -f "$source_unit" ]]; then
  echo "fuzzynth: v2 campaign service unit is missing" >&2
  exit 1
fi
if [[ ! -f $cooldown_source || ! -f $timer_source || ! -f $fallback_source || ! -f $fallback_timer_source ]]; then
  echo "fuzzynth: Spark auxiliary units are missing" >&2
  exit 1
fi
if [[ $repo_root != /root/fuzzynth ]]; then
  echo "fuzzynth: service unit is pinned to /root/fuzzynth" >&2
  exit 1
fi
if [[ -e $target_unit ]] && ! cmp -s "$source_unit" "$target_unit" && ! grep -Fqx 'SyslogIdentifier=fuzzynth-v2-campaign' "$target_unit"; then
  echo "fuzzynth: refusing to overwrite a different installed v2 unit" >&2
  exit 1
fi
if [[ -e $fallback_target ]] && ! cmp -s "$fallback_source" "$fallback_target"; then
  echo "fuzzynth: refusing to overwrite a different fallback service" >&2
  exit 1
fi
if [[ -e $fallback_timer_target ]] && ! cmp -s "$fallback_timer_source" "$fallback_timer_target"; then
  echo "fuzzynth: refusing to overwrite a different fallback timer" >&2
  exit 1
fi
if [[ -e $cooldown_target ]] && ! cmp -s "$cooldown_source" "$cooldown_target"; then
  echo "fuzzynth: refusing to overwrite a different cooldown service" >&2
  exit 1
fi
if [[ -e $timer_target ]] && ! cmp -s "$timer_source" "$timer_target"; then
  echo "fuzzynth: refusing to overwrite a different cooldown timer" >&2
  exit 1
fi
if [[ -e $legacy_target ]] && ! cmp -s "$legacy_source" "$legacy_target"; then
  echo "fuzzynth: refusing to stop an unrecognized legacy unit" >&2
  exit 1
fi

install -d -m 0700 "$state_root"
install -m 0644 "$source_unit" "$target_unit"
install -m 0644 "$cooldown_source" "$cooldown_target"
install -m 0644 "$timer_source" "$timer_target"
install -m 0644 "$fallback_source" "$fallback_target"
install -m 0644 "$fallback_timer_source" "$fallback_timer_target"
systemctl daemon-reload
if [[ -e $legacy_target ]]; then
  systemctl disable --now fuzzynth-spark-campaign.service
fi
systemctl enable fuzzynth-v2-campaign.service
if systemctl is-active --quiet fuzzynth-v2-campaign.service; then
  # Reload Python modules and the ExecStart worker matrix after an in-place
  # upgrade. SIGTERM is handled cooperatively, so in-flight turns finish first.
  systemctl restart fuzzynth-v2-campaign.service
else
  systemctl start fuzzynth-v2-campaign.service
fi
# The current campaign is deliberately custom-Luna-only. Keep the Spark
# quota/cooldown automation installed for future experiments but inactive.
systemctl disable --now fuzzynth-spark-cooldown.timer
systemctl disable --now fuzzynth-spark-fallback.timer
systemctl --no-pager --full status fuzzynth-v2-campaign.service
