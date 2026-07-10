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

Before emitting, gather your evidence with `retrieve_columns` (and
`shelf_lookup` when checking for a matching 360 snapshot). Treat those, and
anything from `normalize_slots`, `select_seed`, or `build_condition_plan`, as
EVIDENCE ONLY. Do not let them choose your columns, your period, or your filter
syntax, and do not hand their `render_input` to `render_condition`. You decide;
you compose; the tool emits.

After emitting, call `validate_rule` and read its result. If it reports an
error, fix your composed string and emit again.

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

## Predicates and the operator catalog

Every filter/condition is a predicate: `COLUMN operator operand(s)`. The full
set of supported operators, their operand shapes, exact syntax, and quoting
rules live in one place:
[references/operator-catalog.md](references/operator-catalog.md).

Consult the catalog before rendering any non-trivial filter. Key points:

- Comparison (`=`, `!=`, `<>`, `>`, `>=`, `<`, `<=`) take one scalar operand.
- Membership (`IN LIST`, `NOT IN LIST`) take a list operand and render as
  `COLUMN IN LIST (v1;v2;v3)` — semicolon separated, quoting per catalog.
- Range (`IN RANGE` / `BETWEEN`) take a low/high pair.
- Null guards (`<> NULL`, `= NULL`) take no operand.
- Pattern (`LIKE`, `NOT LIKE`) take one pattern operand.

Only emit operators the catalog marks **confirmed**. If a request needs a
**needs-confirmation** operator, ask a plain-English clarification instead of
guessing a token or syntax — a wrong operator token produces an invalid rule.

## Membership syntax (most common non-comparison predicate)

Canonical: `COLUMN IN LIST (value1;value2;value3)`

- One space before the operator, one space after `LIST`, then `(`.
- Values separated by `;` with no spaces around the semicolon. No trailing
  separator, no empty members.
- Quoting per member: numeric bare; single-token alphanumeric bare; if any
  member contains a space/special char, single-quote all string members.
- Negation only when explicit: `COLUMN NOT IN LIST (...)`.

Do not:

- quote the whole list (`IN LIST "(123;125)"` is wrong);
- split one multi-value attribute into `= a AND = b` (empty intersection);
- use commas as the separator (always `;`);
- add spaces inside the parentheses.

A membership/range/null predicate has fixed operands and must not carry the
runtime `${operator} ${value}` pair; that single pair belongs to the main KPI.