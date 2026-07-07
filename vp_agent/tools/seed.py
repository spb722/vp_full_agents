from __future__ import annotations

import re
from typing import Any

from vp_agent.data import load_seed_catalog
from vp_agent.schemas import SeedCandidate
from vp_agent.text import phrase_text, tokens
from vp_agent.tools.retrieval_index import char_ngrams, cosine, expand_tokens


TIME_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[dDwWmM])$")

DATE_COL_BY_GROUP = {
    "Recharge_Seg_Fct": "RECHARGE_Event_Date",
    "Common_Seg_Fct": "COMMON_Event_Date",
    "Instant_cdr_group": "created_date",
}

DATE_COL_BY_PREFIX = {
    "RECHARGE_": "RECHARGE_Event_Date",
    "COMMON_": "COMMON_Event_Date",
    "S_": "S_FCT_DT",
    "I_": "created_date",
}

COUNT_WORDS = {"count", "number", "frequency", "times", "transactions", "occurrences"}
AVERAGE_WORDS = {"average", "avg", "mean"}
METRIC_WORDS = {"uplift", "downlift", "increase", "decrease", "change", "growth"}
SUM_WORDS = {"sum", "total", "amount", "revenue", "usage", "volume", "spent", "spend"}
PRESENCE_WORDS = {"exists", "exist", "present", "available", "has"}


def normalize_time_token(raw: object) -> dict[str, Any]:
    token = str(raw or "").strip()
    lower = token.lower()
    if not lower or lower in {"none", "unknown", "null"}:
        return {"raw": token, "required": False, "unit": None, "n": None, "axis_keys": []}
    if lower == "mtd":
        return {"raw": token, "required": True, "unit": "MONTH_TO_DATE", "n": None, "axis_keys": ["mtd"]}
    if lower.endswith("_td"):
        parsed = normalize_time_token(lower.removesuffix("_td"))
        return {**parsed, "raw": token, "till_date": True}

    match = TIME_RE.match(lower)
    if not match:
        return {"raw": token, "required": True, "unit": None, "n": None, "axis_keys": [lower]}

    n = int(match.group("n"))
    unit_code = match.group("unit").lower()
    if unit_code == "d":
        return {"raw": token, "required": True, "unit": "DAYS", "n": n, "axis_keys": [f"{n}d"]}
    if unit_code == "w":
        return {"raw": token, "required": True, "unit": "WEEKS", "n": n, "axis_keys": [f"w{n}", f"{n}w"]}
    return {"raw": token, "required": True, "unit": "MONTHS", "n": n, "axis_keys": [f"m{n}", f"{n}m"]}


def _seed_client_ok(seed_client: str, client: str) -> bool:
    return seed_client in {client, "both", "global", ""}


def _seed_text(seed: dict[str, Any]) -> str:
    parts = [seed.get("seed_id", ""), seed.get("description", ""), seed.get("output_template", "")]
    axes = seed.get("axes") or {}
    for axis in axes.values():
        if not isinstance(axis, dict):
            continue
        for value in axis.values():
            if isinstance(value, dict):
                parts.extend(value.get("input_phrases") or [])
    sig = seed.get("selection_signature") or {}
    parts.append(str(sig.get("axes_summary", "")))
    parts.append(str(sig.get("seed_family", "")))
    parts.append(str(sig.get("agg_type", "")))
    return " ".join(map(str, parts))


def _required_variables(seed: dict[str, Any]) -> tuple[str, ...]:
    sig = seed.get("selection_signature") or {}
    runtime = sig.get("runtime") or {}
    variables = runtime.get("template_variables")
    if variables:
        return tuple(v for v in variables if v not in {"operator", "value"})
    template = seed.get("output_template") or ""
    return tuple(sorted(set(re.findall(r"(?<!\$)\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template))))


def _axis_time_match(seed: dict[str, Any], normalized_time: dict[str, Any]) -> tuple[float, dict[str, Any], str | None]:
    axes = seed.get("axes") or {}
    time_axes = axes.get("time") or axes.get("week") or axes.get("variant") or {}
    if not isinstance(time_axes, dict) or not normalized_time["axis_keys"]:
        return 0.0, {}, None

    for key in normalized_time["axis_keys"]:
        if key in time_axes and isinstance(time_axes[key], dict):
            return 12.0, dict(time_axes[key]), key

    n = normalized_time.get("n")
    if n is not None:
        for key, value in time_axes.items():
            if isinstance(value, dict) and value.get("N") == n:
                return 8.0, dict(value), str(key)
    return 0.0, {}, None


def _kpi_axis_score(seed: dict[str, Any], kpi_phrase: str) -> float:
    axes = seed.get("axes") or {}
    phrase_vec = char_ngrams(kpi_phrase)
    best = 0.0
    for axis_name in ("kpi", "variant"):
        axis = axes.get(axis_name) or {}
        if not isinstance(axis, dict):
            continue
        for key, value in axis.items():
            phrases = value.get("input_phrases") if isinstance(value, dict) else []
            axis_text = " ".join([str(key), *map(str, phrases or [])])
            token_overlap = len(set(expand_tokens(tokens(kpi_phrase))) & set(expand_tokens(tokens(axis_text))))
            semantic = cosine(phrase_vec, char_ngrams(axis_text)) if phrase_vec else 0.0
            best = max(best, token_overlap * 4.0 + semantic * 8.0)
    return best


def _intent_score(sig: dict[str, Any], slot_terms: set[str], table: str | None) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    agg_type = str(sig.get("agg_type") or "").upper()
    seed_type = str(sig.get("seed_type") or "")
    formula = sig.get("formula") or {}
    guards = sig.get("guards") or {}

    if slot_terms & AVERAGE_WORDS:
        if formula.get("has_formula") or agg_type == "FORMULA":
            score += 14
            reasons.append("average/formula intent")
        else:
            score -= 5
    elif slot_terms & COUNT_WORDS:
        if agg_type == "COUNT_ALL" or seed_type == "count":
            score += 12
            reasons.append("count intent")
        elif agg_type == "SUM":
            score -= 3
    elif slot_terms & SUM_WORDS:
        if agg_type == "SUM":
            score += 10
            reasons.append("sum intent")
        elif agg_type == "RAW" and table == "360_PROFILE":
            score += 8
            reasons.append("precomputed raw KPI")

    if slot_terms & PRESENCE_WORDS and (guards.get("has_not_null_guard") or guards.get("not_null_guard")):
        score += 6
        reasons.append("presence/not-null intent")

    if slot_terms & METRIC_WORDS:
        if seed_type in {"derived_metric", "composite"} or formula.get("selection_intent"):
            score += 14
            reasons.append("metric comparison intent")
        else:
            score -= 10

    return score, reasons


def _infer_column(columns: list[dict[str, Any]], role: str, table: str | None = None) -> str | None:
    if role in {"kpi_col", "count_col", "col"}:
        for column in columns:
            group = column.get("group_name")
            if table and table != "360_PROFILE" and group != table:
                continue
            data_type = str(column.get("data_type", "")).lower()
            if role == "kpi_col" and data_type == "numeric":
                return column.get("feature_name")
            if role in {"count_col", "col"}:
                return column.get("feature_name")
        return columns[0].get("feature_name") if columns else None

    if role == "date_col":
        for column in columns:
            if str(column.get("data_type", "")).lower() == "date":
                return column.get("feature_name")
        for column in columns:
            feature = str(column.get("feature_name") or "")
            for prefix, date_col in DATE_COL_BY_PREFIX.items():
                if feature.startswith(prefix):
                    return date_col
            group = column.get("group_name")
            if group in DATE_COL_BY_GROUP:
                return DATE_COL_BY_GROUP[group]
    return None


def _suggest_variables(
    seed: dict[str, Any],
    required_variables: tuple[str, ...],
    axis_values: dict[str, Any],
    columns: list[dict[str, Any]],
    table: str | None,
    normalized_time: dict[str, Any],
    slots: dict[str, Any],
) -> dict[str, Any]:
    variables: dict[str, Any] = {}
    for key in ("N", "start", "end", "divisor"):
        if key in required_variables and key in axis_values:
            variables[key] = axis_values[key]
    if "N" in required_variables and "N" not in variables and normalized_time.get("n") is not None:
        variables["N"] = normalized_time["n"]
    if "divisor" in required_variables and "divisor" not in variables and normalized_time.get("n") is not None:
        variables["divisor"] = normalized_time["n"]

    for role in ("kpi_col", "count_col", "col", "date_col"):
        if role in required_variables:
            inferred = _infer_column(columns, role, table)
            if inferred:
                variables[role] = inferred

    if "vp_name" in required_variables:
        phrase = str(slots.get("kpi_phrase") or "VP")
        time = str(slots.get("time_token") or "").upper()
        name = re.sub(r"[^A-Za-z0-9]+", "_", f"{phrase}_{time}").strip("_").upper()
        variables["vp_name"] = name or "VP_FORMULA"

    return variables


def select_seed(
    slots: dict[str, Any],
    client: str,
    columns: list[dict[str, Any]] | None = None,
    table: str | None = None,
    exclude: list[str] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    client = client.lower().strip()
    exclude_set = set(exclude or [])
    columns = columns or []
    normalized_time = normalize_time_token(slots.get("time_token"))
    query_text = phrase_text(slots)
    kpi_phrase = str(slots.get("kpi_phrase") or "")
    slot_terms = set(expand_tokens(tokens(query_text)))
    query_vec = char_ngrams(query_text)

    candidates: list[SeedCandidate] = []
    for seed in load_seed_catalog().get("seeds", []):
        seed_id = str(seed.get("seed_id") or "")
        if seed_id in exclude_set:
            continue
        seed_client = str(seed.get("client") or "")
        if not _seed_client_ok(seed_client, client):
            continue

        sig = seed.get("selection_signature") or {}
        seed_time = sig.get("time") or {}
        seed_requires_time = bool(seed_time.get("required"))
        required_variables = _required_variables(seed)
        axis_score, axis_values, axis_key = _axis_time_match(seed, normalized_time)

        score = 0.0
        reasons: list[str] = []

        if seed_client == client:
            score += 8
            reasons.append("client exact")
        elif seed_client in {"both", "global"}:
            score += 4
            reasons.append("client shared")

        if table == "360_PROFILE" and seed_id == "S161_raw_kpi_no_time":
            score += 36
            reasons.append("360 precomputed KPI raw comparison")
        elif table == "360_PROFILE" and seed_requires_time:
            score -= 16

        if normalized_time["required"]:
            if seed_requires_time:
                score += 5
                reasons.append("time required")
                if axis_score:
                    score += axis_score
                    reasons.append(f"time axis {axis_key}")
                else:
                    units = set(seed_time.get("units") or [])
                    if normalized_time.get("unit") in units:
                        score += 6
                        reasons.append("time unit match")
                    else:
                        score -= 8
            elif table != "360_PROFILE":
                score -= 14
                reasons.append("missing time window")
        elif seed_requires_time:
            score -= 8

        if normalized_time.get("till_date") and seed_time.get("has_completed_period_upper_bound"):
            score -= 6
            reasons.append("till-date conflicts with completed upper bound")

        kpi_score = _kpi_axis_score(seed, kpi_phrase)
        if kpi_score:
            score += kpi_score
            reasons.append(f"kpi axis score {kpi_score:.1f}")

        semantic = cosine(query_vec, char_ngrams(_seed_text(seed))) if query_vec else 0.0
        score += semantic * 12
        if semantic:
            reasons.append(f"semantic {semantic:.2f}")

        intent, intent_reasons = _intent_score(sig, slot_terms, table)
        score += intent
        reasons.extend(intent_reasons)

        if "${operator} ${value}" in str(seed.get("output_template")):
            score += 4
            reasons.append("runtime operator/value")

        composition = sig.get("composition") or {}
        if composition.get("can_be_main_condition") is False:
            score -= 8

        suggested = _suggest_variables(seed, required_variables, axis_values, columns, table, normalized_time, slots)
        missing = sorted(v for v in required_variables if v not in suggested)
        if missing:
            score -= min(12, len(missing) * 3)
            reasons.append("missing variables: " + ",".join(missing))
        else:
            score += 4
            reasons.append("variables inferred")

        if score <= 0:
            continue

        candidates.append(
            SeedCandidate(
                seed_id=seed_id,
                description=str(seed.get("description") or ""),
                client=seed_client,
                output_template=str(seed.get("output_template") or ""),
                score=score,
                confidence=0.0,
                reason="; ".join(reasons),
                required_variables=required_variables,
                suggested_variables=suggested,
                selection_signature=sig,
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    top = candidates[:top_k]
    if not top:
        return {"selected": None, "candidates": [], "normalized_time": normalized_time}

    best = top[0].score
    second = top[1].score if len(top) > 1 else 0.0
    margin = min((best - second) / max(best, 1.0), 0.24)
    normalized_top = []
    for candidate in top:
        confidence = max(0.05, min(0.99, (candidate.score / max(best, 1.0)) * (0.75 + margin)))
        normalized_top.append({**candidate.__dict__, "confidence": confidence})

    return {
        "selected": normalized_top[0],
        "candidates": normalized_top,
        "normalized_time": normalized_time,
    }
