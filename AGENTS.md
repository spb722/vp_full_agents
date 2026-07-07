# VP Agent Project Rules

You convert telecom audience descriptions into Virtual Profile
`PARENT_CONDITION` rules.

## Invariants

1. The model never writes condition syntax by hand. A rule exists only when the
   `render_condition` tool emits it.
2. Routing and validation are deterministic. The model may decide when to call
   tools, but it cannot alter tool results.

## Agentic Boundary

This is an orchestrator-led Claude Agent SDK system, not a Python rules engine.
The LLM owns semantic interpretation, decomposition, ambiguity handling, and
retry strategy. Deterministic code exists to provide evidence, constraints, and
safe execution:

- retrieval tools surface candidates, not final truth;
- seed and plan tools propose traceable defaults, not irreversible decisions;
- validation and hooks enforce invariants;
- renderer is the only syntax emitter.

Do not encode every golden-case phrase as Python branching. Put reusable
business knowledge in skills, reviewed memory, seed metadata, and golden
examples so extractor/resolver/verifier agents can reason over it. Add
deterministic code only when it protects an invariant, normalizes a stable
primitive, or exposes auditable data to the agents.

## Memory Map

- `kpi_meta.csv`: canonical profile/column metadata.
- `vpdesc-all-omantel.csv` and `vpdesc-all-airtel.csv`: previous production VP
  conditions.
- `vp_seed_catalog_with_selection_metadata.json`: reusable condition templates
  and seed-selection metadata.
- `golden_case.csv`: reviewed UI golden cases used as semantic examples and
  eval targets, not as a hardcoded phrase switchboard.
- `.claude/skills/vp-golden-examples`: compact memory distilled from reviewed
  golden cases for resolver/verifier semantic alignment.
- `.claude/skills/vp-*`: procedural memory for extraction, routing, variant
  selection, rendering, and disambiguation.

## Client Rules

- `client` is required input from the wrapper service. Never infer it from the
  user sentence.
- Clarification questions must be plain English. Do not expose table names,
  group names, or column names to marketers unless explicitly requested.
- Prefer Customer 360 / 360 Profile when it already has a matching
  precomputed KPI. Fall back to summarized/event tables for custom or combined
  ranges.

## Retry Classes

- Column or coverage failure: call resolver again with the failed column in the
  exclusion list.
- Routing or date failure: call `route_table` again with the corrected slots.
- Render or grammar failure: halt as a code bug. Do not ask the model to patch
  syntax manually.
- Ambiguity: ask one batched plain-English clarification.
