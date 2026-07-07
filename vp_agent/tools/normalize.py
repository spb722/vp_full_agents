from __future__ import annotations

import re
from typing import Any


OPERATOR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(more than|greater than|above|over)\b", re.I), ">"),
    (re.compile(r"\b(at least|greater than or equal to|more than or equal to|minimum of|min(?:imum)?)\b", re.I), ">="),
    (re.compile(r"\b(less than|lower than|below|under)\b", re.I), "<"),
    (re.compile(r"\b(at most|less than or equal to|maximum of|max(?:imum)?)\b", re.I), "<="),
    (re.compile(r"\b(equal to|equals|is exactly|exactly)\b", re.I), "="),
)

TIME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:last|past|previous|in the last|over the last)\s+(\d+)\s+days?\b", re.I), "D"),
    (re.compile(r"\b(?:last|past|previous|over the last)\s+(\d+)\s+weeks?\b", re.I), "W"),
    (re.compile(r"\b(?:last|past|previous|over the last)\s+(\d+)\s+months?\b", re.I), "M"),
)


def _find_operator(text: str) -> tuple[str, str | None]:
    for pattern, operator in OPERATOR_PATTERNS:
        match = pattern.search(text)
        if match:
            return operator, match.group(1)
    return "unknown", None


def _find_value(text: str, operator_phrase: str | None) -> str:
    if operator_phrase:
        pattern = re.compile(re.escape(operator_phrase) + r"\s+(-?\d+(?:\.\d+)?)", re.I)
        match = pattern.search(text)
        if match:
            return match.group(1)

    money = re.search(r"\b(-?\d+(?:\.\d+)?)\s*(?:omr|rial|rials|rs|usd|dollars?)\b", text, re.I)
    if money:
        return money.group(1)
    number = re.search(r"\b(-?\d+(?:\.\d+)?)\b", text)
    return number.group(1) if number else ""


def _find_time_token(text: str) -> str:
    explicit = re.search(r"\bM([1-9]|1[0-2])\b", text, re.I)
    if explicit:
        return f"M{int(explicit.group(1))}"
    explicit_week = re.search(r"\bW([1-9]|1[0-2])\b", text, re.I)
    if explicit_week:
        return f"W{int(explicit_week.group(1))}"
    for pattern, unit in TIME_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"{int(match.group(1))}{unit}"
    if re.search(r"\byesterday\b", text, re.I):
        return "1D"
    if re.search(r"\b(month to date|mtd|this month|current month)\b", text, re.I):
        return "MTD"
    if re.search(r"\b(last month|previous month)\b", text, re.I):
        return "M1"
    if re.search(r"\b(month before last)\b", text, re.I):
        return "M2"
    return "none"


def _detect_domain_and_kpi(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if re.search(r"\b(recharged|recharge|top[- ]?up|topup|voucher)\b", lowered):
        if re.search(r"\b(count|number|times|frequency)\b", lowered):
            return "recharge", "recharge count"
        return "recharge", "recharge amount"
    if re.search(r"\b(data usage|internet usage|data consumed|data volume|mb|gb)\b", lowered):
        return "usage", "data usage"
    if re.search(r"\b(voice|call|mou|minutes)\b", lowered):
        return "usage", "voice usage"
    if re.search(r"\b(sms|message)\b", lowered):
        return "usage", "sms usage"
    if re.search(r"\b(pack|bundle|product|subscription|subscribed|purchased)\b", lowered):
        return "subscription", "subscription"
    if re.search(r"\b(segment|audience)\b", lowered):
        return "audience_segment", "audience segment"
    return "unknown", ""


def _detect_filters(text: str) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    lowered = text.lower()

    if re.search(r"\b(omani|oman nationals?|omani nationals?)\b", lowered):
        filters.append({"phrase": "Omani nationals", "operator": "=", "value": "Omani"})
    if re.search(r"\b(non[- ]?omani|expat|expatriate)\b", lowered):
        filters.append({"phrase": "nationality", "operator": "!=", "value": "Omani"})

    if re.search(r"\b(smartphone|smartphones|smart phone|smart phones)\b", lowered):
        filters.append({"phrase": "smartphones", "operator": "=", "value": "smartphone"})
    if re.search(r"\b(feature phone|feature phones|featurephone|featurephones)\b", lowered):
        filters.append({"phrase": "feature phones", "operator": "=", "value": "featurephone"})

    if re.search(r"\b(prepaid)\b", lowered):
        filters.append({"phrase": "line type prepaid", "operator": "=", "value": "Prepaid"})
    if re.search(r"\b(postpaid)\b", lowered):
        filters.append({"phrase": "line type postpaid", "operator": "=", "value": "Postpaid"})

    return filters


def normalize_slots(request: str, client: str | None = None) -> dict[str, Any]:
    text = request.strip()
    operator, operator_phrase = _find_operator(text)
    value = _find_value(text, operator_phrase)
    time_token = _find_time_token(text)
    domain, kpi_phrase = _detect_domain_and_kpi(text)
    filters = _detect_filters(text)

    warnings = []
    missing = []
    if domain == "unknown":
        missing.append("domain")
        warnings.append("Could not determine KPI domain from the sentence.")
    if not kpi_phrase:
        missing.append("kpi_phrase")
    if operator == "unknown":
        missing.append("operator")
        warnings.append("Could not determine comparison operator.")
    if not value:
        missing.append("value")
        warnings.append("Could not determine comparison value.")
    if time_token == "none" and domain in {"recharge", "usage", "subscription"}:
        missing.append("time_token")
        warnings.append("No time window found for an event or aggregate KPI.")

    return {
        "client": client,
        "raw_request": text,
        "domain": domain,
        "kpi_phrase": kpi_phrase,
        "time_token": time_token,
        "operator": operator,
        "value": value,
        "filters": filters,
        "negations": [],
        "needs_clarification": bool(missing),
        "missing": missing,
        "warnings": warnings,
    }
