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

An event-type qualifier is a FILTER, not part of the KPI phrase. When the
sentence names which kind of event counts — promotion, bonus, offer, a DPI
protocol such as streaming or YouTube, a bundle type such as data or voice, or
a channel such as app or USSD — emit it as its own filter predicate on the
categorical column that carries it. `kpi_phrase` describes what is measured;
the event type decides which rows qualify. Left inside `kpi_phrase`, retrieval
runs no filter role for it, so the constraint never reaches the rule and the
audience silently widens:

- "got any promotion in the last 4 days" -> count KPI, plus a filter on the
  lifecycle action type for promotion.
- "received a bonus last week" -> the same shape with the bonus value.
- "streaming app usage" -> usage KPI, plus a filter on the DPI protocol.

The campaign action type is stored with inconsistent casing in production data,
so it is always a MEMBERSHIP predicate covering every observed spelling, never a
single-case equality:

- promotion -> `{"phrase": "promotion", "operator": "IN LIST", "value": ["Promotion", "PROMOTION", "promotion"]}`
- bonus -> `{"phrase": "bonus", "operator": "IN LIST", "value": ["BONUS", "Bonus", "bonus"]}`

Rendered, those become `LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)`
with bare members and no quotes. A single `= "promotion"` matches lowercase rows
only and silently under-selects the audience.

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

## The sentence's subject is what you count

"Find **customers** who got a particular promotion" counts customers. "How many
**times** a customer recharged" counts recharge events. "Total **revenue**" sums a
revenue column. Carry that noun into `kpi_phrase`, because retrieval ranks the
metric role against that phrase and nothing downstream can recover a subject you
dropped.

Writing `kpi_phrase: "promotion received"` for "Find customers who got a
particular promotion" describes the event and loses the subject. Retrieval then
returned promotion columns, the subscriber-count column was not in the top five,
and the rule ended up counting the promotion filter itself. Write
`"customers who received a promotion"` — the entity first, the qualifier second.

Rule of thumb by aggregate:

- `COUNT_ALL` counts identities: subscribers, products, action keys. Never a
  category column such as an action type or a status.
- `SUM` / `AVG` take a numeric measure: revenue, volume, duration, count fields.

## Filters are predicates

Emit `{"phrase", "operator", "value"}`, plus `time_token` and `domain` on the
filter itself when that filter has its own period or lives outside the profile
tables — "purchased a product in the last 45 days" is
`{"phrase": "purchased a product", "time_token": "45D", "domain": "subscription"}`.
Without them the filter is retrieved as a period-less profile attribute and its
own window cannot be matched. Use a scalar for comparison, a JSON
list for membership, a two-value list for range, null for presence, and one
string for pattern. `null` is overloaded, so the operator disambiguates it:
`null` with a presence operator renders `<> NULL`, while `null` with
`operator: "runtime"` means the marketer supplies the value and the predicate
renders `${operator} ${value}`. Same-attribute alternatives become one membership filter;
different attributes remain separate filters. Preserve stated codes/values and
multi-word members. Do not invent syntax or values.

A compound noun phrase usually hides several attributes. Split it at
extraction time, one predicate per attribute:

- "Indian iPhone customers" -> nationality predicate AND handset predicate.
- "active prepaid smartphone users" -> status AND line type AND handset.
- "Omani feature-phone base" -> nationality AND handset.

If you leave them merged, retrieval gets one role for two columns and one of
the attributes is silently dropped. `retrieve_columns` reports
`unexplained_terms` per role as a safety net: on a filter role, a leftover word
means that phrase still needs splitting.

Read [references/predicate-cases.md](references/predicate-cases.md) only for a
non-comparison operator or ambiguous operand shape. Use only confirmed operators
from `vp-rendering-rules/references/operator-catalog.md`; otherwise clarify.

## Clarification discipline

- Missing main-KPI threshold alone is NOT a clarification; the main KPI keeps
  `${operator} ${value}` placeholders. Fixed values stated for non-main KPIs are
  filter predicates (e.g. recharge amount > 100, roaming revenue >= 5000).
- "A SPECIFIED number of X" is a named runtime placeholder, not a missing value
  and not the `${operator} ${value}` pair. "fewer than a specified number of
  bonuses in a specified number of days" keeps both quantities runtime while the
  comparison stays stated: set `operator` to `<`, leave `value` empty, and set
  `time_token` to the parameterised window. `vp-rendering-rules` renders
  `CurrentTime-${NoOfDays}DAYS` and `< ${NoOfBonus}`.
- "ANY <event>" is a scope, not a missing value. "got any promotion", "no bonus
  at all" means the event-type filter is the whole constraint: emit no selector
  predicate and leave `operator`/`value` empty. Do not turn it into a runtime
  filter — that would narrow "any promotion" to one the marketer picks. Contrast
  with "a PARTICULAR promotion", which does name a selector.
- A named attribute with NO stated value is NOT a clarification either. "based
  on segment name", "for a particular promotion", "by tariff plan" each name a
  column and leave its value to the marketer — which is exactly what
  `${operator} ${value}` is for. Emit it as a filter predicate with
  `{"operator": "runtime", "value": null}`, the same way a missing main-KPI
  threshold is emitted rather than asked about. Ask only when the phrase names
  no attribute at all.
- "Based on X" selects on X. It is not a `group_by`, and it does not mean
  "return everyone regardless of X". Do not offer those as clarification
  options.
- Missing filter period: first resolve via retrieved metadata, Customer 360 /
  profile snapshots, golden examples, or production defaults. Ask only if no
  safe default exists or multiple periods stay equally plausible.
- A DATA question is not a clarification. "Does this system record X?", "which
  column holds X?", "what does this term mean here?" are answered by
  `retrieve_columns` and `retrieve_existing_vps`, not by the user. Only an INTENT
  question — which of two equally supported audiences the marketer meant — is a
  clarification. An unfamiliar domain term ("non-responder", "non-delivered") is
  usually a defined family in the client's own VP names; look it up before
  treating it as ambiguous.
- Ordering one event against another is a KNOWN pattern, not a clarification.
  "after the promo was sent", "within 7 days of the bonus", "since the last
  recharge" anchor the window to the subscriber's own earlier event, which the
  engine supplies as a `$VARIABLE`. Do not ask whether the user means a fixed
  window or a runtime date, and do not convert it to a `time_token`: emit the
  anchor as a filter predicate naming the event ("after promo sent"), and let
  `vp-rendering-rules` render `>= $L_PROMO_SENT_DATE`. A stated span such as
  "within 7 days" becomes the upper bound `<= $L_PROMO_SENT_DATE+7DAYS`, not a
  7-day rolling window.
- "or its control-group variant" is a KNOWN pattern, not a clarification. The
  client marks a campaign's control group by suffixing the action key with
  `_CG`, so the request resolves to one action-key filter matched against both
  `${value}` and `${value}_CG`. Look it up with `retrieve_existing_vps` or
  `select_seed` before asking what a control group is.
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
- "per <entity>", "for each <entity>", "by <entity>" alongside an aggregate ->
  also set `group_by` to that entity, for example
  `"total revenue per product"` -> aggregate SUM, `group_by: "product"`. This is
  a separate slot from the aggregate and from any filter; the renderer turns it
  into a `__groupby_` suffix. Dropping it changes the audience from a per-entity
  threshold to a combined subscriber total.
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
