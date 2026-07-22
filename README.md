# VP Agent

Virtual Profile Builder v2 converts telecom audience descriptions into validated
`PARENT_CONDITION` expressions.

This implementation follows the Claude Agent SDK architecture from the PRD:

- one orchestrator session per request
- project skills in `.claude/skills`
- skill-driven extraction and resolution owned by the orchestrator
- an optional independent verifier subagent, required for Variant-3 comparisons
- Python tools exposed through an in-process MCP server for retrieval,
  rendering, validation, and auditable suggestions
- hooks for audit, ordering checks, and render-time validation

The core design is agentic. Claude is the orchestrator and owns semantic
interpretation, decomposition, retry strategy, and clarification. Python tools
are not meant to become a full rules engine; they provide evidence, stable
normalization, validation, and the only legal condition renderer.

The public CLI and API always use the Agent SDK orchestrator. There is no
user-selectable deterministic mode and no Python rule-building path in front of
the agent.

## Core Invariants

- `client` is required input and is never inferred from the audience sentence.
- The model owns semantic interpretation, decomposition, ambiguity handling,
  column selection, routing, and retry strategy.
- Retrieval and seed tools provide auditable evidence; they do not make the
  final semantic decision.
- A rule exists only when `render_condition` emits it. The model must never
  write condition syntax through another path.
- `validate_rule` checks every emitted rule. Render or grammar failures are
  code defects, not prompts for the model to hand-edit syntax.
- Clarification questions are batched and written in plain business language.

## Runtime Flow

1. The wrapper supplies the required client and audience description.
2. The orchestrator loads the extraction and rendering skills, then loads
   additional skills only when the request needs them.
3. `normalize_slots` may provide a deterministic first-pass parse.
4. `retrieve_columns` returns compact metric, filter, and time-compatible
   candidates; the orchestrator resolves the intended columns and table.
5. `select_seed` supplies template evidence when the rule needs aggregation,
   formulas, joins, guards, or other non-trivial composition.
6. `record_resolution` records the orchestrator's semantic choices for audit.
7. `render_condition` is the only component allowed to emit the complete
   `PARENT_CONDITION`.
8. `validate_rule` checks the emitted rule. Variant-3 comparisons also require
   the verifier subagent before the request completes.

## Project Layout

Claude project memory lives under `.claude/`:

```text
.claude/
  CLAUDE.md
  agents/
    verifier.md
  settings.json
  skills/
    vp-extraction/
    vp-table-routing/
    vp-variant-selection/
    vp-metrics-comparison/
    vp-rendering-rules/
    vp-disambiguation/
    vp-golden-examples/
```

The verifier prompt lives in `.claude/agents/`. Extraction and resolution are
orchestrator responsibilities guided by project skills rather than separate
pipeline subagents. The Python SDK configuration creates the verifier's
`AgentDefinition` so its tools, model, skills, and turn limit remain explicit
and testable.

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

One model-facing `retrieve_columns` call now batches the metric, each filter,
and time compatibility. It returns up to five compact candidates per role. Each
candidate contains only its id, feature/group/type, short description,
time-window support, score, and concise evidence. The complete ranking and raw
score components are written to a Langfuse audit span and retained only in the
request-scoped tool store for page-2/page-3 targeted expansion.

The underlying local hybrid ranker uses:

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

It returns one complete `proposed_selected_seed` and up to three compact,
structurally diverse alternatives. Alternatives omit `selection_signature`.
Promote an alternative by fetching that one complete audited seed with the
original `audit_id` and `seed_id`.

Seed ranking is evidence, not a semantic decision. The orchestrator inspects the
proposal and remains responsible for accepting it, choosing its operands, or
asking for clarification.

## Baseline versus optimized regression

Run the baseline from an immutable worktree on port 8000 and the optimized
worktree on port 8001. Do not use `--reload` for the baseline.

```bash
VP_VARIANT=baseline .venv/bin/python -m uvicorn vp_agent.api:app --host 127.0.0.1 --port 8000
VP_VARIANT=optimized .venv/bin/python -m uvicorn vp_agent.api:app --host 127.0.0.1 --port 8001
```

Then run exact cases sequentially:

```bash
.venv/bin/python scripts/compare_vp_variants.py \
  --baseline http://127.0.0.1:8000 \
  --optimized http://127.0.0.1:8001
```

The harness health-checks both endpoints, rejects matching variant labels,
checkpoints each full response/request id, and runs baseline then optimized for
each case with a 15-minute default timeout.

## Variant-3 Metrics

Explicit period comparisons such as uplift, downlift, or percentage decline use
the reviewed `vp-metrics-comparison` skill. The agent extracts both period roles,
retrieves existing helper VPs for exact-name reuse, consults the seed catalog,
records its resolution, renders the final metric, and waits for an independent
semantic verifier. Python does not detect these requests or assemble their
formula with phrase-based branching.

## Condition Planning

There is no deterministic planning stage in the live agent path. The
orchestrator interprets retrieval and seed evidence, chooses the main KPI,
filters, table, aggregate, date bounds, and predicate ordering, records that
resolution, and composes the complete template passed to `render_condition`.

Legacy deterministic planning helpers remain in `vp_agent.tools.plan` for
tests and regression analysis, and the in-process MCP server still defines
compatibility tools such as `build_condition_plan` and `route_table`. They are
intentionally absent from the orchestrator's `allowed_tools`, so they cannot
replace live semantic reasoning.

## Slot Normalization

`normalize_slots` provides a deterministic first pass for common marketer
sentences. It handles common operators, numeric values, rolling time windows,
recharge/data domains, and common filters such as Omani nationality and
smartphones. The orchestrator can use this as a normalization aid; it is not
exposed as a user-selectable application mode.

## Golden Dataset

Golden CSV rows can be loaded with `vp_agent.golden.load_golden_cases`. The
current rule captured from the UI golden set is: when the chosen KPI is a
Customer 360 snapshot, render it as a raw snapshot comparison. This applies to
M*, W*, rolling-day, MTD/LMTD, FW*, and similar pre-aggregated 360 columns. Do
not add an event/date condition like `CurrentMonth-*`, `CurrentTime-*`, or
`Event_Date`, because the snapshot column already represents that period.

The orchestrator and verifier can load `.claude/skills/vp-golden-examples` as
reviewed example memory. Add new semantic cases there before adding Python
branches, unless the change is a stable invariant or mechanical parser rule.

Run a snapshot-focused audit with:

```bash
.venv/bin/python scripts/eval_golden.py --snapshots-only
```
