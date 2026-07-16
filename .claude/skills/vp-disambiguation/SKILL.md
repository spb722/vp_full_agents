---
name: vp-disambiguation
description: Use when candidate confidence is low, multiple KPIs match the same phrase, a required time window is missing, or a Variant 3/metrics interpretation is uncertain.
---

# VP Disambiguation

Ask only plain-English questions. Batch related questions into one user turn.

Ask when:

- The KPI phrase maps to multiple business meanings with similar confidence.
- The main event/aggregate KPI has no timeframe and no wrapper/default period.
- A filter KPI has no timeframe only after retrieval fails to find a clear
  canonical snapshot/default period, or multiple period interpretations remain
  equally plausible.
- The user says "active" without enough context to distinguish account status,
  data activity, recharge activity, or subscription state.
- The user says "high value", "high-value customer", "valuable customer",
  "premium customer", "high spender", or similar business labels without saying
  whether this means a stored value segment, revenue, ARPU, recharge amount,
  spend, CLV, or another KPI. Do not silently map "high value customer" to a
  value-segment column unless the user explicitly says value segment/segment.
- The user appears to request an uplift/downlift metric but the periods or base
  KPI are missing.
- A percentage of an amount is stated without saying what decision or
  comparison should be made from the calculated amount. For example, "20% of
  recharge amount in the last two months" alone is ambiguous: ask whether the
  marketer wants the calculated 20% amount compared with a threshold, a share
  of another amount, or something else. Do not infer a multiplication formula
  merely because a percentage and an amount appear together.

Do not ask the user to pick columns, groups, table names, seed IDs, or internal
template names.

For "high value" ambiguity, ask in business terms, for example:
"Should high value mean customers in an existing High Value segment, or
customers whose revenue, spend, recharge amount, ARPU, or CLV crosses a
threshold over the stated period?"

For a percentage-of-amount ambiguity, ask in business terms, for example:
"What should the calculated 20% of recharge amount be compared with or used
for?"
