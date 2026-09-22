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

- `coverage` — a number the marketer stated is absent from your rule. If the
  request states a COUNT LIMIT ("at most 2 times", "fewer than 3", "exactly 3"),
  the warning says so and there is nothing to defer: render it as a literal on
  the aggregate and move the pair to a selector column. Only an unstated
  threshold is legitimately deferred to `${operator} ${value}`.
- `intent` — something the request said left no trace on the rule. The `cue`
  field names what: `negation`, `per_entity`, `average`, `alternation`,
  `category_value`, or `scope`. `category_value` means the request named a value
  that existing production rules store on a particular column — promotion,
  bonus, a DPI protocol, a bundle type, a channel — and neither the value nor
  that column is in your rule. That almost always means the qualifier stayed
  inside the KPI phrase instead of becoming a filter predicate, so the rule now
  counts every event type rather than the one asked for. Treat `negation` as the most serious: a positive rule for a negative
  request is syntactically perfect and selects exactly the wrong audience.
- `convention` — a production VP built from these columns has a different shape.
  This one is INFORMATION, not a defect report. The named VP may answer a
  different question with the same columns, so a difference is expected and
  copying its clauses into your rule is usually wrong. Adopt something from it
  only when the request actually asks for it; otherwise keep your rule and say
  in one line why the shapes differ. Never add a clause you cannot trace back to
  the request just to make this warning stop.

These checks are textual and know nothing about your reasoning, so some fire
when you were right. That is expected. A `scope` warning in particular can fire
when the chosen column genuinely covers the scope without naming it — confirm
against the column description and say so. What you must never do is finish
without addressing a warning at all.

Addressing a warning means deciding about it in one line, not silencing it. Zero
warnings is not the goal and is not evidence of a better rule. One run rendered
the correct rule first, then added a date guard and an unrelated `ID <> NULL`
until the count reached zero, and shipped a rule that answered a different
question. If re-rendering would add anything the request never asked for, keep
the rule you have and explain the difference instead.

Rules:

- Preserve `${operator} ${value}` in the stored VP expression.
- Use AT MOST one `${operator} ${value}` pair. Two pairs is an error; so is one
  half of a pair without the other. Zero pairs is legal — 22 production VPs are
  written that way, such as `COUNT_ALL(SUBSCRIPTIONS_Product_Id) = 0` — and
  raises a warning rather than an error, because a fully specified rule answers
  exactly one question while a selector carrying the pair is reusable across
  campaigns. Prefer the reusable form, but NEVER delete a stated literal in order
  to create a pair: `COUNT_ALL(x) = 3` for "exactly 3 times" is a correct rule,
  and rewriting it as `COUNT_ALL(x) ${operator} ${value}` throws the answer away.
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

## Named runtime placeholders

`${operator}` and `${value}` are not the only `${...}` the engine substitutes.
Across the 713 production VPs:

| Placeholder | Rules | When |
| --- | --- | --- |
| `${operator}` / `${value}` | 695 | the runtime selector pair |
| `${X}` | 24 | the VP NAME is parameterised too — `LC_DELIVERED_LAST_${X}_DAYS`, `SUM_${X}_DAYS_TOTAL_REV` |
| `${NoOfDays}`, `${NoOfBonus}` | 6 | named runtime parameters on a concrete VP |
| `${SCHEDULE_ID}`, `${SEGMENT_EXECUTION_COUNTER}` | 2 | engine-supplied identifiers |

Choose between them by what the request says:

- The sentence literally uses X or N — "in the last X days" — and the rule is one
  of a family: use `${X}`, matching the name.
- The sentence says "a SPECIFIED number", "a configurable window", "a given
  count": use a descriptive name, `${NoOfDays}` for the window and `${NoOfBonus}`
  for a count limit, following `L_BONUS_SENT` and `L_PROMO_SENT`.

A named placeholder is a VALUE, so the comparison stays in the rule:

```text
L_BONUS_SENT_DATE >= CurrentTime-${NoOfDays}DAYS
  AND L_ACTION_KEY ${operator} ${value}
  AND COUNT_ALL(L_AGG_MSISDN) < ${NoOfBonus}
```

`< ${NoOfBonus}` keeps the "fewer than" the marketer stated while leaving the
number runtime. Writing `COUNT_ALL(...) ${operator} ${value}` instead throws the
`<` away and spends the selector pair on the count.

Named placeholders are preserved literally by the emitter and do NOT count
toward the at-most-one-pair rule — only `${operator}`/`${value}` do.

## Anchoring to another event: `$VARIABLE` references

A single-`$` name is a RUNTIME VARIABLE, not a column and not the
`${operator} ${value}` pair. The engine substitutes it per subscriber from the
event that triggered the rule, which is how a rule in one table can be anchored
to a date in another:

```text
created_date >= $L_PROMO_SENT_DATE AND SUM(I_RECHARGE_AMOUNT) ${operator} ${value}
```

Read that as "recharged at any point after the promo was sent to them". The
bounded form adds an upper edge, and arithmetic on the variable is allowed:

```text
created_date >= $L_PROMO_SENT_DATE
  AND created_date <= $L_PROMO_SENT_DATE+7DAYS
  AND SUM(I_RECHARGE_AMOUNT) ${operator} ${value}
```

Use this whenever the request orders one event against another — "after the
promo was sent", "within N days of the bonus", "since the last recharge" — rather
than asking what the anchor means. The window is relative to each subscriber's
own event, so it is NOT a `CurrentTime-NDAYS` window and NOT a `time_token`.

Reviewed production families: `INSTANT_RECHARGE_SUM_CHECK` and
`RECHARGE_SUM_INSTANT_CHECK` (unbounded), `LAST_2/3/7_DAYS_SUM_RECHARGE` and
`LAST_${X}_DAYS_SUM_RECHARGE` (bounded). Seeds `S79_recharge_post_promo` and
`S80_recharge_post_promo_bounded` carry the two shapes; `S73`/`S75`/`S76`/`S115`
use the same mechanism for offer, campaign and IMEI joins.

Other variables in use: `$OM_MSISDN`, `$OM_CHECK_MSISDN`, `$HBB_imeiNumber`,
`$RPT_RPT_DATE`, `$RPT_RPT_OFFER_ID`, `$RPT_RPT_CAMPAIGN_ID`,
`$TRIGGER_REQUEST_DATE`. They are not kpi_meta columns, so `validate_rule` does
not treat them as unknown identifiers.

## Control-group variants (`_CG`)

"or its control-group variant", "and the control group", "including CG" means the
rule matches BOTH the action key the marketer supplies and that key's
control-group twin. The client writes the twin by suffixing `_CG`, so the two
alternatives share one runtime value:

```text
(L_ACTION_KEY ${operator} ${value} OR L_ACTION_KEY ${operator} ${value}_CG)
  AND COUNT_ALL(L_AGG_CNT)__groupby_L_ACTION_KEY > 0
```

Both sides name the SAME column, and the second `${value}` carries the literal
suffix `_CG`. This counts as one runtime pair, not two — the marketer still
supplies a single key. Reviewed production rules: `L_AK_PROMO_COUNT`,
`L_AK_BONUS_COUNT`, `L_AK_PROMO_COUNT_3D`, `L_PROMO_DT`. Seeds `S44`/`S45` carry
the absent/present forms and `S46`/`S47` the time-scoped ones.

Note the family counts `L_AGG_CNT`, not `L_AGG_MSISDN`, and always groups by the
key column.

## Where the runtime pair goes

Normally `${operator} ${value}` belongs on the main profiled KPI. The reviewed
exception is a rule whose aggregate comparison is already decided by the
request, leaving the pair no work to do there. The aggregate is decided when
the request either:

- states a hard count limit — "at most 4 times", "no more than 3
  subscriptions" — which renders as that literal; or
- implies one — a negation ("did not get any promotion", "never recharged")
  renders `= 0`, and a bare presence ("got any promotion", "has subscribed")
  renders `> 0`.

When the aggregate is decided AND the rule contains a natural runtime selector
column — a segment name, action key, product id, refill id, or similar
identifier the campaign will choose later — follow the production convention:

- put `${operator} ${value}` on the SELECTOR column, and
- keep the decided comparison as a literal on the aggregate.

```text
SUBSCRIPTIONS_EVENT_DATE >= CurrentTime-30DAYS
AND SUBSCRIPTIONS_Product_Id ${operator} ${value}
AND COUNT_ALL(SUBSCRIPTIONS_Product_Id) <= 4
```

```text
L_PROMO_SENT_DATE >= CurrentTime-${X}DAYS
AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)
AND LC_SEGMENT_NAME ${operator} ${value}
AND COUNT_ALL(L_AGG_MSISDN) = 0
```

The second is the reviewed `LC_NONDELIVERED_LAST_${X}_DAYS` family: 22 Omantel
production rules share that exact skeleton, with the pair on the selector and
the negation preserved as a literal `= 0`.

A selector phrase reaches you as a filter predicate with
`{"operator": "runtime", "value": null}`. That predicate IS the pair's home;
render it as `COLUMN ${operator} ${value}`.

"ANY" versus "a particular" decides whether a selector exists at all:

- "got ANY promotion", "no bonus at all" — the event-type filter already scopes
  the rule. Render it WITHOUT a selector and without a runtime pair:
  `L_PROMO_SENT_DATE >= CurrentTime-6DAYS AND LC_ACTION_TYPE IN LIST (...) AND COUNT_ALL(L_AGG_MSISDN) > 0`.
  Adding `L_ACTION_KEY ${operator} ${value}` here narrows "any promotion" to
  "one promotion the marketer picks", which is a different audience.
- "a PARTICULAR promotion", "a specific bonus", "based on segment name", "by
  action key" — the marketer chooses at runtime, so the named attribute becomes
  the selector and carries the pair.

When the request says neither, follow the client's family: all 19 production
promotion rules carry a selector (`L_ACTION_KEY` 10, `LC_SEGMENT_NAME` 9),
because one stored rule then serves every campaign.

A stated count limit — "at most 2 times", "fewer than 3", "exactly 3" — is
NOT deferrable. It renders as a literal on the aggregate; there is nothing to
defer because the marketer already said the number. If you find yourself
replacing `COUNT_ALL(x) = 3` with `COUNT_ALL(x) ${operator} ${value}`, you are
deleting the answer to make room for a placeholder.

When no selector column is available, `select_seed` returns a `selector_hint`
naming the columns the client actually pairs with your counted column, with
counts. Retrieve one of those and use it rather than surrendering the aggregate:
the pair belongs on the selector precisely so the stated number can stay.

Only if no selector exists at all, keep the pair on the aggregate and say plainly
that the decided comparison becomes the runtime threshold. Never silently drop
a stated count, and never drop a negation to make room for the pair.
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
for a needs-confirmation operator. A filter with a FIXED operand never carries
the runtime pair — a stated value renders as that value. A filter whose operand
is a runtime selector (`{"operator": "runtime", "value": null}`) is the one
exception, and it takes the pair instead of the main KPI; see "Where the runtime
pair goes".
