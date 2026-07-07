---
name: vp-table-routing
description: Use after extraction and before column resolution when deciding whether a KPI should use 360 Profile, summarized/event tables, profile tables, recharge tables, subscriptions, lifecycle, or audience segment tables.
---

# VP Table Routing

Routing principles:

1. Prefer Customer 360 / 360 Profile if a matching precomputed KPI exists for
   the requested KPI and time window.
2. Use summarized/event tables when the request needs a custom combined range
   that is not available as one 360 KPI.
3. Use profile tables for static customer attributes such as nationality,
   handset type, language, DOB, status, activation date, and locality.
4. Use recharge fact tables for recharge amount, denomination, top-up channel,
   top-up type, and recharge frequency when not already on 360.
5. Use subscription/purchase history for product or pack purchases.
6. Use lifecycle tables for campaign actions, bonuses, previous promotion
   checks, and action-key checks.
7. Use audience segment only for explicit AM/audience segment membership.

For multi-table Variant 2, order filters before aggregates and keep aggregate
conditions last.

Tools can propose a route, but the resolver should inspect the request and KPI
business meaning. If a Customer 360 snapshot column already represents the
requested period, use it as a raw condition and do not add event-date bounds for
that KPI. If a separate KPI/filter comes from event data, that separate
condition may still need its own date bounds.
