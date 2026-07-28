#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d .git ]]; then
  git init
fi
cp -n .env.example .env || true
uv sync --extra dev
uv run pytest -q
uv run python -m evoeventmem.cli smoke
echo "Bootstrap complete. Next: python scripts/taskctl.py show M01"
