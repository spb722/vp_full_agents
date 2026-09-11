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

`select_seed` returns candidates that fit structurally but still need work.
Read these fields rather than treating a seed as take-it-or-leave-it:

- `adaptations` — the seed's shape is right but something must be
  re-parameterised, such as `time_unit_adaptation` (its window is written in
  different units than the request) or `supply_variables` (a variable the
  resolver could not infer, which YOU fill when composing). An adaptation is
  never a reason to discard an otherwise well-matched seed.
- `matched_phrases` — reviewed marketer phrasings the seed was built for. A
  strong phrase match outweighs a small score gap.
- `supply` — how many seeds were considered and how many survived. When
  `supply.advisory` is present, the proposal is weak evidence: compose from the
  request and the operator catalog instead of deferring to that one template.

After emitting, call `validate_rule` with the rule, the original request, the
table, AND the client, then read its result. Errors mean fix and re-emit.
Warnings are advisory and you must address each one explicitly before
finishing:

- `coverage` — a number the marketer stated is absent from your rule. Either
  render it or state why it is deliberately deferred to `${operator} ${value}`.
- `intent` — something the request said left no trace on the rule. The `cue`
  field names what: `negation`, `per_entity`, `average`, `alternation`, or
  `scope`. Treat `negation` as the most serious: a positive rule for a negative
  request is syntactically perfect and selects exactly the wrong audience.
- `convention` — an existing production VP uses the same columns in a different
  shape. Compare the two and either adopt the production convention or say why
  this request differs.

These checks are textual and know nothing about your reasoning, so some fire
when you were right. That is expected. A `scope` warning in particular can fire
when the chosen column genuinely covers the scope without naming it — confirm
against the column description and say so. What you must never do is finish
without addressing a warning at all.

Rules:

- Preserve `${operator} ${value}` in the stored VP expression.
- Use exactly one `${operator} ${value}` pair for normal VP expressions.
- For "last N months", default to a bounded completed-period range:
  `>= CurrentMonth-NMONTHS AND < CurrentMonth`.
- Drop the upper bound only if the user says "till date" or "including current
  month".
- Match the date anchor to the window the extractor resolved. A rolling day
  token (`7D`, `30D`) renders `CurrentTime-NDAYS`; a completed calendar token
  renders `CurrentWeek-NWEEKS` or `CurrentMonth-NMONTHS`. See
  `vp-extraction/references/time-token-cases.md` for deciding which the
  marketer meant, and ask one clarification when both readings stay plausible.

## Per-entity aggregation (`__groupby_`)

"per product", "per action key", "per loyalty id", "for each <entity>" means the
aggregate is evaluated once per that entity, not once per subscriber. Render it
by suffixing the aggregate with the grouping column:

```text
SUM(COLUMN)__groupby_GROUPING_COLUMN
COUNT_ALL(COLUMN)__groupby_GROUPING_COLUMN
```

Rules:

- The suffix attaches directly to the closing parenthesis with no spaces.
- Group by multiple columns with a comma and no spaces:
  `COUNT_ALL(L_AGG_CNT)__groupby_L_ACTION_KEY,L_PROMO_SENT_DATE`.
- The grouping column is a real column from the same group/table as the
  aggregated column.
- The suffix goes AFTER the aggregate, never before it; `validate_rule` rejects
  the reverse order.
- A runtime `${operator} ${value}` pair, when it belongs on the aggregate, comes
  after the whole suffix:
  `SUM(SUBSCRIPTIONS_Revenue)__groupby_SUBSCRIPTIONS_Product_Id ${operator} ${value}`.

Reviewed production examples:
`SUM(LMS_EXPIRY_REWARD_POINTS)__groupby_LMS_EXPIRY_LOYALTY_ID > 0`,
`COUNT_ALL(SUBSCRIPTIONS_MSISDN)__groupby_SUBSCRIPTIONS_Product_Id = 0`.

Do not drop the grouping. A per-entity request rendered as a plain aggregate
silently changes the audience from "any product exceeding the threshold" to
"the subscriber's combined total exceeding the threshold". Most seed templates
do not carry a groupby suffix, so you usually add it yourself when composing.

## Where the runtime pair goes in a count-threshold rule

Normally `${operator} ${value}` belongs on the main profiled KPI. Count
threshold rules are the reviewed exception. When the marketer states a hard
count limit ("at most 4 times", "no more than 3 subscriptions") AND the rule
contains a natural runtime selector column — a product id, action key, refill
id, or similar identifier the campaign will choose later — follow the
production convention:

- put `${operator} ${value}` on the SELECTOR column, and
- keep the stated count as a literal comparison on the aggregate.

```text
SUBSCRIPTIONS_EVENT_DATE >= CurrentTime-30DAYS
AND SUBSCRIPTIONS_Product_Id ${operator} ${value}
AND COUNT_ALL(SUBSCRIPTIONS_Product_Id) <= 4
```

This keeps the stated 4 in the stored rule and leaves the product runtime
selectable. If there is no selector column, keep the pair on the aggregate and
say plainly that the stated count becomes the runtime threshold. Never silently
drop a stated count.
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
