---
name: vp-rendering-rules
description: Use immediately before rendering a PARENT_CONDITION and when interpreting validation failures for date bounds, placeholders, not-null guards, groupby suffixes, and client conventions.
---

# VP Rendering Rules

Only the `render_condition` tool may emit condition syntax.

Rules:

- Preserve `${operator} ${value}` in the stored VP expression.
- Use exactly one `${operator} ${value}` pair for normal VP expressions.
- For "last N months", default to a bounded completed-period range:
  `>= CurrentMonth-NMONTHS AND < CurrentMonth`.
- Drop the upper bound only if the user says "till date" or "including current
  month".
- Aggregate conditions must be last in multi-table or multi-condition rules.
- Use `<> NULL` guards only when the chosen seed/client convention requires it.
- Formula names in `V{...}=f{...}` must be unique in the rule.
- Customer 360 snapshot KPIs already encode their period. For those KPI
  conditions, render a raw comparison only; do not add `CurrentMonth`,
  `CurrentTime`, or event-date bounds. Other non-snapshot conditions in the same
  parent condition may still need date bounds.
