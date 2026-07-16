#!/usr/bin/env python3
"""Consolidate the original and post-reset Omantel regression attempts."""

from __future__ import annotations

import json
import os
import csv
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vp_agent.data import data_dir


ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "traces"
ORIGINAL = TRACES / "omantel_requested_cases_20260713.json"
POST_RESET = TRACES / "omantel_cases_22_34_post_reset_20260714.json"
RETRY = TRACES / "omantel_cases_26_34_post_reset_retry_20260714.json"
CLEANUP_RETRY = TRACES / "omantel_cases_22_23_25_26_final_retry_20260715.json"
FINAL_LOG = TRACES / "omantel_requested_cases_consolidated_final_20260714.json"
ANALYSIS = TRACES / "omantel_regression_analysis_20260714.json"


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def apply_attempts(final: list[dict[str, Any]], audit: dict[str, Any], start: int, end: int) -> None:
    by_case = {entry["case_number"]: entry for entry in audit["cases"]}
    for case_number in range(start, end + 1):
        entry = by_case[case_number]
        response = entry.get("response")
        if not isinstance(response, dict):
            raise RuntimeError(f"Case {case_number} has no JSON response in {audit.get('source_log')}")
        final[case_number - 1] = response


def apply_selected_attempts(
    final: list[dict[str, Any]], audit: dict[str, Any], case_numbers: list[int]
) -> None:
    by_case = {entry["case_number"]: entry for entry in audit["cases"]}
    for case_number in case_numbers:
        response = by_case[case_number].get("response")
        if not isinstance(response, dict):
            raise RuntimeError(f"Case {case_number} has no JSON response in cleanup retry")
        final[case_number - 1] = response


def classify(response: dict[str, Any]) -> str:
    if response.get("ok") is True:
        validation = response.get("validation")
        if not isinstance(validation, dict):
            return "success_without_persisted_validation"
        if validation.get("ok") is True:
            return "validated_success"
        return "success_with_validation_issue"
    if response.get("needs_clarification") is True:
        return "clarification"
    if "did not call mcp__vp__render_condition" in str(response.get("failure_reason", "")):
        raw_text = str(response.get("raw_text") or "")
        if "API Error: Connection closed mid-response" in raw_text:
            return "provider_connection_closed"
        if "[Request interrupted by user]" in raw_text:
            return "request_interrupted"
        return "agent_no_render"
    return "application_failure"


def normalize_condition(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def main() -> int:
    original = load(ORIGINAL)
    post_reset = load(POST_RESET)
    retry = load(RETRY)
    cleanup_retry = load(CLEANUP_RETRY)

    final = list(original)
    apply_attempts(final, post_reset, 22, 25)
    apply_attempts(final, retry, 26, 34)
    apply_selected_attempts(final, cleanup_retry, [22, 23, 25, 26])
    if len(final) != 34:
        raise RuntimeError(f"Expected 34 consolidated cases, found {len(final)}")
    atomic_json(FINAL_LOG, final)

    golden_path = data_dir() / "golden_case.csv"
    with golden_path.open(newline="", encoding="utf-8-sig") as handle:
        golden_by_sentence = {
            row["NL Input"].strip(): row["Expected Output"].strip()
            for row in csv.DictReader(handle)
        }

    cases = []
    statuses: Counter[str] = Counter()
    for case_number, response in enumerate(final, start=1):
        status = classify(response)
        statuses[status] += 1
        validation = response.get("validation") if isinstance(response.get("validation"), dict) else None
        condition = response.get("parent_condition") or ""
        expected = golden_by_sentence.get(str(response.get("sentence") or "").strip())
        cases.append(
            {
                "case_number": case_number,
                "sentence": response.get("sentence"),
                "request_id": response.get("request_id"),
                "status": status,
                "ok": response.get("ok"),
                "needs_clarification": response.get("needs_clarification"),
                "parent_condition": condition or None,
                "golden_expected_condition": expected,
                "golden_exact_match": normalize_condition(condition) == normalize_condition(expected),
                "runtime_placeholder_pair_count": condition.count("${operator} ${value}"),
                "selected_columns": response.get("selected_columns") or [],
                "validation_present": validation is not None,
                "validation_ok": validation.get("ok") if validation else None,
                "validation_errors": validation.get("errors", []) if validation else [],
                "validation_warnings": validation.get("warnings", []) if validation else [],
                "failure_reason": response.get("failure_reason"),
                "clarification_question": response.get("clarification_question"),
            }
        )

    analysis = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client": "omantel",
        "case_count": len(final),
        "status_counts": dict(statuses),
        "successful_api_responses": sum(1 for response in final if response.get("ok") is True),
        "clarifications": sum(1 for response in final if response.get("needs_clarification") is True),
        "provider_connection_closed_failures": statuses["provider_connection_closed"],
        "request_interrupted_failures": statuses["request_interrupted"],
        "unclassified_agent_no_render_failures": statuses["agent_no_render"],
        "cases_with_parent_condition": sum(1 for response in final if response.get("parent_condition")),
        "cases_with_persisted_validation": sum(
            1 for response in final if isinstance(response.get("validation"), dict)
        ),
        "cases_with_exactly_one_runtime_placeholder_pair": sum(
            1
            for response in final
            if str(response.get("parent_condition") or "").count("${operator} ${value}") == 1
        ),
        "golden_sentence_matches": sum(
            1 for response in final if str(response.get("sentence") or "").strip() in golden_by_sentence
        ),
        "golden_exact_condition_matches": sum(
            1
            for response in final
            if normalize_condition(response.get("parent_condition"))
            == normalize_condition(golden_by_sentence.get(str(response.get("sentence") or "").strip()))
        ),
        "source_attempt_logs": [str(ORIGINAL), str(POST_RESET), str(RETRY), str(CLEANUP_RETRY)],
        "consolidated_log": str(FINAL_LOG),
        "cases": cases,
    }
    atomic_json(ANALYSIS, analysis)
    print(json.dumps({key: value for key, value in analysis.items() if key != "cases"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
