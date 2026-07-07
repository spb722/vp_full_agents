---
name: vp-disambiguation
description: Use when candidate confidence is low, multiple KPIs match the same phrase, a required time window is missing, or a Variant 3/metrics interpretation is uncertain.
---

# VP Disambiguation

Ask only plain-English questions. Batch related questions into one user turn.

Ask when:

- The KPI phrase maps to multiple business meanings with similar confidence.
- An event/aggregate KPI has no timeframe and no wrapper default.
- The user says "active" without enough context to distinguish account status,
  data activity, recharge activity, or subscription state.
- The user appears to request an uplift/downlift metric but the periods or base
  KPI are missing.

Do not ask the user to pick columns, groups, table names, seed IDs, or internal
template names.

