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

## Verification Traps

- Do not convert every "last month" request to event-table dates. If retrieval
  has a matching `M1` snapshot KPI, prefer the raw snapshot comparison.
- Do not add a global date condition just because the marketer mentioned time.
  First decide whether the selected KPI column already encodes that time.
- Do not collapse onnet, offnet, local, roaming, IDD, bundle, free, PayG, and
  finance-service revenue into generic revenue.
- Do not use a formula seed for plain "total" requests.
- Do not expose these golden examples as exact user-facing explanations unless
  the user asks for debugging detail.
