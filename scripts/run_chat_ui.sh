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

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "warning: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set." >&2
  echo "         the clarification rephraser will fail until one is provided." >&2
fi

# The VP agent itself must already be running (./scripts/run_api.sh on :8000).
export VP_CHAT_BACKEND="${VP_CHAT_BACKEND:-http://127.0.0.1:8000/vp/build}"

echo "chat UI    -> http://${VP_CHAT_HOST:-127.0.0.1}:${VP_CHAT_PORT:-8001}"
echo "vp backend -> ${VP_CHAT_BACKEND}"

exec .venv/bin/uvicorn vp_agent.chat_api:app \
  --host "${VP_CHAT_HOST:-127.0.0.1}" \
  --port "${VP_CHAT_PORT:-8001}"
