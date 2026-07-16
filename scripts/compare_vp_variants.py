#!/usr/bin/env python3
"""Run exact VP cases sequentially against baseline and optimized endpoints."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_DIR / "traces" / "omantel_requested_cases_consolidated_final_20260714.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def get_json(url: str, timeout: float = 10.0) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw_body": body}
        return exc.code, parsed


def endpoint(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def parse_args() -> argparse.Namespace:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--baseline", default="http://127.0.0.1:8000")
    parser.add_argument("--optimized", default="http://127.0.0.1:8001")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "traces" / f"vp_paired_comparison_{timestamp}.json")
    parser.add_argument("--case", action="append", type=int, dest="case_numbers")
    parser.add_argument("--start-case", type=int, default=1)
    parser.add_argument("--end-case", type=int, default=34)
    return parser.parse_args()


def load_sentences(path: Path) -> list[str]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"Expected a JSON list in {path}")
    sentences = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not str(row.get("sentence") or "").strip():
            raise SystemExit(f"Case {index} has no sentence")
        sentences.append(str(row["sentence"]))
    return sentences


def run_variant(base_url: str, sentence: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    entry: dict[str, Any] = {
        "endpoint": endpoint(base_url, "/vp/build"),
        "started_at": utc_now(),
        "finished_at": None,
        "elapsed_seconds": None,
        "http_status": None,
        "transport_error": None,
        "response": None,
    }
    try:
        entry["http_status"], entry["response"] = post_json(
            entry["endpoint"],
            {"client": "omantel", "sentence": sentence},
            timeout,
        )
    except Exception as exc:
        entry["transport_error"] = f"{type(exc).__name__}: {exc}"
    entry["finished_at"] = utc_now()
    entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return entry


def request_id(entry: dict[str, Any]) -> str | None:
    response = entry.get("response")
    return str(response.get("request_id")) if isinstance(response, dict) and response.get("request_id") else None


def comparison_summary(baseline: dict[str, Any], optimized: dict[str, Any]) -> dict[str, Any]:
    base_response = baseline.get("response") if isinstance(baseline.get("response"), dict) else {}
    opt_response = optimized.get("response") if isinstance(optimized.get("response"), dict) else {}
    base_condition = base_response.get("parent_condition")
    opt_condition = opt_response.get("parent_condition")
    exact_equal = bool(base_condition and opt_condition and base_condition == opt_condition)
    return {
        "baseline_request_id": request_id(baseline),
        "optimized_request_id": request_id(optimized),
        "baseline_ok": base_response.get("ok"),
        "optimized_ok": opt_response.get("ok"),
        "baseline_validation_ok": (base_response.get("validation") or {}).get("ok"),
        "optimized_validation_ok": (opt_response.get("validation") or {}).get("ok"),
        "exact_condition_equal": exact_equal,
        "semantic_review": "equivalent_exact" if exact_equal else "pending_review",
        "latency_delta_seconds": round(
            float(optimized.get("elapsed_seconds") or 0) - float(baseline.get("elapsed_seconds") or 0),
            3,
        ),
        "langfuse_efficiency_review": "pending_export",
    }


def main() -> int:
    args = parse_args()
    sentences = load_sentences(args.cases)
    if args.case_numbers:
        case_numbers = list(dict.fromkeys(args.case_numbers))
    else:
        case_numbers = list(range(args.start_case, args.end_case + 1))
    if any(number < 1 or number > len(sentences) for number in case_numbers):
        raise SystemExit(f"Invalid cases {case_numbers}; source contains {len(sentences)} cases")

    environments = {"baseline": args.baseline, "optimized": args.optimized}
    health: dict[str, Any] = {}
    for variant, base_url in environments.items():
        try:
            status, response = get_json(endpoint(base_url, "/health"))
            health[variant] = {"status": status, "response": response}
        except Exception as exc:
            health[variant] = {"status": None, "error": f"{type(exc).__name__}: {exc}"}
    if any(item.get("status") != 200 for item in health.values()):
        raise SystemExit("Both endpoints must pass /health before paired testing: " + json.dumps(health))

    variant_labels: dict[str, str | None] = {}
    for variant, base_url in environments.items():
        try:
            _, response = get_json(endpoint(base_url, "/observability"))
            label = response.get("variant") if isinstance(response, dict) else None
            variant_labels[variant] = str(label) if label else None
        except Exception:
            variant_labels[variant] = None
    if variant_labels["baseline"] and variant_labels["baseline"] == variant_labels["optimized"]:
        raise SystemExit(
            "Endpoints report the same VP variant and are not a valid pair: "
            + json.dumps(variant_labels)
        )

    run: dict[str, Any] = {
        "comparison_id": f"vp-pair-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "client": "omantel",
        "source": str(args.cases.resolve()),
        "environments": environments,
        "health": health,
        "variant_labels": variant_labels,
        "started_at": utc_now(),
        "completed_at": None,
        "cases": [],
    }
    atomic_json(args.output, run)

    for case_number in case_numbers:
        sentence = sentences[case_number - 1]
        print(f"[{case_number}/{len(sentences)}] BASELINE START {sentence}", flush=True)
        baseline = run_variant(args.baseline, sentence, args.timeout)
        print(
            f"[{case_number}/{len(sentences)}] BASELINE END request_id={request_id(baseline)} "
            f"elapsed={baseline['elapsed_seconds']}s",
            flush=True,
        )
        case_entry = {
            "case_number": case_number,
            "sentence": sentence,
            "baseline": baseline,
            "optimized": None,
            "comparison": None,
        }
        run["cases"].append(case_entry)
        atomic_json(args.output, run)

        print(f"[{case_number}/{len(sentences)}] OPTIMIZED START", flush=True)
        optimized = run_variant(args.optimized, sentence, args.timeout)
        print(
            f"[{case_number}/{len(sentences)}] OPTIMIZED END request_id={request_id(optimized)} "
            f"elapsed={optimized['elapsed_seconds']}s",
            flush=True,
        )
        case_entry["optimized"] = optimized
        case_entry["comparison"] = comparison_summary(baseline, optimized)
        atomic_json(args.output, run)

    run["completed_at"] = utc_now()
    atomic_json(args.output, run)
    print(f"PAIRED_LOG {args.output.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
