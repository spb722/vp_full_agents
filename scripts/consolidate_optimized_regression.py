#!/usr/bin/env python3
"""Consolidate checkpointed optimized Omantel runs and create a mechanical audit.

Semantic equivalence is intentionally not inferred here. The generated analysis
records exact differences so they can be reviewed independently of validation.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vp_agent.data import data_dir


ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "traces"
BASELINE = TRACES / "omantel_requested_cases_consolidated_final_20260714.json"
ATTEMPTS = [
    TRACES / "omantel_optimized_34_run_20260715.json",
    TRACES / "omantel_optimized_cases19_34_run_20260715.json",
    TRACES / "omantel_optimized_cases20_34_run_20260715.json",
]
CORRECTIONS = [
    TRACES / "omantel_optimized_targeted_cases10_11_20260715.json",
    TRACES / "omantel_optimized_targeted_cases27_28_20260715.json",
    TRACES / "omantel_optimized_targeted_case10_corrected_20260715.json",
]
FINAL = TRACES / "omantel_optimized_34_responses_final_20260715.json"
MANIFEST = TRACES / "omantel_optimized_34_run_manifest_20260715.json"
ANALYSIS = TRACES / "omantel_optimized_34_mechanical_analysis_20260715.json"
SEMANTIC_REVIEW = TRACES / "omantel_optimized_34_semantic_review_20260715.md"
LANGFUSE_EXPORT = TRACES / "langfuse_omantel_optimized_34_corrected_20260715"
LANGFUSE_COMPARISON = TRACES / "omantel_optimized_vs_historical_langfuse_analysis_corrected_20260715.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def classify(response: dict[str, Any]) -> str:
    if response.get("ok") is True:
        validation = response.get("validation")
        if not isinstance(validation, dict):
            return "success_without_persisted_validation"
        return "validated_success" if validation.get("ok") is True else "success_with_validation_issue"
    if response.get("needs_clarification") is True:
        return "clarification"
    return "application_failure"


def main() -> int:
    baseline = load(BASELINE)
    if not isinstance(baseline, list) or len(baseline) != 34:
        raise RuntimeError(f"Expected 34 baseline responses in {BASELINE}")

    by_case: dict[int, dict[str, Any]] = {}
    source_by_case: dict[int, str] = {}
    attempt_summaries: list[dict[str, Any]] = []
    for path in ATTEMPTS:
        attempt = load(path)
        cases = attempt.get("cases") if isinstance(attempt, dict) else None
        if not isinstance(cases, list):
            raise RuntimeError(f"Expected an audit object with cases in {path}")
        attempt_summaries.append(
            {
                "path": str(path),
                "endpoint": attempt.get("endpoint"),
                "started_at": attempt.get("started_at"),
                "completed_at": attempt.get("completed_at"),
                "case_numbers": [entry.get("case_number") for entry in cases],
            }
        )
        for entry in cases:
            case_number = int(entry["case_number"])
            if case_number in by_case:
                raise RuntimeError(f"Case {case_number} appears in more than one optimized checkpoint")
            response = entry.get("response")
            if not isinstance(response, dict):
                raise RuntimeError(f"Case {case_number} in {path} has no full response")
            expected_sentence = str(baseline[case_number - 1].get("sentence") or "")
            if str(response.get("sentence") or "") != expected_sentence:
                raise RuntimeError(f"Case {case_number} sentence differs from the baseline source")
            by_case[case_number] = entry
            source_by_case[case_number] = str(path)

    missing = sorted(set(range(1, 35)) - set(by_case))
    extra = sorted(set(by_case) - set(range(1, 35)))
    if missing or extra:
        raise RuntimeError(f"Invalid optimized case coverage: missing={missing}, extra={extra}")

    for path in CORRECTIONS:
        correction = load(path)
        cases = correction.get("cases") if isinstance(correction, dict) else None
        if not isinstance(cases, list):
            raise RuntimeError(f"Expected a correction audit object with cases in {path}")
        attempt_summaries.append(
            {
                "path": str(path),
                "kind": "targeted_correction",
                "endpoint": correction.get("endpoint"),
                "started_at": correction.get("started_at"),
                "completed_at": correction.get("completed_at"),
                "case_numbers": [entry.get("case_number") for entry in cases],
            }
        )
        for entry in cases:
            case_number = int(entry["case_number"])
            response = entry.get("response")
            if not isinstance(response, dict):
                raise RuntimeError(f"Corrected case {case_number} in {path} has no full response")
            expected_sentence = str(baseline[case_number - 1].get("sentence") or "")
            if str(response.get("sentence") or "") != expected_sentence:
                raise RuntimeError(f"Corrected case {case_number} sentence differs from the baseline source")
            by_case[case_number] = entry
            source_by_case[case_number] = str(path)

    final = [by_case[number]["response"] for number in range(1, 35)]
    atomic_json(FINAL, final)

    golden_path = data_dir() / "golden_case.csv"
    with golden_path.open(newline="", encoding="utf-8-sig") as handle:
        golden = {
            row["NL Input"].strip(): row["Expected Output"].strip()
            for row in csv.DictReader(handle)
        }

    status_counts: Counter[str] = Counter()
    cases: list[dict[str, Any]] = []
    for case_number in range(1, 35):
        run_entry = by_case[case_number]
        optimized = run_entry["response"]
        previous = baseline[case_number - 1]
        status = classify(optimized)
        status_counts[status] += 1
        optimized_condition = optimized.get("parent_condition")
        previous_condition = previous.get("parent_condition")
        sentence = str(optimized.get("sentence") or "")
        expected = golden.get(sentence.strip())
        cases.append(
            {
                "case_number": case_number,
                "sentence": sentence,
                "request_id": optimized.get("request_id"),
                "source_checkpoint": source_by_case[case_number],
                "elapsed_seconds": run_entry.get("elapsed_seconds"),
                "http_status": run_entry.get("http_status"),
                "transport_error": run_entry.get("transport_error"),
                "status": status,
                "ok": optimized.get("ok"),
                "needs_clarification": optimized.get("needs_clarification"),
                "validation_ok": (optimized.get("validation") or {}).get("ok"),
                "runtime_placeholder_pair_count": str(optimized_condition or "").count(
                    "${operator} ${value}"
                ),
                "optimized_parent_condition": optimized_condition,
                "historical_parent_condition": previous_condition,
                "historical_ok": previous.get("ok"),
                "behavior_exact_match": (
                    optimized.get("ok") == previous.get("ok")
                    and optimized.get("needs_clarification") == previous.get("needs_clarification")
                    and normalize(optimized_condition) == normalize(previous_condition)
                ),
                "condition_exact_match_when_present": bool(
                    optimized_condition
                    and previous_condition
                    and normalize(optimized_condition) == normalize(previous_condition)
                ),
                "golden_expected_condition": expected,
                "golden_exact_match": bool(
                    optimized_condition
                    and expected
                    and normalize(optimized_condition) == normalize(expected)
                ),
                "semantic_review": "pending",
            }
        )

    elapsed = [float(by_case[number].get("elapsed_seconds") or 0) for number in range(1, 35)]
    analysis = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client": "omantel",
        "variant": "optimized",
        "case_count": 34,
        "status_counts": dict(status_counts),
        "successful_api_responses": sum(response.get("ok") is True for response in final),
        "clarifications": sum(response.get("needs_clarification") is True for response in final),
        "cases_with_parent_condition": sum(bool(response.get("parent_condition")) for response in final),
        "cases_with_persisted_validation": sum(
            isinstance(response.get("validation"), dict) for response in final
        ),
        "cases_with_validation_ok": sum(
            (response.get("validation") or {}).get("ok") is True for response in final
        ),
        "cases_with_exactly_one_runtime_placeholder_pair": sum(
            str(response.get("parent_condition") or "").count("${operator} ${value}") == 1
            for response in final
        ),
        "historical_behavior_exact_matches": sum(case["behavior_exact_match"] for case in cases),
        "historical_condition_exact_matches": sum(
            case["condition_exact_match_when_present"] for case in cases
        ),
        "golden_exact_condition_matches": sum(case["golden_exact_match"] for case in cases),
        "elapsed_seconds": {
            "total": round(sum(elapsed), 3),
            "average": round(sum(elapsed) / len(elapsed), 3),
            "minimum": round(min(elapsed), 3),
            "maximum": round(max(elapsed), 3),
        },
        "baseline_log": str(BASELINE),
        "optimized_log": str(FINAL),
        "cases": cases,
    }
    atomic_json(ANALYSIS, analysis)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client": "omantel",
        "variant": "optimized",
        "case_count": 34,
        "all_request_ids_unique": len({response["request_id"] for response in final}) == 34,
        "request_ids": [response["request_id"] for response in final],
        "attempt_logs": attempt_summaries,
        "consolidated_response_log": str(FINAL),
        "mechanical_analysis": str(ANALYSIS),
        "semantic_review": str(SEMANTIC_REVIEW),
        "langfuse_export": str(LANGFUSE_EXPORT),
        "langfuse_historical_comparison": str(LANGFUSE_COMPARISON),
        "cases": [
            {
                "case_number": number,
                "sentence": final[number - 1]["sentence"],
                "request_id": final[number - 1]["request_id"],
                "source_checkpoint": source_by_case[number],
            }
            for number in range(1, 35)
        ],
    }
    atomic_json(MANIFEST, manifest)

    print(
        json.dumps(
            {
                key: value
                for key, value in analysis.items()
                if key not in {"cases"}
            },
            indent=2,
        )
    )
    print(f"FINAL_LOG {FINAL.resolve()}")
    print(f"MANIFEST {MANIFEST.resolve()}")
    print(f"ANALYSIS {ANALYSIS.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
