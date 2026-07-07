---
name: vp-golden-examples
description: Use when resolving or verifying VP column choices against reviewed golden UI examples, especially Customer 360 snapshot KPIs, finance-service revenue, data bundle/free usage revenue, recharge transaction counts, and average revenue formulas.
---

# VP Golden Examples

Use this skill as reviewed example memory. Golden cases show how production UI
expects marketer language to map to VP condition families. They are examples for
semantic generalization, not exact phrase switches.

Load [references/golden-patterns.md](references/golden-patterns.md) when:

- retrieval returns multiple plausible KPI columns;
- a Customer 360 snapshot column may already encode the requested time period;
- deciding between raw snapshot comparison and event-table date bounds;
- choosing sum, count, average, max, formula, or presence-style seeds;
- verifier sees a rendered condition that may contain an unnecessary date
  condition or a mismatched KPI family.

Resolver workflow:

1. Read the user request semantically: KPI meaning, service domain, direction
   such as local/offnet/onnet/roaming/IDD, product type, time window, filters,
   aggregate intent, and threshold.
2. Compare retrieved candidates with the golden patterns and KPI metadata.
3. Prefer a matching Customer 360 snapshot only when the column itself encodes
   the requested period. Otherwise use event/summarized tables with date bounds.
4. Do not invent columns. If no retrieved column matches the golden pattern,
   retry retrieval with better semantic terms or ask a clarification.

Verifier workflow:

1. Confirm the selected KPI family matches the marketer request, not just a
   nearby word overlap.
2. If the selected KPI is a snapshot such as `M1`, `W4`, `90D`, `MTD`, or
   similar, the KPI comparison should be raw and should not include an event
   date condition for that KPI.
3. If other filters or secondary KPIs are event-based, those separate
   conditions may still need their own date bounds.
