from __future__ import annotations

from vp_agent.schemas import RouteDecision


DOMAIN_TABLES = {
    "profile": "Profile_Cdr_group",
    "recharge": "Recharge_Seg_Fct",
    "usage": "Common_Seg_Fct",
    "subscription": "Subscriptions",
    "lifecycle": "LIFECYCLE_CDR",
    "campaign": "LIFECYCLE_CDR",
    "audience_segment": "AUDIENCE_SEGMENT_CDR",
}


def route_table(domain: str, token: str, kpi_group: str | None = None, shelf_on_360: bool | None = None) -> RouteDecision:
    normalized_group = (kpi_group or "").strip()
    normalized_domain = (domain or "unknown").strip().lower()

    if normalized_group == "360_PROFILE" or shelf_on_360:
        return RouteDecision(
            table="360_PROFILE",
            reason="matching precomputed 360 Profile KPI is available",
            variant_hint="variant_1_raw_or_precomputed",
        )

    if normalized_group:
        return RouteDecision(
            table=normalized_group,
            reason="candidate KPI metadata already identifies the group",
            variant_hint="variant_1",
        )

    table = DOMAIN_TABLES.get(normalized_domain, "Common_Seg_Fct")
    return RouteDecision(
        table=table,
        reason=f"domain '{normalized_domain}' routed by deterministic domain map",
        variant_hint="variant_1" if normalized_domain != "unknown" else "needs_disambiguation",
    )

