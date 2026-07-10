---
name: vp-extraction
description: Use when parsing a new telecom audience request or re-extracting after a coverage failure. Extract KPI phrase, filters as predicates (comparison, membership IN LIST / NOT IN LIST, range, null/presence, pattern), time token, operator/value, domain, and ambiguity questions.
---

# VP Extraction

Return a compact slot JSON object. Do not write condition syntax.

`normalize_slots` may be used as a first-pass parser, but it is not authority.
You must correct it when the wording implies a richer telecom meaning, such as
finance revenue, local/offnet/onnet/roaming scope, package purchase, recharge
count vs recharge amount, active base, or snapshot period. In particular,
`normalize_slots` emits one equality predicate per detected value; when the
sentence lists several alternatives for the same attribute, merge them into a
single membership predicate.

Required fields:

- `raw_request`: original sentence.
- `domain`: one of `profile`, `recharge`, `usage`, `subscription`,
  `lifecycle`, `campaign`, `audience_segment`, or `unknown`.
- `kpi_phrase`: the main measurable KPI phrase.
- `time_token`: normalized time, such as `30D`, `M1`, `M3`, `MTD`, `W1`,
  or `none`.
- `operator`: the main-KPI comparison intent, such as `>`, `>=`, `=`, `<`,
  `<=`, or `unknown`.
- `value`: the threshold/category value, or empty when the main KPI should keep
  runtime placeholders.
- `filters`: every non-main-KPI constraint as a predicate object (see below).
- `negations`: explicit negative constraints.
- `needs_clarification`: boolean.
- `questions`: one batched list of plain-English questions when needed.

## Filters are predicates

Emit each filter as `{"phrase", "operator", "value"}`, where `operator` names an
operator family and `value` carries the operand(s). The supported operators and
their exact rendering live in the operator catalog
(`vp-rendering-rules/references/operator-catalog.md`); only use operators listed
there. Operator families:

- Comparison — one scalar value.
  `{"phrase": "on network more than 300 days", "operator": ">", "value": "300"}`
- Membership — a list value (JSON array). Use for "A or B", comma lists, id/code
  lists, "any of", "one of".
  `{"phrase": "product 123 or 125", "operator": "IN LIST", "value": ["123","125"]}`
  Negated only when explicit: `"operator": "NOT IN LIST"`.
- Range — a two-element `[low, high]` value, for "between X and Y", "from X to Y".
  `{"phrase": "age between 18 and 35", "operator": "IN RANGE", "value": ["18","35"]}`
- Null / presence — no operand, for "has a recharge date", "was ever bonused".
  `{"phrase": "has last recharge date", "operator": "<> NULL", "value": null}`
- Pattern — one pattern string, for "name starts with", "id contains".

If a request needs an operator NOT present in the catalog, do not invent a
token. Set `needs_clarification=true` and ask in plain English.

## Same attribute vs different attributes

- Alternatives for the SAME attribute -> ONE membership predicate.
  "smartphone or iPhone" -> `IN LIST ["smartphone","iPhone"]`.
- Constraints on DIFFERENT attributes -> separate predicates joined later by AND.
  "prepaid smartphone users" -> line type = prepaid AND handset = smartphone.
  This is NOT a list.

## Values

- Keep id/codes as-is: `["123","125"]`, `["AR38","MD40"]`.
- Keep categorical words as the user said them; the resolver maps them to the
  canonical column value (e.g. "smartphone" -> handset `SP`). Do not invent
  values that were not stated.
- Preserve multi-word values as one array element: `["feature phone","smartphone"]`.
- Do not build the final `(a;b)` / `IN LIST` string yourself. Produce operands;
  the renderer emits syntax.

## Clarification discipline

- Missing main-KPI threshold alone is NOT a clarification; the main KPI keeps
  `${operator} ${value}` placeholders. Fixed values stated for non-main KPIs are
  filter predicates (e.g. recharge amount > 100, roaming revenue >= 5000).
- Missing filter period: first resolve via retrieved metadata, Customer 360 /
  profile snapshots, golden examples, or production defaults. Ask only if no
  safe default exists or multiple periods stay equally plausible.
- Do not expose column names or table names in clarification questions.

## Tenure/age durations are filters, not the time window

A duration describing how long the subscriber has been on the network is a
FILTER on an age/tenure column, never the KPI `time_token`.

- "been on the network more than 300 days", "age in the network more than 65
  days", "active for more than 35 days" -> filter `AON > N`, and
  `time_token = none` if that is the only duration in the sentence.
- A duration that says over what period the KPI is measured ("recharges in the
  last 300 days") IS the window.

Never let a tenure duration set the window or pull in a period snapshot.

## Aggregate intent and period

Set `aggregate` intent (COUNT / SUM / MAX / AVG / FORMULA / NONE) from the
wording, independently of the period.

- "count of / number of X" -> COUNT.
- "total X" -> SUM.
- When a COUNT/SUM KPI has NO stated period (`time_token = none`), the aggregate
  must be a raw `COUNT_ALL(...)` / `SUM(...)` over the event column. Do not let
  the resolver substitute a precomputed period snapshot such as `_90D`/`_30D`;
  those are only valid when the user stated that exact period.

Worked example (previously mis-handled):
"count of recharges performed by customers using feature phones, smartphones,
who are active subscribers and on the network more than 300 days"
- filter: handset type `IN LIST ["feature phone","smartphone"]`
- filter: subscriber status `= active`
- filter: `AON > 300`  (tenure, NOT a window)
- kpi_phrase: "recharge count"; aggregate: COUNT; `time_token: none`
- expected shape: `... AND COUNT_ALL(Recharge_count) ${operator} ${value}`
  (no snapshot, no date bound)

See [references/predicate-cases.md](references/predicate-cases.md) for worked
examples across every operator family.