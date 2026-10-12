#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
uv sync --frozen
if [ ! -d frontend/node_modules ]; then (cd frontend && npm ci --ignore-scripts); fi
exec .venv/bin/python -m backend.app.localstart "$@"
