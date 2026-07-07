# VP Agent

Virtual Profile Builder v2 converts telecom audience descriptions into validated
`PARENT_CONDITION` expressions.

This implementation follows the Claude Agent SDK architecture from the PRD:

- one orchestrator session per request
- project skills in `.claude/skills`
- programmatic extractor, resolver, and verifier subagents
- Python tools exposed through an in-process MCP server for retrieval,
  rendering, validation, and auditable suggestions
- hooks for audit, ordering checks, and render-time validation

The core design is agentic. Claude is the orchestrator and owns semantic
interpretation, decomposition, retry strategy, and clarification. Python tools
are not meant to become a full rules engine; they provide evidence, stable
normalization, validation, and the only legal condition renderer.

The public CLI and API always use the Agent SDK orchestrator with extractor,
resolver, and verifier subagents.

## Project Layout

Claude project memory lives under `.claude/`:

```text
.claude/
  CLAUDE.md
  agents/
    extractor.md
    resolver.md
    verifier.md
  skills/
    vp-extraction/
    vp-table-routing/
    vp-variant-selection/
    vp-rendering-rules/
    vp-disambiguation/
    vp-golden-examples/
```

Agent prompts live in `.claude/agents/`. The Python SDK configuration still
creates `AgentDefinition` objects so tools, model, skills, and turn limits stay
explicit and testable.

## Agentic Memory

Business knowledge should primarily live in memory surfaces the agents can use:

- skills for procedural rules and role-specific judgment;
- `kpi_meta.csv` for canonical column/profile evidence;
- production VP descriptions for historical patterns;
- seed catalog metadata for template families;
- golden cases for evals and examples through the `vp-golden-examples` skill;
- episodic reviewed corrections for future boosts.

Avoid hardcoding every golden phrase in Python. Add deterministic code when the
logic is stable and mechanical, such as parsing common time tokens, enforcing
placeholder rules, checking known columns, or preventing handwritten syntax.

## Data

By default the app reads source data from:

`/Users/sachinpb/PycharmProjects/Virtual_profile_agent/data`

Override with:

```bash
export VP_DATA_DIR=/path/to/data
```

## Local Checks

```bash
./scripts/setup_dev.sh
. .venv/bin/activate
python -m pytest
python scripts/smoke_sdk.py
```

## Example

```bash
vp-agent "Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days" --client omantel
```

This command requires Claude Agent SDK authentication.

For SDK observability in agent mode, write a trace file:

```bash
vp-agent \
  --debug-sdk \
  --trace-file traces/latest.json \
  --client omantel \
  "Revenue from Total Roaming financial services in last 4 weeks for smartphone users who recharged more than 100 and have been on the network for more than 35 days."
```

The trace captures SDK message summaries, hook traces, SDK stderr/debug output,
model names, request metadata, and final errors if the Claude SDK stream fails.

## Langfuse

This app uses the official Langfuse/OpenTelemetry path for Claude Agent SDK:
`openinference-instrumentation-claude-agent-sdk` instruments the SDK, while the
FastAPI wrapper creates an app-level `vp-build` span.

Enable Langfuse by setting credentials before starting the API. The app loads
the project `.env` file automatically, so either export these values or place
them in `.env`:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://us.cloud.langfuse.com"
./scripts/run_api.sh
```

Use the base URL for the Langfuse project region that owns the keys, for example
`https://cloud.langfuse.com` for EU or `https://us.cloud.langfuse.com` for US.
For self-hosted Langfuse, set `LANGFUSE_BASE_URL` to your deployment, for
example `http://localhost:3000`.

Check the running server's tracing setup without exposing secrets:

```bash
curl http://127.0.0.1:8000/observability
```

Local development without credentials remains a no-op. To force-disable tracing:

```bash
export LANGFUSE_ENABLED=false
```

## API Server

Run the application backend with:

```bash
./scripts/run_api.sh
```

Enable concise per-request console tracing while debugging:

```bash
VP_CONSOLE_TRACE=1 ./scripts/run_api.sh
```

This prints tool calls and inferred decisions from existing tool outputs without
adding extra LLM calls or changing prompts.

Build a VP condition through the SDK-backed API:

```bash
curl -X POST http://127.0.0.1:8000/vp/build \
  -H "Content-Type: application/json" \
  -d '{"client":"omantel","request_id":"demo-001","session_id":"vp-demo","sentence":"Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days"}'
```

`request_id` is optional. If omitted, the API generates one and returns it in the
response. Langfuse root traces are named `vp-build:<client>:<request_id>` and
tagged with `vp-agent` plus the client name, so use `Is Root Observation = True`
and search for the request id to isolate one API call from its tool spans.

## Retrieval

`retrieve_columns` uses a local hybrid ranker:

- BM25 over feature name, group, type, description, time window, and value
  references.
- An embedding-similarity scorer. The current local implementation uses
  character n-gram vectors behind the same cosine-similarity contract; it can be
  replaced with a real embedding backend later.
- Equal weighting: `0.5 * normalized BM25 + 0.5 * normalized embedding`.
- Metadata/time boosts and client-production prior are only small tie-breakers,
  not primary ranking signals.

If we later add a real embedding service or precomputed vector file, it should
replace only the semantic scorer while keeping the BM25/RRF contract stable.

## Seed Selection

`select_seed` scores the seed catalog deterministically using:

- client compatibility
- time-token compatibility with seed axes
- KPI phrase match against seed axes and descriptions
- aggregate intent such as sum, count, average, presence, or metric change
- 360/raw preference when a precomputed Customer 360 KPI is selected
- required template-variable coverage

It returns the selected seed, top alternatives, confidence, reasoning, and
suggested render variables such as `kpi_col`, `date_col`, `N`, `divisor`, and
`vp_name`.

## Condition Planning

`build_condition_plan` connects retrieval, routing, and seed selection into the
exact input expected by `render_condition`. It chooses a main KPI column,
resolves filter columns, selects the route/seed, and returns `render_input`
without emitting condition syntax. This keeps `render_condition` as the only SDK
tool that emits `PARENT_CONDITION`.

## Slot Normalization

`normalize_slots` provides a deterministic first pass for common marketer
sentences. It handles common operators, numeric values, rolling time windows,
recharge/data domains, and common filters such as Omani nationality and
smartphones. The Claude extractor can use this as a normalization aid; it is not
exposed as a user-selectable application mode.

## Golden Dataset

Golden CSV rows can be loaded with `vp_agent.golden.load_golden_cases`. The
current rule captured from the UI golden set is: when the chosen KPI is a
Customer 360 snapshot, render it as a raw snapshot comparison. This applies to
M*, W*, rolling-day, MTD/LMTD, FW*, and similar pre-aggregated 360 columns. Do
not add an event/date condition like `CurrentMonth-*`, `CurrentTime-*`, or
`Event_Date`, because the snapshot column already represents that period.

The resolver and verifier agents can load `.claude/skills/vp-golden-examples`
as reviewed example memory. Add new semantic cases there before adding Python
branches, unless the change is a stable invariant or mechanical parser rule.

Run a snapshot-focused audit with:

```bash
.venv/bin/python scripts/eval_golden.py --snapshots-only
```
