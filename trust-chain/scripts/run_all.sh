#!/usr/bin/env bash
# Runs the whole pipeline from a fresh clone. Requires uv on PATH. Fails loudly.
set -euo pipefail
command -v uv >/dev/null || { echo "uv is not on PATH. Install it first: https://docs.astral.sh/uv/" >&2; exit 1; }
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python 3.12 python "$repo/scripts/run_all.py"
