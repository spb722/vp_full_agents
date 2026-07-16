#!/usr/bin/env python3
"""Compare two ordered VP Langfuse exports without conflating semantics and cost."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-export", type=Path, required=True)
    parser.add_argument("--optimized-export", type=Path, required=True)
    parser.add_argument("--baseline-responses", type=Path, required=True)
    parser.add_argument("--optimized-responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_trace_summary(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            request_id = row["applicationRequestId"]
            rows[request_id] = {
                "request_id": request_id,
                "latency": float(row["latency"] or 0),
                "total_cost": float(row["totalCost"] or 0),
                "observation_count": int(row["observationCount"] or 0),
                "observation_type_counts": json.loads(row["observationTypeCounts"] or "{}"),
                "usage": json.loads(row["usageTotals"] or "{}"),
            }
    return rows


def load_observations(path: Path) -> dict[str, list[dict[str, Any]]]:
    csv.field_size_limit(sys.maxsize)
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            rows[row["applicationRequestId"]].append(row)
    return dict(rows)


def percent_delta(before: float, after: float) -> float | None:
    if not before:
        return None
    return round((after - before) / before * 100, 3)


def observation_metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts: Counter[str] = Counter()
    name_counts: Counter[str] = Counter()
    output_chars: Counter[str] = Counter()
    input_chars: Counter[str] = Counter()
    for observation in observations:
        observation_type = observation.get("type") or ""
        name = observation.get("name") or ""
        type_counts[observation_type] += 1
        name_counts[name] += 1
        output_chars[name] += len(observation.get("output") or "")
        input_chars[name] += len(observation.get("input") or "")
    return {
        "type_counts": dict(type_counts),
        "name_counts": dict(name_counts),
        "tool_calls": type_counts["TOOL"],
        "agent_observations": type_counts["AGENT"],
        "span_observations": type_counts["SPAN"],
        "tool_output_chars": sum(
            len(row.get("output") or "") for row in observations if row.get("type") == "TOOL"
        ),
        "retrieve_columns_calls": name_counts["mcp__vp__retrieve_columns"],
        "retrieve_columns_output_chars": output_chars["mcp__vp__retrieve_columns"],
        "select_seed_calls": name_counts["mcp__vp__select_seed"],
        "select_seed_output_chars": output_chars["mcp__vp__select_seed"],
        "read_calls": name_counts["Read"],
        "read_output_chars": output_chars["Read"],
        "skill_calls": name_counts["Skill"],
        "subagent_tool_calls": name_counts["Agent"],
        "retrieval_full_audit_span_chars": output_chars["vp.retrieve_columns.full_audit"],
        "seed_full_audit_span_chars": output_chars["vp.select_seed.full_audit"],
    }


def aggregate(cases: list[dict[str, Any]], side: str) -> dict[str, Any]:
    trace_fields = ["latency", "total_cost", "observation_count"]
    usage_fields = ["input", "input_cache_creation", "input_cached_tokens", "output", "total"]
    observation_fields = [
        "tool_calls",
        "agent_observations",
        "span_observations",
        "tool_output_chars",
        "retrieve_columns_calls",
        "retrieve_columns_output_chars",
        "select_seed_calls",
        "select_seed_output_chars",
        "read_calls",
        "read_output_chars",
        "skill_calls",
        "subagent_tool_calls",
        "retrieval_full_audit_span_chars",
        "seed_full_audit_span_chars",
    ]
    result: dict[str, Any] = {
        field: round(sum(float(case[side][field]) for case in cases), 6)
        for field in trace_fields
    }
    result["usage"] = {
        field: int(sum(int(case[side]["usage"].get(field, 0)) for case in cases))
        for field in usage_fields
    }
    result["observations"] = {
        field: int(sum(int(case[side]["observations"].get(field, 0)) for case in cases))
        for field in observation_fields
    }
    result["average_latency"] = round(result["latency"] / len(cases), 6)
    result["average_total_cost"] = round(result["total_cost"] / len(cases), 6)
    return result


def main() -> int:
    args = parse_args()
    baseline_responses = load_json(args.baseline_responses)
    optimized_responses = load_json(args.optimized_responses)
    if not isinstance(baseline_responses, list) or not isinstance(optimized_responses, list):
        raise RuntimeError("Response logs must be JSON arrays")
    if len(baseline_responses) != len(optimized_responses):
        raise RuntimeError("Response logs must contain the same number of ordered cases")

    baseline_traces = load_trace_summary(args.baseline_export / "trace_summary.csv")
    optimized_traces = load_trace_summary(args.optimized_export / "trace_summary.csv")
    baseline_observations = load_observations(args.baseline_export / "observations.csv")
    optimized_observations = load_observations(args.optimized_export / "observations.csv")

    cases: list[dict[str, Any]] = []
    for index, (baseline_response, optimized_response) in enumerate(
        zip(baseline_responses, optimized_responses), start=1
    ):
        baseline_id = str(baseline_response["request_id"])
        optimized_id = str(optimized_response["request_id"])
        if baseline_id not in baseline_traces or optimized_id not in optimized_traces:
            raise RuntimeError(f"Missing trace summary for case {index}")
        baseline = dict(baseline_traces[baseline_id])
        optimized = dict(optimized_traces[optimized_id])
        baseline["observations"] = observation_metrics(baseline_observations[baseline_id])
        optimized["observations"] = observation_metrics(optimized_observations[optimized_id])
        cases.append(
            {
                "case_number": index,
                "sentence": optimized_response.get("sentence"),
                "baseline": baseline,
                "optimized": optimized,
                "delta": {
                    "latency": round(optimized["latency"] - baseline["latency"], 6),
                    "latency_percent": percent_delta(baseline["latency"], optimized["latency"]),
                    "total_cost": round(optimized["total_cost"] - baseline["total_cost"], 9),
                    "total_cost_percent": percent_delta(
                        baseline["total_cost"], optimized["total_cost"]
                    ),
                    "reported_usage_total": int(optimized["usage"].get("total", 0))
                    - int(baseline["usage"].get("total", 0)),
                    "reported_usage_total_percent": percent_delta(
                        float(baseline["usage"].get("total", 0)),
                        float(optimized["usage"].get("total", 0)),
                    ),
                    "tool_calls": int(optimized["observations"]["type_counts"].get("TOOL", 0))
                    - int(baseline["observations"]["type_counts"].get("TOOL", 0)),
                    "retrieve_columns_output_chars": optimized["observations"][
                        "retrieve_columns_output_chars"
                    ]
                    - baseline["observations"]["retrieve_columns_output_chars"],
                    "select_seed_output_chars": optimized["observations"][
                        "select_seed_output_chars"
                    ]
                    - baseline["observations"]["select_seed_output_chars"],
                },
            }
        )

    baseline_totals = aggregate(cases, "baseline")
    optimized_totals = aggregate(cases, "optimized")
    summary = {
        "latency_percent": percent_delta(baseline_totals["latency"], optimized_totals["latency"]),
        "total_cost_percent": percent_delta(
            baseline_totals["total_cost"], optimized_totals["total_cost"]
        ),
        "reported_usage_total_percent": percent_delta(
            baseline_totals["usage"]["total"], optimized_totals["usage"]["total"]
        ),
        "output_tokens_percent": percent_delta(
            baseline_totals["usage"]["output"], optimized_totals["usage"]["output"]
        ),
        "cache_creation_tokens_percent": percent_delta(
            baseline_totals["usage"]["input_cache_creation"],
            optimized_totals["usage"]["input_cache_creation"],
        ),
        "cached_input_tokens_percent": percent_delta(
            baseline_totals["usage"]["input_cached_tokens"],
            optimized_totals["usage"]["input_cached_tokens"],
        ),
        "tool_output_chars_percent": percent_delta(
            baseline_totals["observations"]["tool_output_chars"],
            optimized_totals["observations"]["tool_output_chars"],
        ),
        "retrieve_columns_calls_percent": percent_delta(
            baseline_totals["observations"]["retrieve_columns_calls"],
            optimized_totals["observations"]["retrieve_columns_calls"],
        ),
        "retrieve_columns_output_chars_percent": percent_delta(
            baseline_totals["observations"]["retrieve_columns_output_chars"],
            optimized_totals["observations"]["retrieve_columns_output_chars"],
        ),
        "select_seed_output_chars_percent": percent_delta(
            baseline_totals["observations"]["select_seed_output_chars"],
            optimized_totals["observations"]["select_seed_output_chars"],
        ),
    }

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "baseline": baseline_totals,
        "optimized": optimized_totals,
        "delta_percent": summary,
        "optimized_cases_with_retrieval_expansion": [
            case["case_number"]
            for case in cases
            if case["optimized"]["observations"]["retrieve_columns_calls"] > 1
        ],
        "largest_reported_usage_increases": [
            {
                "case_number": case["case_number"],
                "delta": case["delta"]["reported_usage_total"],
                "percent": case["delta"]["reported_usage_total_percent"],
            }
            for case in sorted(
                cases, key=lambda item: item["delta"]["reported_usage_total"], reverse=True
            )[:8]
        ],
        "largest_cost_increases": [
            {
                "case_number": case["case_number"],
                "delta": case["delta"]["total_cost"],
                "percent": case["delta"]["total_cost_percent"],
            }
            for case in sorted(cases, key=lambda item: item["delta"]["total_cost"], reverse=True)[:8]
        ],
        "largest_cost_decreases": [
            {
                "case_number": case["case_number"],
                "delta": case["delta"]["total_cost"],
                "percent": case["delta"]["total_cost_percent"],
            }
            for case in sorted(cases, key=lambda item: item["delta"]["total_cost"])[:8]
        ],
        "cases": cases,
    }
    atomic_json(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, indent=2))
    print(f"OUTPUT {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
