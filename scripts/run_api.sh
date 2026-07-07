#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/uvicorn" ]; then
  echo "uvicorn is not installed in .venv. Run ./scripts/setup_dev.sh first." >&2
  exit 1
fi

exec .venv/bin/uvicorn vp_agent.api:app --host "${VP_API_HOST:-127.0.0.1}" --port "${VP_API_PORT:-8000}" --reload
