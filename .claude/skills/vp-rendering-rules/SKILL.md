---
name: vp-rendering-rules
description: Use immediately before rendering a PARENT_CONDITION and when interpreting validation failures for predicate operators (comparison, IN LIST / NOT IN LIST membership, range, null guards, pattern), date bounds, placeholders, not-null guards, groupby suffixes, and client conventions.
---

# VP Rendering Rules

Only the `render_condition` tool may emit condition syntax.

## Emission contract (agentic — read first)

YOU compose the entire PARENT_CONDITION in your reasoning, applying the operator
catalog and the rules below, and then emit it through `render_condition` as a
single finished string. The tool is the emitter of record; it does not decide
anything for you.

Call it like this:

- `template` = the complete PARENT_CONDITION string you assembled, filters and
  aggregate and `${operator} ${value}` included, in final order.
- `variables` = `{}` (empty). Do not leave `{placeholder}` tokens for the tool
  to fill.
- `filters` = `[]` (empty). Do NOT pass filters as separate objects — that path
  applies fixed quoting you do not want (it would wrap a list as
  `IN LIST "(...)"` and quote categorical values).
- `client` = the client.

`${operator}` and `${value}` are preserved as-is by the emitter, so keep them
literally in your string.

Before emitting, gather metric, filter, and time evidence with one role-aware
`retrieve_columns` call. Its compact candidate fields are the model-facing
evidence; the full ranking remains in the external audit. Treat those, and
anything from `normalize_slots`, `select_seed`, or `build_condition_plan`, as
EVIDENCE ONLY. Do not let them choose your columns, your period, or your filter
syntax, and do not hand their `render_input` to `render_condition`. You decide;
you compose; the tool emits.

Expand only an unresolved retrieval role with the same `audit_id`: page 2 for
ranks 6–10, then page 3 for ranks 11–15. Do not repeat successful roles or run
separate broad metric/filter/date retrieval calls.

After emitting, call `validate_rule` and read its result. If it reports an
error, fix your composed string and emit again.

Rules:

- Preserve `${operator} ${value}` in the stored VP expression.
- Use exactly one `${operator} ${value}` pair for normal VP expressions.
- For "last N months", default to a bounded completed-period range:
  `>= CurrentMonth-NMONTHS AND < CurrentMonth`.
- Drop the upper bound only if the user says "till date" or "including current
  month".
- Aggregate conditions must be last in multi-table or multi-condition rules.
- Use `<> NULL` guards only when the chosen seed/client convention requires it.
- Formula names in `V{...}=f{...}` must be unique in the rule.
- `V{...}=f{...}` is a Variant-1 formula shape. A Variant-3 period metric is an
  exception: reference its helper VP names directly and apply the arithmetic
  without a `V{...}=f{...}` wrapper. Follow `vp-metrics-comparison`.
- Customer 360 snapshot KPIs already encode their period. For those KPI
  conditions, render a raw comparison only; do not add `CurrentMonth`,
  `CurrentTime`, or event-date bounds. Other non-snapshot conditions in the same
  parent condition may still need date bounds.
- Customer 360 columns may support a helper VP, but they do not replace an
  existing helper VP name inside the final Variant-3 metric.

## Predicates and the operator catalog

Load the catalog before any non-comparison predicate. It is the single source
for supported tokens, operand shapes, quoting, and exact syntax:
[references/operator-catalog.md](references/operator-catalog.md).

Emit only catalog operators marked confirmed. Ask a plain-English clarification
for a needs-confirmation operator. Fixed filter operands never carry the runtime
pair; `${operator} ${value}` belongs only to the main KPI.
