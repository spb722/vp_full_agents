---
name: vp-variant-selection
description: Use when choosing the VP variant or seed family after slots and candidate columns are known.
---

# VP Variant Selection

Use these variants:

- Variant 1: all profiles come from one group/table.
- Variant 2: profiles come from multiple groups/tables; filter first,
  aggregate last.
- Variant 3: metrics/uplift/downlift. Build only when the user explicitly asks
  for uplift, downlift, percentage change, or comparison between periods.

Seed selection:

- Match seed by aggregation function, time style, formula need, guards, groupby,
  join, and runtime placeholders.
- Use `select_seed` after `retrieve_columns` and `route_table` as a ranked
  proposal. Inspect the reasons and alternatives before accepting it.
- Choose V3 only on explicit metric language. Otherwise ask a clarification.
- Exactly one `${operator} ${value}` pair is allowed in Variant 1 and Variant 2
  parent conditions.

Golden cases are examples for semantic generalization, not phrase switches.
Prefer skills and candidate descriptions over brittle string matching.
