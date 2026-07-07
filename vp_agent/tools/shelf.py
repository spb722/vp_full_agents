from __future__ import annotations

from typing import Any

from vp_agent.data import load_kpi_meta
from vp_agent.text import token_counter, tokens


def shelf_lookup(token: str, client: str) -> dict[str, Any]:
    query_terms = set(tokens(token))
    matches = []
    for row in load_kpi_meta():
        if row.group_name != "360_PROFILE":
            continue
        haystack = token_counter(row.feature_name, row.description, row.kpi_type_name, row.time_window_value)
        score = sum(haystack[t] for t in query_terms)
        if score:
            matches.append(
                {
                    "feature_name": row.feature_name,
                    "group_name": row.group_name,
                    "time_window_value": row.time_window_value,
                    "description": row.description,
                    "score": score,
                }
            )
    matches.sort(key=lambda item: item["score"], reverse=True)
    return {
        "on_shelf": bool(matches),
        "client": client,
        "token": token,
        "matches": matches[:8],
    }

