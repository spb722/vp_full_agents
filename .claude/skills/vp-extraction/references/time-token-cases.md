# Time Token Cases

- `last 30 days`, `past 30 days`, `previous 30 days` -> `30D`.
- `last 7 days`, `past week` when used as rolling days -> `7D`.
- `yesterday` -> `1D`.
- `current month`, `this month`, `month to date`, `MTD` -> `MTD`.
- `last month`, `previous month`, `M1` -> `M1`.
- `month before last`, `M2` -> `M2`.
- `last 3 months` -> `M3`, default bounded as previous 3 completed months.
- `last 3 months till date`, `including current month` -> `M3_TD`.
- `last week` as completed calendar week -> `W1`.
- `last 2 weeks` as completed calendar weeks -> `W2`.
- `last 14 days` -> `14D`; divisor semantics are days, not weeks.
- Missing time -> `none`.

## Comparison periods

For a metrics request, extract two role-bearing period tokens instead of letting
the last phrase overwrite the first:

- `2 months ago compared with last month` -> older `M2`, newer `M1`.
- `M3 to M2 decline` -> older `M3`, newer `M2`.
- `last week versus the week before` -> newer `W1`, older `W2`.

Preserve the period relationship even when the sentence mentions the newer
period first. Let the metrics skill decide whether the requested operation is a
decline, uplift, ratio, percentage change, or an absolute comparison.

## Tenure / age phrases are NOT time windows

A duration that describes how long the customer has existed on the network is a
FILTER on an age/tenure column, never the KPI's `time_token`. Do not let it set
the window. Keep `time_token = none` if that duration is the only duration in
the sentence.

These map to an age/tenure filter (operator + N), not a time token:

- "been on the network for more than 300 days" -> filter `AON > 300`, time `none`.
- "age in the network is more than 65 days" -> filter `AON > 65`, time `none`.
- "active for more than 35 days" -> filter `AON > 35`, time `none`.
- "network age greater than 50 days" -> filter `AON > 50`, time `none`.

Contrast (these ARE windows on the measured event):

- "recharges in the last 300 days" -> `300D` (the count is measured over 300 days).
- "data usage over the last 30 days" -> `30D`.

Rule of thumb: if the duration answers "how long has the subscriber been on the
network / active", it is a tenure filter. If it answers "over what period is the
KPI measured", it is the time window.
