#!/usr/bin/env python3
"""Rerun Omantel regression cases 22-34 and persist every full response."""

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
DEFAULT_SOURCE = PROJECT_DIR / "traces" / "omantel_requested_cases_20260713.json"
DEFAULT_POST_RESET = PROJECT_DIR / "traces" / "omantel_cases_22_34_post_reset_20260714.json"
DEFAULT_CONSOLIDATED = PROJECT_DIR / "traces" / "omantel_requested_cases_consolidated_20260714.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw_body": body}
        return exc.code, parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--post-reset-log", type=Path, default=DEFAULT_POST_RESET)
    parser.add_argument("--consolidated-log", type=Path, default=DEFAULT_CONSOLIDATED)
    parser.add_argument("--url", default="http://127.0.0.1:8000/vp/build")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--start-case", type=int, default=22)
    parser.add_argument("--end-case", type=int, default=34)
    parser.add_argument("--case", action="append", type=int, dest="case_numbers")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    original: list[dict[str, Any]] = json.loads(args.source.read_text(encoding="utf-8"))
    if args.case_numbers:
        case_numbers = list(dict.fromkeys(args.case_numbers))
    else:
        if not 1 <= args.start_case <= args.end_case <= len(original):
            raise SystemExit(
                f"Invalid case range {args.start_case}-{args.end_case}; source contains {len(original)} cases"
            )
        case_numbers = list(range(args.start_case, args.end_case + 1))
    if any(case_number < 1 or case_number > len(original) for case_number in case_numbers):
        raise SystemExit(f"Invalid case selection {case_numbers}; source contains {len(original)} cases")
    selected = [(case_number, original[case_number - 1]) for case_number in case_numbers]

    run: dict[str, Any] = {
        "client": "omantel",
        "source_log": str(args.source.resolve()),
        "endpoint": args.url,
        "started_at": utc_now(),
        "completed_at": None,
        "cases": [],
    }
    atomic_json(args.post_reset_log, run)

    for case_number, prior in selected:
        sentence = prior["sentence"]
        started = time.monotonic()
        started_at = utc_now()
        print(f"[{case_number}/34] START {sentence}", flush=True)
        try:
            http_status, response = post_json(
                args.url,
                {"client": "omantel", "sentence": sentence},
                timeout=args.timeout,
            )
            transport_error = None
        except Exception as exc:  # preserve transport failures in the audit log
            http_status = None
            response = None
            transport_error = f"{type(exc).__name__}: {exc}"

        entry = {
            "case_number": case_number,
            "sentence": sentence,
            "started_at": started_at,
            "finished_at": utc_now(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "http_status": http_status,
            "transport_error": transport_error,
            "response": response,
        }
        run["cases"].append(entry)
        atomic_json(args.post_reset_log, run)
        request_id = response.get("request_id") if isinstance(response, dict) else None
        ok = response.get("ok") if isinstance(response, dict) else None
        print(
            f"[{case_number}/34] END status={http_status} ok={ok} "
            f"request_id={request_id} elapsed={entry['elapsed_seconds']}s",
            flush=True,
        )

    run["completed_at"] = utc_now()
    atomic_json(args.post_reset_log, run)

    consolidated = list(original)
    for entry in run["cases"]:
        index = entry["case_number"] - 1
        response = entry["response"]
        if isinstance(response, dict):
            consolidated[index] = response
        else:
            consolidated[index] = {
                "ok": False,
                "client": "omantel",
                "sentence": entry["sentence"],
                "failure_reason": entry["transport_error"] or "No JSON response",
            }
    atomic_json(args.consolidated_log, consolidated)
    print(f"POST_RESET_LOG {args.post_reset_log.resolve()}", flush=True)
    print(f"CONSOLIDATED_LOG {args.consolidated_log.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
