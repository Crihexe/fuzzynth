#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
local_root=${FUZZYNTH_LOCAL_ROOT:-"$repo_root/.local"}
v8_root="$local_root/v8-workspace/v8"
build_config="$repo_root/config/v8-builds.toml"
target_config="$repo_root/config/v8-target.toml"
profile=${1:-release_symbolized}

if [[ ! "$profile" =~ ^[a-z0-9_]+$ ]]; then
  printf 'Invalid profile name: %s\n' "$profile" >&2
  exit 2
fi

mapfile -t values < <(python3 - "$build_config" "$target_config" "$profile" <<'PY'
from pathlib import Path
import sys
import tomllib

with Path(sys.argv[1]).open("rb") as stream:
    builds = tomllib.load(stream)
with Path(sys.argv[2]).open("rb") as stream:
    target = tomllib.load(stream)
try:
    profile = builds["profiles"][sys.argv[3]]
except KeyError:
    raise SystemExit(f"Unknown build profile: {sys.argv[3]}")
print(profile["output_dir"])
print(profile["target"])
print(target["v8_revision"])
PY
)
output_dir=${values[0]}
target=${values[1]}
revision=${values[2]}
binary="$v8_root/out.gn/$output_dir/$target"
if [[ ! -x "$binary" ]]; then
  printf 'Built target is missing: %s\n' "$binary" >&2
  exit 1
fi
actual_revision=$(git -C "$v8_root" rev-parse HEAD)
if [[ "$actual_revision" != "$revision" ]]; then
  printf 'V8 revision mismatch: expected %s, got %s\n' \
    "$revision" "$actual_revision" >&2
  exit 1
fi

staging=$(mktemp -d -p "$local_root" worker-context.XXXXXXXX)
cleanup() {
  rm -rf -- "$staging"
}
trap cleanup EXIT
rootfs="$staging/rootfs"
install -d -m 0755 "$rootfs/opt/fuzzynth" "$rootfs/work"
install -m 0755 "$binary" "$rootfs/opt/fuzzynth/d8"
runtime_binaries=("$binary")

if [[ "$profile" == asan ]]; then
  symbolizer="$v8_root/third_party/llvm-build/Release+Asserts/bin/llvm-symbolizer"
  if [[ ! -x "$symbolizer" ]]; then
    printf 'ASAN profile requires llvm-symbolizer: %s\n' "$symbolizer" >&2
    exit 1
  fi
  install -m 0755 "$symbolizer" "$rootfs/opt/fuzzynth/llvm-symbolizer"
  runtime_binaries+=("$symbolizer")
fi

for data_file in icudtl.dat snapshot_blob.bin; do
  if [[ -f "$(dirname "$binary")/$data_file" ]]; then
    install -m 0644 \
      "$(dirname "$binary")/$data_file" \
      "$rootfs/opt/fuzzynth/$data_file"
  fi
done

mapfile -t libraries < <(
  for runtime_binary in "${runtime_binaries[@]}"; do
    ldd "$runtime_binary"
  done |
    awk '/=> \// {print $3} /^[[:space:]]*\// {print $1}' |
    sort -u
)
for library in "${libraries[@]}"; do
  if [[ ! -f "$library" ]]; then
    printf 'Unable to package dynamic library: %s\n' "$library" >&2
    exit 1
  fi
  case "$library" in
    "$v8_root"/*)
      install -m 0755 "$library" "$rootfs/opt/fuzzynth/$(basename "$library")"
      ;;
    *)
      install -D -m 0755 "$library" "$rootfs$library"
      ;;
  esac
done

install -m 0644 "$repo_root/docker/d8-worker/Dockerfile" "$staging/Dockerfile"
short_revision=${revision:0:12}
image_tag="fuzzynth/d8-$profile:$short_revision"
docker build --network=none --tag "$image_tag" "$staging"

manifest_dir="$local_root/worker-images"
mkdir -p "$manifest_dir"
docker image inspect "$image_tag" > "$manifest_dir/$profile-$revision.json"
image_id=$(docker image inspect --format '{{.Id}}' "$image_tag")

smoke_output=$(docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 64 \
  --memory 256m \
  --memory-swap 256m \
  --cpus 1 \
  "$image_id" \
  -e "print('worker-ok')")
if [[ "$smoke_output" != "worker-ok" ]]; then
  printf 'Packaged worker smoke test failed.\n' >&2
  exit 1
fi

printf 'image_tag=%s\n' "$image_tag"
printf 'image_id=%s\n' "$image_id"
printf 'worker_smoke=passed\n'
printf 'manifest=%s\n' "$manifest_dir/$profile-$revision.json"
