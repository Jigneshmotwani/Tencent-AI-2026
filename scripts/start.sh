#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
uv sync --frozen
if [ ! -d frontend/node_modules ]; then (cd frontend && npm ci --ignore-scripts); fi
# Windows (Git Bash) venvs use Scripts/, POSIX venvs use bin/.
if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
exec "$PY" -m backend.app.localstart "$@"
