---
name: vp-extraction
description: Use when parsing a new telecom audience request or re-extracting after a coverage failure. Extract KPI phrase, filters, time token, operator/value, domain, and ambiguity questions.
---

# VP Extraction

Return a compact slot JSON object. Do not write condition syntax.

`normalize_slots` may be used as a first-pass parser, but it is not authority.
You must correct it when the wording implies a richer telecom meaning, such as
finance revenue, local/offnet/onnet/roaming scope, package purchase, recharge
count vs recharge amount, active base, or snapshot period.

Required fields:

- `raw_request`: original sentence.
- `domain`: one of `profile`, `recharge`, `usage`, `subscription`,
  `lifecycle`, `campaign`, `audience_segment`, or `unknown`.
- `kpi_phrase`: the main measurable KPI phrase.
- `time_token`: normalized time, such as `30D`, `M1`, `M3`, `MTD`, `W1`,
  or `none`.
- `operator`: normalized comparison from the user intent, such as `>`, `>=`,
  `=`, `<`, `<=`, `IN`, or `unknown`.
- `value`: the threshold or category value from the user request, or empty when
  the main KPI should keep runtime placeholders.
- `filters`: every non-main-KPI constraint as plain objects with `phrase`,
  `operator`, and `value`.
- `negations`: explicit negative constraints.
- `needs_clarification`: boolean.
- `questions`: one batched list of plain-English questions when clarification
  is needed.

If the main KPI omits a timeframe for an event or aggregate KPI, mark
`needs_clarification=true` unless the wrapper has provided a default.

For filter KPIs with explicit fixed values, do not ask immediately only because
the filter period is missing. First resolve via retrieved metadata, Customer
360/profile snapshots, golden examples, or production defaults. Ask
clarification only if no clear default or multiple conflicting periods remain.

If the main KPI omits a comparison threshold, do not ask clarification for that
alone. Normal VP rules preserve `${operator} ${value}` as runtime placeholders.
Values stated in the request for non-main KPIs are fixed filter predicates, such
as recharge amount > 100 or roaming revenue >= 5000. Ask clarification only when
a filter needs a concrete value or the business meaning is ambiguous.

Do not expose column names or table names in clarification questions.
