# Golden Pattern Memory

These reviewed patterns are examples for semantic reasoning. Use them to steer
column/seed choice, then confirm against retrieved KPI metadata.

## Snapshot KPIs

If a Customer 360 or precomputed KPI column already contains the period, render
it as a raw comparison:

- "finance voice services last 1 month" -> `TOTAL_VOICE_REVENUE_FINANCE_REV_M1`
- "total revenue for a subscriber last 30 days" -> `360_Total_Rev_Voice_30`
- "data bundle revenue last 1 month" -> `TOTAL_DATA_BUNDLE_REVENUE_M1`
- "recharge transactions last 90 days" -> `Recharge_Count_90D`
- "offnet finance revenue last month" -> `CUST_360_VOICE_REVENUE_OFFNET_FINANCE_REV_M1`
- "local voice subscription services current month till date" -> `360_Local_Voice_Plan_Rev_MTD`
- "local financial services last 15 days" -> `CUST_360_DATA_REVENUE_LOCAL_FINANCE_REV_15D`
- "local financial services week 6" -> `CUST_360_DATA_REVENUE_LOCAL_FINANCE_REV_W6`
- "total roaming financial services last 4 weeks" -> `CUST_360_TOTAL_ROAMING_REV_FINANCE_REV_W4`
- "data roaming financial services month 1" -> `CUST_360_DATA_REVENUE_ROAMING_FINANCE_REV_M1`

Snapshot rule: do not add `CurrentMonth`, `CurrentWeek`, `CurrentTime`,
`Event_Date`, `FCT_DT`, or `SUM(...)` around the snapshot KPI itself. Filters
can appear before the snapshot comparison.

## Event Time Windows

Use event/summarized date bounds when the time range is not already encoded in
the KPI column:

- "data usage last 2 days" -> date `FCT_DT >= CurrentTime-2DAYS`, aggregate
  `SUM(Total_Data_usage)`.
- "voice revenue last 2 days" -> date `COMMON_FCT_DT >= CurrentTime-2DAYS`,
  aggregate `SUM(Total_Voice_Revenue)`.
- "data revenue last 2 days" -> date `COMMON_FCT_DT >= CurrentTime-2DAYS`,
  aggregate `SUM(Total_Data_Revenue)`.
- "prepaid SMS revenue last one month" -> date
  `COMMON_Event_Date >= CurrentMonth-1MONTHS`, aggregate
  `SUM(COMMON_Prepay_Sms_Revenue)`.

Use `CurrentTime-NDAYS` for rolling day windows, `CurrentWeek-NWEEKS` for week
windows, and `CurrentMonth-NMONTHS` for month windows unless a retrieved
snapshot column already covers that same period.

## Service Direction And Revenue Families

Revenue language must preserve service direction and product family:

- "outgoing on-net SMS" -> onnet SMS revenue such as `OG_SMS_Onnet_Revenue`.
- "outgoing off-net SMS" -> offnet SMS revenue such as
  `OG_SMS_Offnet_Revenue` or `COMMON_OG_Local_Offnet_Sms_Revenue`.
- "international outgoing calls" or "IDD calls" -> IDD voice revenue such as
  `COMMON_OG_IDD_Call_Revenue`.
- "local network pay-as-you-go data" -> local PayG data volume/revenue family,
  not bundle/free data.
- "bundled data usage" -> bundle data revenue family such as
  `COMMON_Data_Local_Bundle_Revenue` or `COMMON_Data_Bundle_Revenue`.
- "free data usage" -> free data revenue family such as
  `COMMON_Data_Free_Revenue`.
- "financial services" is usually represented by `FINANCE_REV` snapshot
  columns when those candidates match the service family and period.

## Aggregate Intent

Choose the seed/aggregation that matches the marketer's intent:

- "total" -> `SUM(...)` unless a snapshot KPI is selected.
- "number/count of recharge transactions" -> `Recharge_Count_90D` snapshot
  if available for 90 days, otherwise a count/frequency seed.
- "number of subscription purchases" -> subscription event date plus
  `COUNT_ALL(VAS_SUBSCRIPTIONS_SUBSCRIPTION_TYPE)`.
- "average daily revenue last 90 days" -> formula dividing by 90, for example
  `V{AVG_DAILY_COMMON_Data_Bundle_Revenue}=f{COMMON_Data_Bundle_Revenue/90}`.
- "average weekly revenue last 4 weeks" -> formula dividing by 4, for example
  `V{AVG_WEEKLY_COMMON_OG_Call_Revenue}=f{COMMON_OG_Call_Revenue/4}`.
- "20% of recharge amount" -> formula multiplying recharge amount by `0.2`.
- "maximum data usage" -> `MAX(...)`, not `SUM(...)`.
- "purchased product 123 or 125" as a standalone audience filter -> product
  list plus `COUNT_ALL(SUBSCRIPTIONS_Product_Id) > 0`.

## Common Filters

Keep filters before the KPI comparison:

- smartphone users -> handset type filter.
- iPhone users -> handset type filter for iPhone.
- active/inactive subscribers -> subscriber status filter or list.
- prepaid customers/base/recharges -> line type or recharge type prepaid,
  depending on the retrieved candidate family.
- on-network age, active days, or "been on the network" -> AON or profile age
  filter. Prefer metadata-supported candidate names.
- nationality such as Indian or Omani -> nationality filter.
- product 123 or 125 -> product-id `IN LIST` filter.

If a filter itself has a time phrase, keep that time condition attached to the
filter source. Example: product subscription in the last 45 days can require a
subscription event date condition even when the main KPI uses another table.

## Predicate Filters (membership, range, null, comparison)

Filters are predicates. Operator tokens, operand shapes, and exact syntax are
defined in `vp-rendering-rules/references/operator-catalog.md`; the examples
below show how marketer language maps to them.

When one attribute is given several allowed values, render one membership
filter, not several equality conditions. Canonical syntax:
`COLUMN IN LIST (v1;v2;v3)` — semicolon separated, numeric/single-token values
bare, space-containing values single-quoted.

Reviewed golden and production examples:

- "products 123 or 125" ->
  `SUBSCRIPTIONS_Product_Id IN LIST (123;125)`, usually with
  `COUNT_ALL(SUBSCRIPTIONS_Product_Id) > 0` when the list is the whole audience.
- "active or inactive subscribers" ->
  `Profile_Cdr_Subscriber_Status IN LIST (active;inactive)`.
- "feature phones, smartphones" ->
  `Profile_Cdr_Handset_Type IN LIST ('feature phone';'smartphone')`.
- "smartphone or iPhone users" ->
  `Profile_Cdr_Handset_Type IN LIST (smartphone;iPhone)`.
- promotion/bonus delivery checks ->
  `LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)` or
  `LC_ACTION_TYPE IN LIST (BONUS;Bonus;bonus)`.
- refill pack membership ->
  `RE_REFILL_ID IN LIST (MD03;M138;M139;M140)`, with `COUNT_ALL(RE_REFILL_ID)`.

Decision cue: alternatives for the SAME attribute joined by "or"/commas ->
IN LIST. Constraints on DIFFERENT attributes (prepaid + smartphone) -> separate
AND filters, never a list.

## Verification Traps

- A count/sum with NO stated period must be a raw `COUNT_ALL(...)` / `SUM(...)`,
  not a period snapshot. "count of recharges performed by ... customers" (no
  period) -> `COUNT_ALL(Recharge_count)`, NOT `CUST_360_RECHARGE_COUNT_90D` and
  NOT any `_90D`/`_30D` column. Only use a `_Nd`/`_M*`/`_W*` snapshot when the
  user stated that exact period.
- Tenure/age ("on the network more than 300 days", "age in network > 65 days",
  "active for 35 days") is a filter `AON > N`, never a time window and never a
  reason to select a period snapshot or add a date bound.
- Do not convert every "last month" request to event-table dates. If retrieval
  has a matching `M1` snapshot KPI, prefer the raw snapshot comparison.
- Do not add a global date condition just because the marketer mentioned time.
  First decide whether the selected KPI column already encodes that time.
- Do not collapse onnet, offnet, local, roaming, IDD, bundle, free, PayG, and
  finance-service revenue into generic revenue.
- Do not use a formula seed for plain "total" requests.
- Do not split a single multi-value attribute ("smartphone or iPhone") into
  `handset = smartphone AND handset = iPhone`. That intersection is always
  empty. Use one `IN LIST`.
- Do not quote the whole list: `IN LIST "(123;125)"` is wrong. Quote only
  individual space-containing members, and never the parentheses.
- Do not use commas as the list separator; the separator is always `;`.
- A membership filter has fixed values; it must not carry the runtime
  `${operator} ${value}` pair.
- Do not expose these golden examples as exact user-facing explanations unless
  the user asks for debugging detail.