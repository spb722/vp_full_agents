from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from vp_agent.data import load_kpi_meta, load_vp_descriptions
from vp_agent.text import tokens


CONDITION_SIGNATURE_RE = re.compile(r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\(|Current(?:Time|Month|Week)|\$\{operator\}|\$\{value\}")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
PLACEHOLDER_OWNER_RE = re.compile(
    r"((?:SUM|COUNT_ALL|AVG|MAX|MIN)\([^)]*\)(?:__groupby_[A-Za-z0-9_,]+)?|[A-Za-z][A-Za-z0-9_]*)"
    r"\s*\$\{operator\}\s*\$\{value\}"
)
AGGREGATED_COLUMN_RE = re.compile(r"(?:SUM|COUNT_ALL|AVG|MAX|MIN)\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)")
FIXED_AGGREGATE_RE = re.compile(
    r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)\s*(<=|>=|<>|!=|<|>|=)\s*(-?\d+(?:\.\d+)?)"
)
IDENTIFIER_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*(?:_\$\{X\})?[A-Za-z0-9_]*\b")
FUNCTIONS = {"SUM", "COUNT_ALL", "AVG", "MAX", "MIN", "IN", "V", "f"}
KEYWORDS = {
    "AND",
    "OR",
    "NULL",
    "CurrentTime",
    "CurrentMonth",
    "CurrentWeek",
    "DAYS",
    "MONTHS",
    "WEEKS",
}


def has_condition_signature(text: str) -> bool:
    return bool(CONDITION_SIGNATURE_RE.search(text))


def _known_columns() -> set[str]:
    return {row.feature_name for row in load_kpi_meta()}


def referenced_columns(rule: str) -> set[str]:
    known = _known_columns()
    refs = set()
    for token in IDENTIFIER_RE.findall(rule):
        if token in FUNCTIONS or token in KEYWORDS:
            continue
        if token in known:
            refs.add(token)
    return refs


def _rule_shape(rule: str) -> dict[str, Any]:
    owner_match = PLACEHOLDER_OWNER_RE.search(rule)
    owner = owner_match.group(1) if owner_match else None
    return {
        "placeholder_owner": owner,
        "placeholder_owner_kind": ("aggregate" if owner and "(" in owner else "column" if owner else None),
        "fixed_aggregate_comparisons": [
            f"{function}({column}) {operator} {value}"
            for function, column, operator, value in FIXED_AGGREGATE_RE.findall(rule)
        ],
    }


def _focus_columns(rule: str) -> set[str]:
    """The columns that decide whether two rules are about the same thing.

    Prefer the aggregated column: two rules that aggregate different columns are
    different business questions even when they share a filter column, and
    comparing those produces noise. Fall back to the profiled column only when
    the rule has no aggregate at all, such as a 360 snapshot comparison.
    """
    known = _known_columns()
    focus = set(AGGREGATED_COLUMN_RE.findall(rule))
    focus.update(column for _, column, _, _ in FIXED_AGGREGATE_RE.findall(rule))
    if not focus:
        owner = _rule_shape(rule)["placeholder_owner"]
        if owner:
            focus.update(re.findall(r"[A-Za-z][A-Za-z0-9_]*", owner))
    return {column for column in focus if column in known}


def unrendered_stated_numbers(rule: str, request: str) -> list[str]:
    """Numbers the marketer stated that never reached the rendered rule.

    Dropping a stated number is sometimes correct — the main KPI threshold
    becomes `${operator} ${value}` by design — so this is reported as a warning
    for the agent to adjudicate, never as an error.
    """
    missing: list[str] = []
    for number in NUMBER_RE.findall(request or ""):
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", rule):
            continue
        if number not in missing:
            missing.append(number)
    return missing


@dataclass(frozen=True)
class IntentCue:
    """A phrase in the request that should leave a visible mark on the rule."""

    name: str
    in_request: re.Pattern[str]
    in_rule: re.Pattern[str]
    message: str


INTENT_CUES: tuple[IntentCue, ...] = (
    IntentCue(
        "negation",
        re.compile(r"\b(not|never|without|excluding|exclude|haven'?t|hasn'?t|didn'?t|did not|no longer|non-)\b", re.I),
        # `<> NULL` is a presence guard, not a negation, so it does not satisfy this cue.
        re.compile(r"=\s*0\b|!=|<>\s*(?!NULL)|NOT\s+IN\s+LIST|NOT\s+LIKE", re.I),
        "the request looks negative but the rule has no negative condition; a positive rule selects the opposite audience",
    ),
    IntentCue(
        "per_entity",
        # "per month" is a period and "per customer" is the default grain, so
        # neither counts as a grouping request.
        re.compile(
            r"\bper\s+(?!day|days|week|weeks|month|months|year|years|hour|hours|subscriber|subscribers|customer|customers|user|users)\w+"
            r"|\bfor each\b|\bbroken down by\b|\bgrouped by\b",
            re.I,
        ),
        re.compile(r"__groupby_"),
        "the request asks for a per-entity figure but the rule has no __groupby_ suffix; it currently totals per subscriber",
    ),
    IntentCue(
        "average",
        re.compile(r"\b(average|avg|mean)\b", re.I),
        re.compile(r"\bAVG\s*\(|/\s*\d+|f\s*\{[^}]*/", re.I),
        "the request asks for an average but the rule has no AVG aggregate and no divisor formula",
    ),
    IntentCue(
        "alternation",
        re.compile(r"\bor\b(?!\s+(?:more|less|equal|above|below|higher|lower|greater|fewer))", re.I),
        re.compile(r"\bIN\s+LIST\b|\bOR\b", re.I),
        "the request offers alternatives but the rule has no IN LIST or OR; ANDing alternatives for one column always matches nobody",
    ),
)

# scope name -> (how the marketer says it, how a column spells it)
SCOPE_CUES: dict[str, tuple[re.Pattern[str], tuple[str, ...]]] = {
    "local": (re.compile(r"\blocal\b", re.I), ("local",)),
    "offnet": (re.compile(r"\boff[- ]?net\b", re.I), ("offnet",)),
    "onnet": (re.compile(r"\bon[- ]?net\b", re.I), ("onnet",)),
    "idd": (re.compile(r"\b(idd|international)\b", re.I), ("idd", "international")),
    "roaming": (re.compile(r"\broam(?:ing)?\b", re.I), ("roam", "roaming")),
    "prepaid": (re.compile(r"\bpre[- ]?paid\b", re.I), ("prepaid", "prepay")),
    "postpaid": (re.compile(r"\bpost[- ]?paid\b", re.I), ("postpaid", "postpay")),
    "bundle": (re.compile(r"\bbundle[ds]?\b", re.I), ("bundle",)),
    "payg": (re.compile(r"\b(payg|pay[- ]as[- ]you[- ]go)\b", re.I), ("payg",)),
    "free": (re.compile(r"\bfree\b", re.I), ("free",)),
    "finance": (re.compile(r"\bfinanc(?:e|ial)\b", re.I), ("finance", "financial")),
}


def _rule_words(rule: str) -> str:
    """Rule text with underscores as separators, so column parts match as words."""
    return re.sub(r"[_\s]+", " ", rule).lower()


def intent_cue_warnings(rule: str, request: str) -> list[dict[str, Any]]:
    """Flag request phrasing that left no trace on the rendered rule.

    Purely textual and deliberately independent of the skills: it does not know
    what the agent was told, so it cannot inherit the agent's blind spots. Every
    result is advisory — some cues are legitimately satisfied in ways this
    cannot see, and the agent adjudicates.
    """
    if not request:
        return []

    warnings: list[dict[str, Any]] = []
    for cue in INTENT_CUES:
        match = cue.in_request.search(request)
        if match and not cue.in_rule.search(rule):
            warnings.append(
                {
                    "class": "intent",
                    "cue": cue.name,
                    "message": cue.message,
                    "request_phrase": match.group(0),
                }
            )

    rule_words = _rule_words(rule)
    missing_scopes = [
        scope
        for scope, (in_request, aliases) in SCOPE_CUES.items()
        if in_request.search(request)
        and not any(re.search(rf"\b{alias}", rule_words) for alias in aliases)
    ]
    if missing_scopes:
        warnings.append(
            {
                "class": "intent",
                "cue": "scope",
                "message": (
                    "the request names a service scope that does not appear in any chosen column; "
                    "confirm the column really covers that scope, since a column can encode it without naming it"
                ),
                "scopes": missing_scopes,
            }
        )
    return warnings


def production_shape_differences(rule: str, client: str | None, limit: int = 3) -> list[dict[str, Any]]:
    """Compare the rendered rule against production VPs on the same columns.

    This is a post-render cross-check, not a source of truth: it runs only once
    a rule exists and reports differences for the agent to weigh.
    """
    if not client:
        return []
    focus = _focus_columns(rule)
    if not focus:
        return []
    try:
        rows = load_vp_descriptions(client)
    except (OSError, ValueError):
        return []

    shape = _rule_shape(rule)
    matches: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        condition = str(row.get("PARENT_CONDITION") or "").strip()
        name = str(row.get("VIRTUAL_PROFILE_NAME") or "").strip()
        if not condition or not name or condition == rule:
            continue
        shared = {column for column in focus if re.search(rf"\b{re.escape(column)}\b", condition)}
        if not shared:
            continue

        other = _rule_shape(condition)
        differences: list[str] = []
        if other["placeholder_owner_kind"] and shape["placeholder_owner_kind"] != other["placeholder_owner_kind"]:
            differences.append(
                f"production puts ${{operator}} ${{value}} on the {other['placeholder_owner_kind']} "
                f"({other['placeholder_owner']}); this rule puts it on the "
                f"{shape['placeholder_owner_kind']} ({shape['placeholder_owner']})"
            )
        if other["fixed_aggregate_comparisons"] and not shape["fixed_aggregate_comparisons"]:
            differences.append(
                "production keeps a literal aggregate threshold ("
                + "; ".join(other["fixed_aggregate_comparisons"])
                + "); this rule has none"
            )
        if not differences:
            continue
        matches.append(
            (
                len(shared),
                {
                    "class": "convention",
                    "message": "a production VP uses the same columns with a different shape",
                    "vp_name": name,
                    "shared_columns": sorted(shared),
                    "parent_condition": condition,
                    "differences": differences,
                },
            )
        )

    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in matches[:limit]]


def validate_rule(
    rule: str,
    request: str,
    table: str | None = None,
    client: str | None = None,
) -> dict[str, Any]:
    errors = []
    warnings = []

    op_count = rule.count("${operator}")
    value_count = rule.count("${value}")
    if op_count != 1 or value_count != 1:
        errors.append(
            {
                "class": "render",
                "message": "normal VP rules must contain exactly one ${operator} and one ${value}",
                "operator_count": op_count,
                "value_count": value_count,
            }
        )

    if "${operator} ${value}" not in rule:
        errors.append({"class": "render", "message": "operator/value placeholders must be adjacent"})

    if "__groupby_" in rule:
        aggregate_positions = [rule.find(fn + "(") for fn in ("SUM", "COUNT_ALL", "AVG", "MAX") if fn + "(" in rule]
        groupby_pos = rule.find("__groupby_")
        if aggregate_positions and min(aggregate_positions) > groupby_pos:
            errors.append({"class": "render", "message": "groupby suffix appears before aggregate"})

    unknown_like = []
    known = _known_columns()
    for token in IDENTIFIER_RE.findall(rule):
        if token in FUNCTIONS or token in KEYWORDS or token in known:
            continue
        if token.startswith("Current") or token in {"DAYS", "MONTHS", "WEEKS"}:
            continue
        if token.isupper() and "_" in token:
            unknown_like.append(token)
    if unknown_like:
        warnings.append({"class": "column", "message": "identifier-like tokens not found in kpi_meta", "tokens": sorted(set(unknown_like))[:20]})

    unrendered = unrendered_stated_numbers(rule, request)
    if unrendered:
        warnings.append(
            {
                "class": "coverage",
                "message": (
                    "numbers stated in the request do not appear in the rule; confirm each is "
                    "intentionally deferred to ${operator} ${value} rather than lost"
                ),
                "numbers": unrendered,
            }
        )

    warnings.extend(intent_cue_warnings(rule, request))
    warnings.extend(production_shape_differences(rule, client))

    request_terms = set(tokens(request))
    rule_terms = set(tokens(rule))
    coverage_terms = sorted(t for t in request_terms if t in rule_terms)

    return {
        "ok": not errors,
        "table": table,
        "client": client,
        "errors": errors,
        "warnings": warnings,
        "referenced_columns": sorted(referenced_columns(rule)),
        "coverage_terms": coverage_terms,
    }

