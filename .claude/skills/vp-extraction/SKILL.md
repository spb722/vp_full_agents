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

Keep explicit metric qualifiers inside `kpi_phrase`: service (data/voice/SMS),
measure (revenue/usage/count), direction (incoming/outgoing), scope
(local/onnet/offnet/IDD/roaming), and charging family (PAYG/bundle/free/finance).
Put subscriber constraints such as handset, status, tenure, nationality, line
type, and product id in independent filter predicates.

When a secondary KPI/filter grammatically refers to the same service event as
the main KPI, retain the shared direction/scope in that predicate. For example,
"outgoing international SMS ... where bundled SMS count equals 2" keeps
outgoing + international on both the revenue metric and the bundled-SMS-count
predicate. Do not broaden the secondary KPI to all outgoing SMS.

Required fields:

- `raw_request`: original sentence.
- `domain`: one of `profile`, `recharge`, `usage`, `subscription`,
  `lifecycle`, `campaign`, `audience_segment`, or `unknown`.
- `kpi_phrase`: the main measurable KPI phrase.
- `time_token`: normalized time, such as `30D`, `M1`, `M3`, `MTD`, `W1`,
  or `none`.
- `comparison`: for an explicit period-comparison request, an object containing
  `metric_intent`, `metric_unit`, `older_period`, and `newer_period`. Keep the
  two period roles separate; do not collapse them into the single `time_token`.
- `formula`: for a calculated percentage of one named KPI, emit
  `{"type":"percentage_of_kpi","percentage":N,"factor":N/100}`. This is
  distinct from a percentage change between periods.
- `operator`: the main-KPI comparison intent, such as `>`, `>=`, `=`, `<`,
  `<=`, or `unknown`.
- `value`: the threshold/category value, or empty when the main KPI should keep
  runtime placeholders.
- `filters`: every non-main-KPI constraint as a predicate object (see below).
- `negations`: explicit negative constraints.
- `needs_clarification`: boolean.
- `questions`: one batched list of plain-English questions when needed.

## Filters are predicates

Emit `{"phrase", "operator", "value"}`. Use a scalar for comparison, a JSON
list for membership, a two-value list for range, null for presence, and one
string for pattern. Same-attribute alternatives become one membership filter;
different attributes remain separate filters. Preserve stated codes/values and
multi-word members. Do not invent syntax or values.

Read [references/predicate-cases.md](references/predicate-cases.md) only for a
non-comparison operator or ambiguous operand shape. Use only confirmed operators
from `vp-rendering-rules/references/operator-catalog.md`; otherwise clarify.

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
- Explicit uplift, downlift, decline, growth, ratio, percentage change, or a
  mathematical comparison between two stated periods -> FORMULA and a populated
  `comparison` object. A plain mention of two periods is not automatically a
  metric; interpret what relationship the user requested.
- "N% of recharge amount" or "flat N% of recharge amount" -> FORMULA with
  `formula.type = percentage_of_kpi`, the stated percentage/factor, and the
  recharge amount as the KPI. Missing main-KPI threshold is not a clarification;
  the rendered KPI keeps `${operator} ${value}`. Do not confuse this with "top
  N% of subscribers" (population ranking), "increased/decreased by N%"
  (percentage change), or "KPI A is N% of KPI B" (ratio).
  For Omantel event-window retrieval, normalize this KPI phrase to "recharge
  denomination" so the reviewed recharge fact family is considered. Keep the
  aggregate as FORMULA even though the formula seed contains an outer SUM; do
  not replace it with a plain SUM seed.
- When a COUNT/SUM KPI has NO stated period (`time_token = none`), the aggregate
  must be a raw `COUNT_ALL(...)` / `SUM(...)` over the event column. Do not let
  the resolver substitute a precomputed period snapshot such as `_90D`/`_30D`;
  those are only valid when the user stated that exact period.

Read [references/time-token-cases.md](references/time-token-cases.md) when a
duration could be either subscriber tenure or the measured KPI window.
