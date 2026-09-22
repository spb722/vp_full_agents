from __future__ import annotations

import re
from typing import Any


# Stable routing primitives belong in deterministic configuration, not prompts.
# Measured against the 713 client VPs: a configured column that production never
# uses is simply wrong. Two entries named columns with zero usage — FCT_DT for
# Instant_cdr_group (created_date is used in 8 rules) and SUBSCRIPTIONS_DT for
# Subscriptions (SUBSCRIPTIONS_EVENT_DATE is used in 10). Change an entry here
# when the client's convention changes; nothing else needs touching.
GROUP_DATE_COLUMN_MAP: dict[str, str] = {
    "Instant_cdr_group": "created_date",
    "Common_Seg_Fct": "COMMON_Event_Date",
    "Subscriptions": "SUBSCRIPTIONS_EVENT_DATE",
    "Recharge_Seg_Fct": "RECHARGE_Event_Date",
    "LIFECYCLE_CDR": "L_SENT_DATE",
}


# LIFECYCLE_CDR has no single event date: production uses L_PROMO_SENT_DATE in 47
# rules, L_BONUS_SENT_DATE in 12, and the generic L_SENT_DATE in 16. The event
# type the request already names decides which, so a fixed default put the wrong
# date column into every promotion and bonus rule.
LIFECYCLE_DATE_BY_EVENT: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"promo", "promotion", "promotions", "promotional"}), "L_PROMO_SENT_DATE"),
    (frozenset({"bonus", "bonuses"}), "L_BONUS_SENT_DATE"),
)


# Kept as an override even though it now agrees with the default: if the
# Subscriptions default is ever set back to a purchase-side date column, cancel
# and renew events still have to resolve to the event date.
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


def _slot_text(slots: dict[str, Any]) -> str:
    """Request wording plus filter phrases and values, lowercased."""
    parts = [str(slots.get(key) or "") for key in ("raw_request", "kpi_phrase", "metric", "event_type")]
    for item in slots.get("filters") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("phrase") or ""))
            value = item.get("value")
            parts.extend(str(v) for v in (value if isinstance(value, list) else [value]) if v is not None)
        else:
            parts.append(str(item))
    return " ".join(parts).lower()


def date_column_for_group(group_name: str, slots: dict[str, Any] | None = None) -> str | None:
    """Return the configured event date without doing semantic column retrieval."""
    if group_name == "LIFECYCLE_CDR" and slots:
        words = set(re.findall(r"[a-z0-9]+", _slot_text(slots)))
        for terms, column in LIFECYCLE_DATE_BY_EVENT:
            if words & terms:
                return column
    if group_name == "Subscriptions" and slots:
        text = " ".join(
            str(slots.get(key) or "")
            for key in ("raw_request", "kpi_phrase", "metric", "event_type")
        ).lower()
        if any(term in text for term in SUBSCRIPTION_EVENT_TERMS):
            return SUBSCRIPTION_EVENT_DATE_COLUMN
    return GROUP_DATE_COLUMN_MAP.get(group_name)
