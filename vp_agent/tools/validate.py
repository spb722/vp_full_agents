from __future__ import annotations

import re
from typing import Any

from vp_agent.data import load_kpi_meta
from vp_agent.text import tokens


CONDITION_SIGNATURE_RE = re.compile(r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\(|Current(?:Time|Month|Week)|\$\{operator\}|\$\{value\}")
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


def validate_rule(rule: str, request: str, table: str | None = None) -> dict[str, Any]:
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

    request_terms = set(tokens(request))
    rule_terms = set(tokens(rule))
    coverage_terms = sorted(t for t in request_terms if t in rule_terms)

    return {
        "ok": not errors,
        "table": table,
        "errors": errors,
        "warnings": warnings,
        "referenced_columns": sorted(referenced_columns(rule)),
        "coverage_terms": coverage_terms,
    }

