# Predicate Cases — worked extraction examples by operator family

Produce the `filters` predicates only; the renderer emits final syntax. Use only
operators listed in the operator catalog. Operands are the values as the user
meant them.

## Comparison (one scalar)

- "on the network more than 300 days" ->
  `{"phrase": "network age > 300 days", "operator": ">", "value": "300"}`
- "recharged more than 100" ->
  `{"phrase": "recharge amount > 100", "operator": ">", "value": "100"}`
- "prepaid customers" ->
  `{"phrase": "line type prepaid", "operator": "=", "value": "prepaid"}`

## Membership: IN LIST (list operand)

Use for "A or B", comma lists, and explicit id/code lists on ONE attribute.

- "products 123 or 125" ->
  `{"phrase": "product 123 or 125", "operator": "IN LIST", "value": ["123","125"]}`
- "active or inactive subscribers" ->
  `{"phrase": "active or inactive", "operator": "IN LIST", "value": ["active","inactive"]}`
- "feature phones, smartphones" ->
  `{"phrase": "feature phones, smartphones", "operator": "IN LIST", "value": ["feature phone","smartphone"]}`
- "smartphone or iPhone users" ->
  `{"phrase": "smartphone or iPhone", "operator": "IN LIST", "value": ["smartphone","iPhone"]}`
- "refill packs AR38, AR39 or MD40" ->
  `{"phrase": "refill packs AR38, AR39, MD40", "operator": "IN LIST", "value": ["AR38","AR39","MD40"]}`

## Membership: NOT IN LIST (explicit negation only)

- "customers not on packs 123 or 125" ->
  `{"phrase": "not on packs 123 or 125", "operator": "NOT IN LIST", "value": ["123","125"]}`

## Range (low/high pair)

- "age between 18 and 35" ->
  `{"phrase": "age between 18 and 35", "operator": "IN RANGE", "value": ["18","35"]}`
- "recharge amount from 100 to 500" ->
  `{"phrase": "recharge amount 100 to 500", "operator": "IN RANGE", "value": ["100","500"]}`

(Range operator token — `IN RANGE` vs `BETWEEN` — must be confirmed in the
catalog before it is emitted.)

## Null / presence (no operand)

- "customers who have a last recharge date" ->
  `{"phrase": "has last recharge date", "operator": "<> NULL", "value": null}`
- "subscribers with no error code" ->
  `{"phrase": "no error code", "operator": "= NULL", "value": null}`

## Pattern (one pattern operand)

- "msisdn starting with 968" ->
  `{"phrase": "msisdn starts with 968", "operator": "LIKE", "value": "968%"}`

(Pattern operator/wildcard must be confirmed in the catalog before it is
emitted.)

## Not a predicate list (do NOT use membership)

- "prepaid smartphone users" -> two predicates on two attributes:
  line type = prepaid AND handset type = smartphone.
- "Omani smartphone users" -> nationality = Omani AND handset type = smartphone.

## Standalone audience via list

When the whole request is "customers who bought product 123 or 125 in the last
month" with no other measured KPI, the membership predicate is the audience and
the aggregate is a presence count (`COUNT_ALL(<product-id column>) > 0`). Mark
this as a count/presence intent for the resolver rather than leaving the main
KPI empty.