# Cloud Agent SDK Setup

This project uses the Claude Agent SDK as the cloud-facing agent runtime for the
VP Agent service. The deployed API does not call a separate Python rules engine
to build final condition syntax. It starts a Claude Agent SDK session, exposes
the VP tools through an in-process MCP server, and lets the orchestrator call the
tools under the invariants in `AGENTS.md`.

In this document, "Cloud Agent SDK" means the implemented Claude Agent SDK
runtime in this repository.

## What Is Implemented

The SDK integration is in `vp_agent/orchestrator.py`.

The application creates `ClaudeAgentOptions` with:

- `setting_sources=["project"]`, so project memory from `.claude/` is loaded;
- `cwd` set to the repository root;
- the orchestrator model from `VP_ORCHESTRATOR_MODEL`;
- subagent definitions for extractor, resolver, and verifier;
- an in-process MCP server named `vp`;
- allowed VP MCP tools for retrieval, routing, seed selection, rendering, and
  validation;
- hooks that enforce audit and render-time safety;
- `Bash` disallowed for the agent.

The request path is:

1. The FastAPI endpoint receives a VP build request.
2. `vp_agent.orchestrator.run_request()` creates a `ClaudeSDKClient`.
3. The SDK starts a Claude Agent session with this repository as the project
   context.
4. Claude calls the local MCP tools exposed by `vp_agent.server`.
5. `render_condition` emits the only legal `PARENT_CONDITION` syntax.
6. `validate_rule` checks the result before the API returns it.

## Required Cloud Secrets

Claude authentication and Langfuse authentication are separate.

For Claude/Anthropic access, set:

```bash
ANTHROPIC_API_KEY="sk-ant-..."
```

This is the credential that lets the SDK-backed service connect to Claude in the
cloud. The key should come from the Anthropic Console and should be stored in the
cloud provider's secret manager, not committed to this repository.

The Claude Agent SDK also supports Claude configuration directories used by the
Claude Code runtime. The relevant environment variable is:

```bash
CLAUDE_CONFIG_DIR="/path/to/claude/config"
```

For production, prefer a service-owned `ANTHROPIC_API_KEY`. Do not depend on a
developer's local Claude login or copy personal `~/.claude` credentials into a
cloud container unless that is an explicitly approved operational pattern.

Optional SDK identity:

```bash
CLAUDE_AGENT_SDK_CLIENT_APP="vp-agent/0.1.0"
```

This identifies the application in SDK client metadata.

## Required Application Environment

The service also needs the VP Agent configuration:

```bash
VP_DATA_DIR="/path/to/vp/data"
VP_ORCHESTRATOR_MODEL="claude-sonnet-5"
VP_SUBAGENT_MODEL="claude-haiku-4-5-20251001"
VP_MAX_TURNS="25"
```

`VP_DATA_DIR` is important. The local default points to:

```text
/Users/sachinpb/PycharmProjects/Virtual_profile_agent/data
```

That path will not exist in most cloud deployments. The cloud runtime must
either mount the same data files or set `VP_DATA_DIR` to the deployed data
location.

The cloud image or runtime must include:

- this repository;
- `.claude/CLAUDE.md`;
- `.claude/agents/`;
- `.claude/skills/`;
- the Python package dependencies from `pyproject.toml`;
- the VP data files referenced by `VP_DATA_DIR`.

## Running In Cloud

For local development, `scripts/run_api.sh` starts Uvicorn with reload on
`127.0.0.1`.

For cloud deployment, run without reload and bind to all interfaces:

```bash
uvicorn vp_agent.api:app --host 0.0.0.0 --port "${PORT:-8000}"
```

Before accepting traffic, verify that imports and SDK options can be built:

```bash
PYTHONPATH=. python scripts/smoke_sdk.py
```

To run a live Claude request from the deployment environment:

```bash
PYTHONPATH=. python scripts/smoke_sdk.py --live
```

The live smoke test requires Claude Agent SDK authentication, normally through
`ANTHROPIC_API_KEY` in the cloud environment.

## Langfuse, Not Linefuse

If "Linefuse" means Langfuse, then the same Langfuse API information is useful
for observability, but it is not enough to connect Claude to the cloud.

Langfuse credentials send traces to Langfuse:

```bash
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_SECRET_KEY="sk-lf-..."
LANGFUSE_BASE_URL="https://us.cloud.langfuse.com"
LANGFUSE_TRACING_ENVIRONMENT="production"
```

Claude credentials authenticate the model call:

```bash
ANTHROPIC_API_KEY="sk-ant-..."
```

Both sets can be present in the same cloud service, but they do different jobs:

- `ANTHROPIC_API_KEY` authorizes Claude API usage.
- `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` authorize trace ingestion.
- `LANGFUSE_BASE_URL` must match the Langfuse region that owns the keys.
- Langfuse cannot replace the Anthropic key.
- The Anthropic key cannot replace Langfuse credentials.

This repository uses the official OpenTelemetry path for Langfuse:

- `openinference-instrumentation-claude-agent-sdk` instruments the Claude Agent
  SDK;
- `opentelemetry-exporter-otlp-proto-http` exports spans;
- `vp_agent.observability.configure_langfuse()` builds the OTLP endpoint and
  auth headers from the Langfuse environment variables.

The default OTLP endpoint becomes:

```text
${LANGFUSE_BASE_URL}/api/public/otel/v1/traces
```

For example, if `LANGFUSE_BASE_URL` is `https://us.cloud.langfuse.com`, traces
are sent to:

```text
https://us.cloud.langfuse.com/api/public/otel/v1/traces
```

## Checking Observability

The API exposes a safe status endpoint that does not return secrets:

```bash
curl http://127.0.0.1:8000/observability
```

In cloud, replace the host with the service URL.

The response shows whether Langfuse is enabled, whether public/secret keys are
present, the configured base URL, OTLP endpoint settings, and the last
instrumentation error if one occurred.

To disable Langfuse without removing secrets:

```bash
LANGFUSE_ENABLED=false
```

Local development without Langfuse credentials is a no-op. The app should still
run, but traces will not be exported.

## Minimal Secret Matrix

| Purpose | Required variables | Notes |
| --- | --- | --- |
| Claude cloud access | `ANTHROPIC_API_KEY` | Required for live SDK calls in cloud. |
| Project memory | `.claude/` files in image | Required because `setting_sources=["project"]`. |
| VP data | `VP_DATA_DIR` | Must point to deployed metadata and seed data. |
| Langfuse tracing | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Optional for runtime behavior, required for traces. |
| Model override | `VP_ORCHESTRATOR_MODEL`, `VP_SUBAGENT_MODEL` | Optional; defaults come from `vp_agent/config.py`. |
| Cloud port | `PORT` or `VP_API_PORT` | Use the platform's expected port. |

## Developer Handoff

Give the cloud developer these points:

1. Deploy the repository with the `.claude` directory included.
2. Install dependencies from `pyproject.toml`.
3. Provide `ANTHROPIC_API_KEY` as a cloud secret.
4. Provide `VP_DATA_DIR` and ensure the data exists at that path.
5. Start the API with Uvicorn on `0.0.0.0`.
6. Add Langfuse credentials only if tracing is desired.
7. Confirm `/observability` after startup.
8. Run `PYTHONPATH=. python scripts/smoke_sdk.py --live` from the cloud runtime
   to verify the Agent SDK can reach Claude.

## Common Misconfigurations

- Setting only Langfuse keys and expecting Claude calls to work. Langfuse is
  observability; it does not authenticate Claude.
- Using the local `scripts/run_api.sh` command in production. It binds to
  `127.0.0.1` and enables reload.
- Forgetting `.claude/` in the deployed image. The SDK is configured to load
  project memory and agent definitions from this repository.
- Leaving `VP_DATA_DIR` at the local macOS default path.
- Using Langfuse keys from one region with another region's base URL.
- Treating render failures as prompt problems. Per project rules, render or
  grammar failures are code bugs, not syntax for the model to patch manually.

## Documentation Basis

The current Claude Agent SDK documentation describes:

- `ClaudeSDKClient` as the async client used to query and receive responses;
- `ClaudeAgentOptions` as the configuration surface for model, project setting
  sources, tools, MCP servers, hooks, sessions, and environment variables;
- in-process SDK MCP servers as a supported way to expose local Python tools;
- OpenTelemetry trace-context propagation from the SDK wrapper;
- Claude configuration via `CLAUDE_CONFIG_DIR` and environment passed to the
  Claude Code subprocess.

The current Langfuse documentation for Claude Agent SDK integration describes
using both Langfuse credentials and `ANTHROPIC_API_KEY`: Langfuse keys for trace
ingestion, Anthropic key for Claude access.
