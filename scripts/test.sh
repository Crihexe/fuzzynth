#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m unittest discover -s "$repo_root/tests" -v
python3 -m compileall -q "$repo_root/src" "$repo_root/scripts"
