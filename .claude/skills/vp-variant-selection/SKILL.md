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

For Variant 3, load `vp-metrics-comparison` and follow its reviewed dependency,
period-role, formula, and verification rules. Variant 3 is not a Variant-1
`V{name}=f{...}` formula: it references existing helper VP names directly.

Seed selection:

- Match seed by aggregation function, time style, formula need, guards, groupby,
  join, and runtime placeholders.
- Use `select_seed` after role-aware retrieval and routing. Treat
  `proposed_selected_seed` as a complete, deterministic proposal, not final
  truth. Inspect its compatibility evidence and up to three structurally
  diverse compact alternatives.
- If an alternative is better, call `select_seed` with its `audit_id` and
  `seed_id` to fetch that one complete audited entry. Never reconstruct omitted
  metadata and never expect `selection_signature` on compact alternatives.
- Choose V3 only on explicit metric language. Otherwise ask a clarification.
- Retrieve both existing helper VPs before composing V3. Use exact-name reuse
  when their definitions match the requested KPI, aggregate, and period.
- Treat an absolute delta, percentage decline, percentage uplift, and ratio as
  different metric intents. Do not substitute one for another.
- Exactly one `${operator} ${value}` pair is allowed in Variant 1 and Variant 2
  parent conditions.

Golden cases are examples for semantic generalization, not phrase switches.
Prefer skills and candidate descriptions over brittle string matching.
