from __future__ import annotations

from typing import Any


# Stable routing primitives belong in deterministic configuration, not prompts.
GROUP_DATE_COLUMN_MAP: dict[str, str] = {
    "Instant_cdr_group": "FCT_DT",
    "Common_Seg_Fct": "COMMON_Event_Date",
    "Subscriptions": "SUBSCRIPTIONS_DT",
    "Recharge_Seg_Fct": "RECHARGE_Event_Date",
    "LIFECYCLE_CDR": "L_SENT_DATE",
}


SUBSCRIPTION_EVENT_DATE_COLUMN = "SUBSCRIPTIONS_EVENT_DATE"
SUBSCRIPTION_EVENT_TERMS = frozenset(
    {
        "cancel",
        "cancellation",
        "cancelled",
        "renew",
        "renewal",
        "renewed",
    }
)


DOMAIN_GROUP_PREFERENCES: dict[str, tuple[str, ...]] = {
    "usage": ("Common_Seg_Fct", "Instant_cdr_group", "360_PROFILE"),
    "recharge": ("Recharge_Seg_Fct", "Instant_cdr_group", "360_PROFILE"),
    "subscription": ("Subscriptions", "360_PROFILE"),
    "lifecycle": ("LIFECYCLE_CDR", "360_PROFILE"),
    "profile": ("Profile_Cdr_group", "360_PROFILE"),
}


def date_column_for_group(group_name: str, slots: dict[str, Any] | None = None) -> str | None:
    """Return the configured event date without doing semantic column retrieval."""
    if group_name == "Subscriptions" and slots:
        text = " ".join(
            str(slots.get(key) or "")
            for key in ("raw_request", "kpi_phrase", "metric", "event_type")
        ).lower()
        if any(term in text for term in SUBSCRIPTION_EVENT_TERMS):
            return SUBSCRIPTION_EVENT_DATE_COLUMN
    return GROUP_DATE_COLUMN_MAP.get(group_name)
