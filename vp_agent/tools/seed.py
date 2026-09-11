from __future__ import annotations

import re
from typing import Any

from vp_agent.data import load_seed_catalog
from vp_agent.domain_config import date_column_for_group
from vp_agent.schemas import SeedCandidate
from vp_agent.text import phrase_text, tokens
from vp_agent.tools.retrieval_index import char_ngrams, cosine, expand_tokens


TIME_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[dDwWmM])$")
PREFIX_TIME_RE = re.compile(r"^(?P<unit>[wWmM])(?P<n>\d+)$")

DATE_COL_BY_PREFIX = {
    "S_": "S_FCT_DT",
}

COUNT_WORDS = {"count", "number", "frequency", "times", "transactions", "occurrences"}
AVERAGE_WORDS = {"average", "avg", "mean"}
METRIC_WORDS = {"uplift", "downlift", "increase", "decrease", "change", "growth"}
SUM_WORDS = {"sum", "total", "amount", "revenue", "usage", "volume", "spent", "spend"}
PRESENCE_WORDS = {"exists", "exist", "present", "available", "has"}

# Axes that describe the measured business phrase. `time`/`week` axes are
# excluded because their phrases match on period wording alone.
PHRASE_AXES_SKIP = {"time", "week"}
TEMPLATE_VAR_ONLY_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Column roles the resolver can infer. `kpi_col` is the measured quantity;
# the identifier roles are grouping/membership/join keys, which must NOT
# resolve to the metric column.
IDENTIFIER_ROLES = frozenset({"key_col", "grp_col", "id_col"})
COLUMN_ROLES = frozenset({"kpi_col", "count_col", "col"}) | IDENTIFIER_ROLES

# Every template variable the resolver fills itself.
RESOLVER_FILLED_ROLES = COLUMN_ROLES | frozenset(
    {"date_col", "N", "start", "end", "divisor", "factor", "vp_name", "threshold"}
)
# Variables deliberately left for the agent to supply while composing: helper
# VP dependencies, join targets, fixed literals, and runtime parameters.
AGENT_SUPPLIED_ROLES = frozenset(
    {"older_vp", "newer_vp", "left_vp", "right_vp", "join_col", "literal", "X"}
)
KNOWN_TEMPLATE_ROLES = RESOLVER_FILLED_ROLES | AGENT_SUPPLIED_ROLES


def _numeric_value(raw: object) -> int | float | None:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _aggregate_intent(value: object) -> str:
    text = str(value or "").upper()
    if "FORMULA" in text:
        return "FORMULA"
    if "AVERAGE" in text or "AVG" in text:
        return "AVG"
    if "COUNT" in text or "NUMBER" in text:
        return "COUNT"
    if "MAX" in text:
        return "MAX"
    if "SUM" in text or "TOTAL" in text:
        return "SUM"
    return text


def _slot_aggregate_intent(slots: dict[str, Any]) -> str:
    formula = slots.get("formula")
    if isinstance(formula, dict) and (formula.get("type") or formula.get("formula_type")):
        return "FORMULA"
    return _aggregate_intent(slots.get("aggregate"))


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

    match = TIME_RE.match(lower) or PREFIX_TIME_RE.match(lower)
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


RUNTIME_PLACEHOLDER_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _required_variables(seed: dict[str, Any]) -> tuple[str, ...]:
    """Variables the resolver must fill before the template can be used.

    `${...}` names are runtime placeholders the engine substitutes later —
    `${operator}`, `${value}`, but also `${NoOfDays}`, `${SCHEDULE_ID}`. The
    renderer preserves them literally, so they are not resolver variables even
    though some seeds list them under `template_variables`.
    """
    template = str(seed.get("output_template") or "")
    runtime_placeholders = set(RUNTIME_PLACEHOLDER_RE.findall(template)) | {"operator", "value"}
    sig = seed.get("selection_signature") or {}
    runtime = sig.get("runtime") or {}
    variables = runtime.get("template_variables")
    if variables:
        return tuple(v for v in variables if v not in runtime_placeholders)
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


def _kpi_axis_score(seed: dict[str, Any], *request_texts: str) -> tuple[float, list[str]]:
    """Score the request against every business axis the seed declares.

    Two corrections to the previous behaviour:

    - Scan every axis except the time axes. Reviewed families such as
      `check_n_products_threshold` keep their marketer phrases under a
      `threshold` axis, so a fixed `kpi`/`variant`/`operands` scan hid their
      strongest evidence from ranking entirely.
    - Score against the raw request as well as the KPI phrase. Axis
      `input_phrases` are whole marketer sentences, so the sentence is the
      right thing to compare them with.
    """
    axes = seed.get("axes") or {}
    probes = [text for text in request_texts if str(text or "").strip()]
    if not probes:
        return 0.0, []
    probe_terms = [set(expand_tokens(tokens(text))) for text in probes]
    probe_vectors = [char_ngrams(text) for text in probes]

    best = 0.0
    best_phrases: list[str] = []
    for axis_name, axis in axes.items():
        if axis_name in PHRASE_AXES_SKIP or not isinstance(axis, dict):
            continue
        for key, value in axis.items():
            phrases = [str(item) for item in ((value.get("input_phrases") if isinstance(value, dict) else []) or [])]
            axis_text = " ".join([str(key), *phrases])
            axis_terms = set(expand_tokens(tokens(axis_text)))
            axis_vector = char_ngrams(axis_text)
            for terms, vector in zip(probe_terms, probe_vectors):
                score = len(terms & axis_terms) * 4.0 + (cosine(vector, axis_vector) if vector else 0.0) * 8.0
                if score > best:
                    best = score
                    best_phrases = phrases[:2]
    return best, best_phrases


def _intent_score(
    sig: dict[str, Any],
    slot_terms: set[str],
    table: str | None,
    slots: dict[str, Any],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    agg_type = str(sig.get("agg_type") or "").upper()
    seed_type = str(sig.get("seed_type") or "")
    formula = sig.get("formula") or {}
    guards = sig.get("guards") or {}
    aggregate = _slot_aggregate_intent(slots)
    comparison = slots.get("comparison")
    formula_slots = slots.get("formula") if isinstance(slots.get("formula"), dict) else {}
    requested_formula_type = str(formula_slots.get("type") or formula_slots.get("formula_type") or "").lower()
    seed_formula_type = str(formula.get("formula_type") or "").lower()

    if aggregate == "FORMULA":
        if formula.get("has_formula") or agg_type == "FORMULA":
            score += 14
            reasons.append("agent-extracted formula intent")
        else:
            score -= 8

    if requested_formula_type:
        if seed_formula_type == requested_formula_type:
            score += 24
            reasons.append(f"formula type match: {requested_formula_type}")
        elif formula.get("has_formula") or agg_type == "FORMULA":
            score -= 12

    if isinstance(comparison, dict) and comparison:
        if seed_type in {"derived_metric", "composite"} or formula.get("selection_intent"):
            score += 22
            reasons.append("agent-extracted period comparison")
        else:
            score -= 12

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


def _infer_column(
    columns: list[dict[str, Any]],
    role: str,
    table: str | None = None,
    slots: dict[str, Any] | None = None,
) -> str | None:
    if role in COLUMN_ROLES:
        in_table = [
            column
            for column in columns
            if not (table and table != "360_PROFILE" and column.get("group_name") != table)
        ]
        pool = in_table or columns
        if not pool:
            return None

        def _first(predicate) -> str | None:
            return next(
                (
                    column.get("feature_name")
                    for column in pool
                    if predicate(str(column.get("data_type", "")).lower())
                ),
                None,
            )

        if role == "kpi_col":
            # A measured quantity is numeric.
            return _first(lambda data_type: data_type == "numeric") or pool[0].get("feature_name")
        if role in IDENTIFIER_ROLES:
            # A grouping/membership key is an identifier, not the measure. Taking
            # the first column here used to hand back the metric itself.
            return _first(lambda data_type: data_type != "numeric") or pool[0].get("feature_name")
        return pool[0].get("feature_name")

    if role == "date_col":
        # Airtel's summarized CDR uses an S_ prefix. Other stable group dates
        # come from domain configuration and do not need retrieval/model tokens.
        for column in columns:
            feature = str(column.get("feature_name") or "")
            for prefix, date_col in DATE_COL_BY_PREFIX.items():
                if feature.startswith(prefix):
                    return date_col
        configured = date_column_for_group(table or "", slots)
        if configured:
            return configured
        for column in columns:
            configured = date_column_for_group(str(column.get("group_name") or ""), slots)
            if configured:
                return configured
        for column in columns:
            if str(column.get("data_type", "")).lower() == "date":
                return column.get("feature_name")
    return None


def _fixed_comparison_variables(seed: dict[str, Any], slots: dict[str, Any]) -> dict[str, Any]:
    """Resolve template variables that a seed declares as a fixed comparison.

    A seed such as `S30_count_threshold_30d` declares
    `fixed_comparisons: [{function: COUNT_ALL, operator: "<=", value: "{threshold}"}]`.
    When the request states the same operator, the stated value IS that
    threshold, so it can be filled deterministically instead of leaving the
    seed unusable.
    """
    sig = seed.get("selection_signature") or {}
    operation = sig.get("operation") or {}
    requested_operator = str(slots.get("operator") or "").strip()
    value = _numeric_value(slots.get("value"))
    if not requested_operator or value is None:
        return {}

    resolved: dict[str, Any] = {}
    for comparison in operation.get("fixed_comparisons") or []:
        if not isinstance(comparison, dict):
            continue
        if str(comparison.get("operator") or "").strip() != requested_operator:
            continue
        match = TEMPLATE_VAR_ONLY_RE.fullmatch(str(comparison.get("value") or "").strip())
        if match:
            resolved[match.group(1)] = value
    return resolved


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

    for name, value in _fixed_comparison_variables(seed, slots).items():
        if name in required_variables and name not in variables:
            variables[name] = value
    for name in ("threshold", "X"):
        if name in required_variables and name not in variables and name in axis_values:
            variables[name] = axis_values[name]
    if "N" in required_variables and "N" not in variables and normalized_time.get("n") is not None:
        variables["N"] = normalized_time["n"]
    if "divisor" in required_variables and "divisor" not in variables and normalized_time.get("n") is not None:
        variables["divisor"] = normalized_time["n"]

    for role in (*sorted(COLUMN_ROLES), "date_col"):
        if role in required_variables:
            inferred = _infer_column(columns, role, table, slots)
            if inferred:
                variables[role] = inferred

    formula_slots = slots.get("formula") if isinstance(slots.get("formula"), dict) else {}
    if "factor" in required_variables:
        factor = formula_slots.get("factor", slots.get("factor"))
        if factor is None:
            percentage = formula_slots.get("percentage", slots.get("percentage"))
            if isinstance(percentage, (int, float)):
                factor = percentage / 100
        if isinstance(factor, (int, float)):
            variables["factor"] = factor

    if "vp_name" in required_variables:
        formula_type = str(formula_slots.get("type") or formula_slots.get("formula_type") or "").lower()
        if formula_type == "percentage_of_kpi" and variables.get("kpi_col") and "factor" in variables:
            factor_token = str(variables["factor"]).replace(".", "_")
            variables["vp_name"] = f"{variables['kpi_col']}_MUL_{factor_token}"
        else:
            phrase = str(slots.get("kpi_phrase") or "VP")
            time = str(slots.get("time_token") or "").upper()
            name = re.sub(r"[^A-Za-z0-9]+", "_", f"{phrase}_{time}").strip("_").upper()
            variables["vp_name"] = name or "VP_FORMULA"

    return variables


SEED_WINDOW_RE = re.compile(r"-\s*\{?(\d+|N)\}?\s*(DAYS|WEEKS|MONTHS)", re.I)


def _seed_window_text(seed: dict[str, Any]) -> str:
    match = SEED_WINDOW_RE.search(str(seed.get("output_template") or ""))
    if not match:
        return "unspecified window"
    return f"{match.group(1)} {match.group(2).upper()}"


def _seed_compatibility_failures(
    seed: dict[str, Any],
    slots: dict[str, Any],
    normalized_time: dict[str, Any],
    table: str | None,
    missing_variables: list[str],
) -> tuple[list[str], list[str]]:
    """Split structural impossibility from things the agent can adapt.

    A hard failure means the template can never become this rule. Everything
    else — a differing window unit, a variable the resolver could not infer —
    is surfaced as an adaptation note so the agent can re-parameterise it. The
    agent composes the final string, so removing those candidates from view
    removed a decision that belongs to the agent.
    """
    sig = seed.get("selection_signature") or {}
    agg_type = str(sig.get("agg_type") or "").upper()
    seed_type = str(sig.get("seed_type") or "").lower()
    aggregate = _slot_aggregate_intent(slots)
    comparison_request = aggregate == "FORMULA" or bool(slots.get("comparison"))
    formula = sig.get("formula") or {}
    time = sig.get("time") or {}
    runtime = sig.get("runtime") or {}
    composition = sig.get("composition") or {}
    failures: list[str] = []
    adaptations: list[str] = []

    formula_compatible = bool(formula.get("has_formula")) or seed_type in {"derived_metric", "composite"}
    if table == "360_PROFILE" and not comparison_request:
        if str(seed.get("seed_id") or "") != "S161_raw_kpi_no_time":
            failures.append("snapshot_requires_generic_raw_seed")
    else:
        if aggregate == "FORMULA" and not formula_compatible:
            failures.append("formula_required")
        elif aggregate == "AVG" and not (formula_compatible or agg_type == "AVG"):
            failures.append("average_structure_required")
        elif aggregate == "COUNT" and agg_type not in {"COUNT", "COUNT_ALL"} and seed_type != "count":
            failures.append("count_structure_required")
        elif aggregate == "SUM" and agg_type != "SUM":
            failures.append("sum_structure_required")
        elif aggregate == "MAX" and agg_type != "MAX":
            failures.append("max_structure_required")

    seed_requires_time = bool(time.get("required"))
    if table == "360_PROFILE":
        if seed_requires_time:
            failures.append("snapshot_must_not_add_time_window")
        if comparison_request and not formula_compatible:
            failures.append("snapshot_comparison_requires_formula_seed")
    elif normalized_time["required"]:
        if not seed_requires_time:
            failures.append("time_window_required")
        units = set(time.get("units") or [])
        normalized_unit = normalized_time.get("unit")
        requested_window = f"{normalized_time.get('n')} {normalized_unit}"
        # A differing window unit is a parameterisation difference, not a
        # structural one: the seed shape still fits, only its window needs
        # rewriting. Keep it visible and let the agent re-parameterise.
        if normalized_unit == "MONTH_TO_DATE":
            if units or time.get("bound_style") != "equality":
                adaptations.append(
                    f"time_unit_adaptation: seed window is {_seed_window_text(seed)}; "
                    "request is month-to-date — re-parameterise the window"
                )
        elif normalized_unit and normalized_unit not in units:
            adaptations.append(
                f"time_unit_adaptation: seed window is {_seed_window_text(seed)}; "
                f"request is {requested_window} — re-parameterise the window"
            )
    elif seed_requires_time:
        failures.append("time_window_not_requested")

    if normalized_time.get("till_date") and time.get("has_completed_period_upper_bound"):
        adaptations.append("till_date_adaptation: drop the completed-period upper bound")
    if composition.get("can_be_main_condition") is False:
        failures.append("cannot_be_main_condition")

    output_template = str(seed.get("output_template") or "")
    uses_runtime_pair = runtime.get("uses_operator_value_placeholders")
    if uses_runtime_pair is False or "${operator} ${value}" not in output_template:
        failures.append("runtime_operator_value_required")

    deferred_dependency_roles = {"older_vp", "newer_vp", "left_vp", "right_vp"}
    unresolved_required = [name for name in missing_variables if name not in deferred_dependency_roles]
    if unresolved_required:
        # The agent composes the final string, so a variable the resolver could
        # not infer is a hand-off, not a disqualification.
        adaptations.append("supply_variables: " + ", ".join(unresolved_required))
    return failures, adaptations


def _structural_summary(signature: dict[str, Any]) -> str:
    time = signature.get("time") or {}
    formula = signature.get("formula") or {}
    guards = signature.get("guards") or {}
    groupby = signature.get("groupby") or {}
    join = signature.get("join") or {}
    parts = [str(signature.get("agg_type") or signature.get("seed_type") or "unknown")]
    if time.get("required"):
        units = "/".join(map(str, time.get("units") or [])) or "time"
        parts.append(f"{time.get('bound_style') or 'window'} {units}")
    if formula.get("has_formula"):
        parts.append(str(formula.get("formula_type") or "formula"))
    if guards.get("has_not_null_guard") or guards.get("not_null_guard"):
        parts.append("not-null guard")
    if groupby.get("required"):
        parts.append("groupby")
    if join.get("required"):
        parts.append("join")
    return "; ".join(parts)


def _structural_fingerprint(signature: dict[str, Any]) -> tuple[Any, ...]:
    time = signature.get("time") or {}
    formula = signature.get("formula") or {}
    guards = signature.get("guards") or {}
    return (
        signature.get("agg_type"),
        time.get("required"),
        time.get("bound_style"),
        tuple(time.get("units") or []),
        formula.get("has_formula"),
        formula.get("formula_type"),
        guards.get("has_not_null_guard") or guards.get("not_null_guard"),
        (signature.get("groupby") or {}).get("required"),
        (signature.get("join") or {}).get("required"),
    )


def _concise_seed_evidence(reason: str) -> str:
    useful = [part.strip() for part in reason.split(";") if part.strip()]
    return "; ".join(useful[:4])


def build_seed_audit(
    slots: dict[str, Any],
    client: str,
    columns: list[dict[str, Any]] | None = None,
    table: str | None = None,
    exclude: list[str] | None = None,
) -> dict[str, Any]:
    client = client.lower().strip()
    exclude_set = set(exclude or [])
    columns = columns or []
    normalized_time = normalize_time_token(slots.get("time_token"))
    query_text = phrase_text(slots)
    kpi_phrase = str(slots.get("kpi_phrase") or "")
    raw_request = str(slots.get("raw_request") or "")
    slot_terms = set(expand_tokens(tokens(query_text)))
    query_vec = char_ngrams(query_text)

    candidates: list[dict[str, Any]] = []
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

        kpi_score, matched_phrases = _kpi_axis_score(seed, kpi_phrase, raw_request)
        if kpi_score:
            score += kpi_score
            reasons.append(f"kpi axis score {kpi_score:.1f}")

        semantic = cosine(query_vec, char_ngrams(_seed_text(seed))) if query_vec else 0.0
        score += semantic * 12
        if semantic:
            reasons.append(f"semantic {semantic:.2f}")

        intent, intent_reasons = _intent_score(sig, slot_terms, table, slots)
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

        candidate = SeedCandidate(
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
        failures, adaptations = _seed_compatibility_failures(seed, slots, normalized_time, table, missing)
        candidates.append(
            {
                **candidate.__dict__,
                "eligible": not failures,
                "gate_failures": failures,
                "adaptations": adaptations,
                "matched_phrases": matched_phrases,
                "structural_summary": _structural_summary(sig),
                "structural_fingerprint": _structural_fingerprint(sig),
            }
        )

    candidates.sort(key=lambda item: (item["eligible"], item["score"]), reverse=True)
    for raw_rank, candidate in enumerate(candidates, start=1):
        candidate["raw_rank"] = raw_rank
    eligible = [item for item in candidates if item["eligible"]]
    if not eligible:
        return {
            "client": client,
            "slots": slots,
            "table": table,
            "normalized_time": normalized_time,
            "candidates": candidates,
        }

    best = eligible[0]["score"]
    second = eligible[1]["score"] if len(eligible) > 1 else 0.0
    margin = min((best - second) / max(best, 1.0), 0.24)
    for candidate in candidates:
        candidate["confidence"] = max(
            0.05,
            min(0.99, (candidate["score"] / max(best, 1.0)) * (0.75 + margin)),
        )
    return {
        "client": client,
        "slots": slots,
        "table": table,
        "normalized_time": normalized_time,
        "candidates": candidates,
    }


def _supply_diagnostics(candidates: list[dict[str, Any]], eligible: list[dict[str, Any]]) -> dict[str, Any]:
    """Report how much of the catalog actually reached the agent.

    Silent over-filtering is invisible from a single proposal, so state the
    counts and the top rejection reasons before the agent starts composing.
    """
    gate_counts: dict[str, int] = {}
    for candidate in candidates:
        for failure in candidate["gate_failures"]:
            key = failure.split(":", 1)[0]
            gate_counts[key] = gate_counts.get(key, 0) + 1
    adapted = sum(1 for item in eligible if item["adaptations"])
    diagnostics = {
        "seeds_considered": len(candidates),
        "eligible": len(eligible),
        "eligible_needing_adaptation": adapted,
        "top_gate_failures": dict(sorted(gate_counts.items(), key=lambda pair: pair[1], reverse=True)[:5]),
    }
    # A single survivor is only a warning sign when it came from over-filtering.
    # On the Customer 360 path the reviewed snapshot rule allows exactly one
    # raw-comparison seed, so one option there is the intended outcome.
    if len(eligible) == 1 and gate_counts.get("snapshot_requires_generic_raw_seed"):
        diagnostics["note"] = (
            "360 snapshot path: the reviewed rule permits one raw-comparison seed, so a single "
            "option here is by design, not a shortage."
        )
    elif len(eligible) <= 1:
        diagnostics["advisory"] = (
            "Only one seed survived structural gating. Treat the proposal as weak evidence, "
            "check top_gate_failures for a family that was rejected, and compose from the "
            "request rather than deferring to this template."
        )
    return diagnostics


def compact_seed_selection(audit: dict[str, Any], *, audit_id: str) -> dict[str, Any]:
    eligible = [item for item in audit["candidates"] if item["eligible"]]
    if not eligible:
        return {
            "audit_id": audit_id,
            "proposed_selected_seed": None,
            "alternatives": [],
            "normalized_time": audit["normalized_time"],
            "supply": _supply_diagnostics(audit["candidates"], eligible),
            "unresolved_reason": "no structurally compatible seed",
        }

    selected = eligible[0]
    selected_response = {
        key: value
        for key, value in selected.items()
        if key not in {"eligible", "gate_failures", "structural_fingerprint"}
    }
    alternatives: list[dict[str, Any]] = []
    seen_structures = {selected["structural_fingerprint"]}
    for candidate in eligible[1:]:
        fingerprint = candidate["structural_fingerprint"]
        if fingerprint in seen_structures:
            continue
        alternatives.append(
            {
                "seed_id": candidate["seed_id"],
                "raw_rank": candidate["raw_rank"],
                "description": candidate["description"],
                "score": round(candidate["score"], 3),
                "confidence": round(candidate["confidence"], 3),
                "structural_difference": candidate["structural_summary"],
                "adaptations": candidate["adaptations"],
                "matched_phrases": candidate["matched_phrases"],
                "evidence": _concise_seed_evidence(candidate["reason"]),
            }
        )
        seen_structures.add(fingerprint)
        if len(alternatives) == 3:
            break
    return {
        "audit_id": audit_id,
        "proposed_selected_seed": selected_response,
        "alternatives": alternatives,
        "normalized_time": audit["normalized_time"],
        "supply": _supply_diagnostics(audit["candidates"], eligible),
    }


def serialize_seed_audit(audit: dict[str, Any]) -> dict[str, Any]:
    result = dict(audit)
    result["candidates"] = [
        {
            key: value
            for key, value in candidate.items()
            if key != "structural_fingerprint"
        }
        for candidate in audit["candidates"]
    ]
    return result


def select_seed(
    slots: dict[str, Any],
    client: str,
    columns: list[dict[str, Any]] | None = None,
    table: str | None = None,
    exclude: list[str] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    # top_k is retained for call compatibility; the optimized contract always
    # returns one complete proposal plus at most three diverse alternatives.
    del top_k
    audit = build_seed_audit(slots, client, columns, table, exclude)
    return compact_seed_selection(audit, audit_id="local")
