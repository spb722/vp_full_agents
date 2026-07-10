# Operator Catalog — single source of truth for VP predicates

Every filter/condition in a PARENT_CONDITION is a **predicate**:

```
COLUMN  <operator>  <operand(s)>
```

This catalog is the ONLY place operators are defined. You (the agent) apply
these rules directly when you compose the PARENT_CONDITION string, then emit it
through `render_condition` as `template` (see the emission contract in
vp-rendering-rules). Extraction records the operator; you render it from the
family here. To support a new operator, add a row — do not scatter operator
logic across prompts or code.

## Status legend

- **confirmed** — observed in production/golden VPs; safe to emit.
- **needs-confirmation** — plausible for the CVM engine but NOT yet seen in the
  data provided. Do NOT emit until the exact token and syntax are confirmed by
  the project owner. If a request needs one of these, ask a plain-English
  clarification instead of guessing.

## Operator families

### 1. Comparison  (operand: one scalar)  — confirmed
Tokens: `=`, `!=`, `<>`, `>`, `>=`, `<`, `<=`
Syntax: `COLUMN OP value`
Quoting: numeric bare (`> 300`); categorical rendered by the existing scalar
literal rule (`= "prepaid"`).
Examples: `AON > 300`, `Profile_Line_Type = "PREPAID"`

### 2. Membership  (operand: list)  — IN LIST confirmed; NOT IN LIST needs-confirmation
Tokens: `IN LIST`  (alias `IN`),  `NOT IN LIST` (alias `NOT IN`)
Syntax: `COLUMN IN LIST (v1;v2;v3)`
Separator: `;` with no surrounding spaces. One space after `LIST`, then `(`.
Quoting per member: numeric bare; single-token alphanumeric bare; if any member
contains a space/special char, single-quote all string members (numerics stay
bare).
Examples:
- `SUBSCRIPTIONS_Product_Id IN LIST (123;125)`
- `Profile_Cdr_Handset_Type IN LIST ('feature phone';'smartphone')`
- `RE_REFILL_ID IN LIST (MD03;M138;M139;M140)`
- `LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)`

### 3. Range  (operand: two scalars: low, high)  — needs-confirmation
Candidate tokens: `IN RANGE`, `BETWEEN`
Proposed syntax (CONFIRM which the engine accepts and the exact shape):
- `COLUMN IN RANGE (low;high)`   or
- `COLUMN BETWEEN low AND high`
Use case: "age between 18 and 35", "recharge amount from 100 to 500".

### 4. Null / presence  (operand: none)  — `<> NULL` confirmed; `= NULL` needs-confirmation
Tokens: `<> NULL` (not-null guard, alias `IS NOT NULL`), `= NULL` (alias `IS NULL`)
Syntax: `COLUMN <> NULL`
Note: `<> NULL` is heavily used in production as a not-null guard on the
aggregated column, per client/seed convention. It carries no operand.

### 5. Pattern  (operand: one pattern string)  — needs-confirmation
Candidate tokens: `LIKE`, `NOT LIKE`
Not observed in the data. Confirm token and wildcard syntax before enabling.

## Hard rules that apply to every predicate

- A filter predicate has FIXED operands and never carries `${operator} ${value}`.
- Exactly one predicate in the whole rule carries the runtime
  `${operator} ${value}` pair — that is the main profiled KPI.
- Filter predicates come before the aggregate; the aggregate is last.
- Alternatives for the SAME column collapse into ONE membership predicate.
  Constraints on DIFFERENT columns stay as separate predicates joined by AND.
- Emit only operators marked **confirmed**. For a **needs-confirmation**
  operator, ask before rendering.

## Extending the catalog

To enable a new operator: (1) confirm the exact token and syntax with the
project owner, (2) add a row to the matching family above with an example,
(3) flip its status to confirmed. Because the agent composes the condition
string directly and emits it via `render_condition`'s `template`, no code change
is needed to support a new operator — this catalog is the source of truth.