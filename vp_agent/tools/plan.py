from __future__ import annotations

from dataclasses import asdict
from typing import Any

from vp_agent.data import load_kpi_meta
from vp_agent.schemas import Candidate
from vp_agent.tools.render import render_condition
from vp_agent.tools.retrieve import retrieve_columns
from vp_agent.tools.router import route_table
from vp_agent.tools.seed import select_seed
from vp_agent.tools.validate import validate_rule


STATIC_FILTER_DOMAIN = "profile"
SNAPSHOT_TOKEN_RE = __import__("re").compile(
    r"(^|_)(?:M(?:TD|[1-9]|1[0-2])|LMTD|W[1-9]|FW\d+|\d+D(?:_\d+D)?|\d+D_\d+D)(_|$)",
    __import__("re").I,
)


def _candidate_dict(candidate: Candidate) -> dict[str, Any]:
    return asdict(candidate)


def is_customer_360_snapshot(candidate: Candidate | dict[str, Any]) -> bool:
    group = candidate.group_name if isinstance(candidate, Candidate) else str(candidate.get("group_name") or "")
    if group != "360_PROFILE":
        return False
    time_window = candidate.time_window_value if isinstance(candidate, Candidate) else str(candidate.get("time_window_value") or "")
    feature_name = candidate.feature_name if isinstance(candidate, Candidate) else str(candidate.get("feature_name") or "")
    if time_window.strip():
        return True
    return bool(SNAPSHOT_TOKEN_RE.search(feature_name))


def _numeric_threshold(slots: dict[str, Any]) -> bool:
    operator = str(slots.get("operator") or "")
    value = str(slots.get("value") or "")
    return operator in {">", ">=", "<", "<=", "between"} or value.replace(".", "", 1).isdigit()


def _main_column_score(candidate: Candidate, slots: dict[str, Any], prefer_360: bool, force_event: bool) -> float:
    score = candidate.score * 100
    feature = candidate.feature_name.upper()
    group = candidate.group_name
    data_type = candidate.data_type.lower()
    time_token = str(slots.get("time_token") or "").upper()
    domain = str(slots.get("domain") or "").lower()

    if force_event and group == "360_PROFILE":
        score -= 1000
    if prefer_360 and not force_event and group == "360_PROFILE":
        score += 80
    if prefer_360 and not force_event and is_customer_360_snapshot(candidate):
        score += 120
    if domain == "recharge" and group == "Recharge_Seg_Fct":
        score += 140 if force_event else 60
    if domain == "recharge" and group == "Instant_cdr_group":
        score += -40 if force_event else 25
    if domain in {"recharge", "usage"} and group == "Profile_Cdr_group":
        score -= 250
    if domain == "usage" and group in {"Common_Seg_Fct", "360_PROFILE"}:
        score += 35
    if time_token and candidate.time_window_value.upper() == time_token:
        score += 100
    if _numeric_threshold(slots) and data_type == "numeric":
        score += 60
    if _numeric_threshold(slots) and data_type in {"string", "categorical"}:
        score -= 80
    if domain == "recharge" and any(part in feature for part in ("AMOUNT", "DENOMINATION", "REVENUE")):
        score += 35
    if domain == "recharge" and "RECHARGE" in feature:
        score += 140
    if domain == "recharge" and "RECHARGE" not in feature and group == "360_PROFILE":
        score -= 180
    if force_event and domain == "recharge" and "DENOMINATION" in feature:
        score += 45
    return score


def _choose_main_column(slots: dict[str, Any], client: str, prefer_360: bool, force_event: bool) -> Candidate:
    main_slots = {key: value for key, value in slots.items() if key not in {"filters", "negations"}}
    candidates = retrieve_columns(main_slots, client=client, top_k=40)
    if not candidates:
        raise ValueError("no candidate columns found for main KPI")
    domain = str(slots.get("domain") or "").lower()
    if force_event and domain == "recharge":
        recharge_candidates = [
            candidate
            for candidate in candidates
            if candidate.group_name == "Recharge_Seg_Fct" and candidate.data_type.lower() == "numeric"
        ]
        if recharge_candidates:
            return max(
                recharge_candidates,
                key=lambda candidate: (
                    "DENOMINATION" in candidate.feature_name.upper(),
                    "RECHARGE" in candidate.feature_name.upper(),
                    candidate.score,
                ),
            )
        for feature_name in ("RECHARGE_Denomination", "RECHARGE_Revenue", "I_RECHARGE_AMOUNT"):
            for row in load_kpi_meta():
                if row.feature_name == feature_name:
                    return Candidate(
                        id=row.id,
                        feature_name=row.feature_name,
                        group_name=row.group_name,
                        description=row.description,
                        data_type=row.data_type,
                        time_window_value=row.time_window_value,
                        score=0.0,
                        reason="deterministic recharge event fallback",
                    )
    return max(candidates, key=lambda candidate: _main_column_score(candidate, slots, prefer_360, force_event))


def _normalize_filter_value(feature_name: str, value: object) -> str:
    text = str(value or "").strip()
    lower = text.lower()
    feature = feature_name.upper()
    if "HANDSET_TYPE" in feature and lower in {"smartphone", "smartphones", "sp"}:
        return "SP"
    if "HANDSET_TYPE" in feature and lower in {"featurephone", "feature phone", "fp"}:
        return "FP"
    if "NATIONALITY" in feature and lower in {"omani", "oman"}:
        return "OMANI"
    if text and text.replace("_", "").replace("-", "").isalpha():
        return text.upper()
    return text


def _filter_column_score(candidate: Candidate, preferred_group: str | None) -> float:
    score = candidate.score * 100
    if preferred_group and candidate.group_name == preferred_group:
        score += 120
    if not preferred_group and candidate.group_name == "Profile_Cdr_group":
        score += 40
    if candidate.data_type.lower() in {"string", "categorical"}:
        score += 35
    if candidate.data_type.lower() == "numeric":
        score -= 50
    if "DERIVED" in candidate.feature_name.upper():
        score -= 30
    if candidate.feature_name.upper().endswith("_NATIONALITY"):
        score += 20
    return score


def _build_filter_conditions(slots: dict[str, Any], client: str, preferred_group: str | None) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for item in slots.get("filters") or []:
        if not isinstance(item, dict):
            phrase = str(item)
            operator = "="
            value = phrase
        else:
            phrase = str(item.get("phrase") or item.get("name") or item.get("field") or "")
            operator = str(item.get("operator") or "=")
            value = item.get("value", phrase)

        filter_slots = {
            "domain": STATIC_FILTER_DOMAIN,
            "kpi_phrase": f"{phrase} {value}",
            "filters": [{"phrase": phrase, "operator": operator, "value": value}],
        }
        candidates = retrieve_columns(filter_slots, client=client, top_k=12)
        if not candidates:
            raise ValueError(f"no candidate column found for filter: {phrase}")
        selected = max(candidates, key=lambda candidate: _filter_column_score(candidate, preferred_group))
        conditions.append(
            {
                "phrase": phrase,
                "col": selected.feature_name,
                "operator": operator,
                "value": _normalize_filter_value(selected.feature_name, value),
                "column": _candidate_dict(selected),
            }
        )
    return conditions


def build_condition_plan(
    slots: dict[str, Any],
    client: str,
    prefer_360: bool = True,
    force_event: bool = False,
) -> dict[str, Any]:
    main_column = _choose_main_column(slots, client=client, prefer_360=prefer_360, force_event=force_event)
    main_is_snapshot = is_customer_360_snapshot(main_column)
    shelf_on_360 = main_column.group_name == "360_PROFILE"
    route = route_table(
        domain=str(slots.get("domain") or "unknown"),
        token=" ".join(str(slots.get(key) or "") for key in ("kpi_phrase", "time_token")),
        kpi_group=main_column.group_name,
        shelf_on_360=shelf_on_360,
    )

    filter_preferred_group = "360_PROFILE" if route.table == "360_PROFILE" else "Profile_Cdr_group"
    filters = _build_filter_conditions(slots, client=client, preferred_group=filter_preferred_group)
    seed_slots = dict(slots)
    if main_is_snapshot:
        seed_slots["time_token"] = "none"
    seed_result = select_seed(
        slots=seed_slots,
        client=client,
        columns=[_candidate_dict(main_column)],
        table=route.table,
        top_k=5,
    )
    selected_seed = seed_result.get("selected")
    if not selected_seed:
        raise ValueError("no seed selected for condition plan")

    render_filters = [{key: item[key] for key in ("col", "operator", "value")} for item in filters]
    render_input = {
        "seed_id": selected_seed["seed_id"],
        "template": None,
        "variables": selected_seed["suggested_variables"],
        "filters": render_filters,
        "client": client,
    }

    return {
        "client": client,
        "slots": slots,
        "path": "360" if route.table == "360_PROFILE" else "event",
        "snapshot": main_is_snapshot,
        "main_column": _candidate_dict(main_column),
        "filters": filters,
        "route": asdict(route),
        "seed": selected_seed,
        "seed_alternatives": seed_result.get("candidates", [])[1:],
        "render_input": render_input,
    }


def build_parent_condition(
    slots: dict[str, Any],
    client: str,
    request: str = "",
    prefer_360: bool = True,
    force_event: bool = False,
) -> dict[str, Any]:
    plan = build_condition_plan(slots=slots, client=client, prefer_360=prefer_360, force_event=force_event)
    rendered = render_condition(**plan["render_input"])
    validation = validate_rule(
        rendered["parent_condition"],
        request=request or str(slots.get("raw_request") or ""),
        table=plan["route"]["table"],
    )
    return {
        "plan": plan,
        "rendered": rendered,
        "validation": validation,
        "ok": validation["ok"],
    }
