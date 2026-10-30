#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEFAULT_CODEX_PYTHON="/Users/sachinpb/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ -z "${PYTHON_BIN:-}" ] && [ -x "$DEFAULT_CODEX_PYTHON" ]; then
  PYTHON_BIN="$DEFAULT_CODEX_PYTHON"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# Activate venv across POSIX and Windows bash (Git Bash / WSL)
if [ -f ".venv/bin/activate" ]; then
  . .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
  . .venv/Scripts/activate
else
  echo "Could not find venv activate script in .venv/bin or .venv/Scripts" >&2
  exit 1
fi
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

python - <<'PY'
import claude_agent_sdk
print("claude_agent_sdk import ok")
PY

echo "Development environment ready: $ROOT_DIR/.venv"
