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

