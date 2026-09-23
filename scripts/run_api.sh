#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/uvicorn" ]; then
  echo "uvicorn is not installed in .venv. Run ./scripts/setup_dev.sh first." >&2
  exit 1
fi
# Credentials come from the environment or from .env (gitignored).
# Never hardcode a token here: this file IS tracked in git.
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "warning: no CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN is set." >&2
fi

#export VP_ORCHESTRATOR_MODEL="${VP_ORCHESTRATOR_MODEL:-claude-haiku-4-5-20251001}"
export VP_ORCHESTRATOR_MODEL="${VP_ORCHESTRATOR_MODEL:-claude-sonnet-5}"
export VP_CONSOLE_TRACE="${VP_CONSOLE_TRACE:-1}"

exec .venv/bin/uvicorn vp_agent.api:app \
  --host "${VP_API_HOST:-127.0.0.1}" \
  --port "${VP_API_PORT:-8000}"

#exec .venv/bin/uvicorn vp_agent.api:app --host "${VP_API_HOST:-127.0.0.1}" --port "${VP_API_PORT:-8000}" --reload
#VP_VARIANT=optimized .venv/bin/python -m uvicorn \
#  vp_agent.api:app \
#  --host 127.0.0.1 \
#  --port 8001