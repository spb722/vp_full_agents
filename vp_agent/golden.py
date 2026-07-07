from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any


DEFAULT_GOLDEN_PATH = Path("/Users/sachinpb/PycharmProjects/Virtual_profile_agent/data/golden_case.csv")
SNAPSHOT_OUTPUT_RE = re.compile(
    r"\b(?:CUST_360|360_|[A-Z0-9_]+_(?:M(?:TD|[1-9]|1[0-2])|LMTD|W[1-9]|FW\d+|\d+D))",
    re.I,
)
DATE_CONDITION_RE = re.compile(r"\b(?:CurrentMonth|CurrentTime|CurrentWeek|Event_Date|FCT_DT|_DT)\b", re.I)


def load_golden_cases(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    with source.open(newline="", encoding="utf-8-sig") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return rows


def find_360_snapshot_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for row in rows:
        expected = str(row.get("Expected Output") or row.get("expected_output") or "")
        if is_snapshot_expected_output(expected):
            cases.append(row)
    return cases


def is_snapshot_expected_output(expected_output: str) -> bool:
    return bool(SNAPSHOT_OUTPUT_RE.search(expected_output)) and not has_date_condition(expected_output)


def has_date_condition(condition: str) -> bool:
    return bool(DATE_CONDITION_RE.search(condition))


def condition_column(condition: str) -> str | None:
    match = re.search(r"\b([A-Za-z][A-Za-z0-9_]*)\s+\$\{operator\}\s+\$\{value\}", condition)
    return match.group(1) if match else None
