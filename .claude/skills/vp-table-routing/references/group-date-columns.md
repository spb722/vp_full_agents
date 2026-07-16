# Group Date Routing

Use this reference only when reviewing event-time routing or a date-coverage
failure. Deterministic configuration is authoritative; do not retrieve these
default dates as ordinary KPI candidates.

| Group | Default event date |
|---|---|
| `Instant_cdr_group` | `FCT_DT` |
| `Common_Seg_Fct` (Summary CDR) | `COMMON_Event_Date` |
| `Subscriptions` | `SUBSCRIPTIONS_DT` |
| `Recharge_Seg_Fct` | `RECHARGE_Event_Date` |
| `LIFECYCLE_CDR` | `L_SENT_DATE` |

For subscription renewal or cancellation events, use
`SUBSCRIPTIONS_EVENT_DATE` instead of the subscription default. Keep
`L_SENT_DATE` as the lifecycle default until a reviewed event-specific override
is added to deterministic configuration.

Treat the compact retrieval candidate's `time_window_support` as the normal
agent-facing evidence. Read this mapping only when resolving why an event KPI
does or does not support a custom window.
