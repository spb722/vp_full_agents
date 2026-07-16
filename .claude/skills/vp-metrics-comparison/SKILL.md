---
name: vp-metrics-comparison
description: Resolve and verify telecom Variant-3 metrics that compare two explicit periods, including uplift, downlift, decline, percentage drop, percentage change, growth, and ratios. Use when a request asks for a mathematical comparison between two month/week aggregates or when a rendered rule may have confused an absolute delta with a percentage metric.
---

# VP Metrics Comparison

Interpret the request semantically. Do not detect Variant 3 with a closed phrase
list or construct it with hardcoded branching.

## Extract the comparison

Represent both periods and their roles independently from the main KPI:

- `metric_intent`: decline, uplift, percentage change, ratio, or the user's
  stated comparison intent.
- `metric_unit`: percentage or absolute. Do not silently exchange them.
- `older_period` and `newer_period`: normalized tokens such as `M2` and `M1`.
- `kpi_phrase`: the business measure shared by both operands.

An explicit KPI plus two explicit periods plus comparison language is sufficient
for Variant 3. Ask one plain-English clarification when the KPI, a period, or
the requested metric remains unclear after retrieval.

Under the reviewed KT convention, `decline`, `downlift`, and `percentage drop`
with two resolved periods mean percentage decline. An explicit request for an
amount decrease, absolute difference, or revenue delta instead means an
absolute metric and must not be converted to percentage.

## Resolve dependencies

Retrieve existing client VPs for both period operands before composing the
metric. Reuse semantically matching helpers by exact name. A helper candidate is
valid only when its definition covers the same KPI family, aggregate, and exact
period.

If a helper does not exist, identify it as a dependency to provision before the
final metric. Do not replace a required helper VP name with a raw KPI column in
the final Variant-3 expression merely because a Customer 360 snapshot exists.

Use `select_seed` as ranked evidence. Inspect its template, reasoning, variable
roles, and alternatives; the agent owns the final choice.

## Apply the reviewed decline convention

For the reviewed Rakesh KT convention, percentage decline from an older period
to a newer period is:

`(older VP - newer VP) / newer VP * 100`

For M2 compared with M1, M2 is older and M1 is newer. This convention is a
client business rule even though other percentage-change conventions may use a
different denominator. Do not substitute a textbook convention.

Read [references/rakesh-variant3.md](references/rakesh-variant3.md) before
rendering or verifying a Variant-3 rule.

## Render the final metric

- Reference helper VP names directly and exactly.
- Do not wrap the final Variant-3 metric in `V{name}=f{...}`.
- Preserve exactly one `${operator} ${value}` pair on the final metric.
- Do not put runtime placeholders inside the final metric's helper references.
- Emit only through `render_condition`, then run `validate_rule`.

## Verify semantically

Reject and retry when a candidate rule:

- calculates only an absolute difference for a percentage request;
- reverses older and newer operands;
- uses the wrong denominator;
- omits multiplication by 100;
- references raw KPI columns where matching helper VPs must be reused;
- applies the Variant-1 `V{...}=f{...}` wrapper to the final metric;
- carries missing or duplicate runtime placeholders.

Grammar validation is necessary but cannot prove these semantic properties.
