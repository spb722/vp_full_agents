# Reviewed Rakesh KT Variant-3 Convention

Source: `Virtual_Profile_KT.pdf`, pages 8-9.

Variant 3 performs mathematics across two period aggregates. It is represented
as three chained VPs:

1. A helper VP for the newer period, such as `REVENUE_MON1`.
2. A helper VP for the older period, such as `REVENUE_MON2`.
3. A user-facing comparison VP that references the two helper names.

For a decline comparing two months ago with last month, use the reviewed
business convention:

`(M2 helper - M1 helper) / M1 helper * 100 ${operator} ${value}`

The M2 helper is the older-period operand. The M1 helper is the newer-period
operand and denominator. A decline therefore produces a positive percentage.

The final metric:

- reuses existing helpers by exact VP name;
- does not use the `V(x)=f(...)` format;
- carries the runtime `${operator} ${value}` pair;
- is provisioned only after any missing helper VPs.

Do not infer that every mention of two periods requests Variant 3. Require a
clear mathematical comparison such as decline, downlift, uplift, percentage
change, growth, ratio, or an explicit request to compare the period values. If
the intended comparison is not clear, ask conversationally.
