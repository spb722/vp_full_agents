"""Export full Langfuse traces and nested observations for VP API requests.

Credentials are loaded from the project .env file or the current environment.
The script never prints or writes credentials.

Examples:
    .venv/bin/python scripts/export_langfuse_traces.py
    .venv/bin/python scripts/export_langfuse_traces.py --request-id d90a735337
    .venv/bin/python scripts/export_langfuse_traces.py \
        --response-log traces/omantel_requested_cases_20260713.json
"""

from __future__ import annotations

import argparse
import csv
import inspect
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from dotenv import load_dotenv
from langfuse import Langfuse


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RESPONSE_LOG = PROJECT_DIR / "traces" / "omantel_requested_cases_20260713.json"
TRACE_FIELDS = "core,io,scores,observations,metrics"
OBSERVATION_FIELDS = "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context"
OBSERVATION_CSV_FIELDS = [
    "applicationRequestId",
    "langfuseTraceId",
    "traceName",
    "id",
    "parentObservationId",
    "type",
    "name",
    "startTime",
    "endTime",
    "completionStartTime",
    "environment",
    "version",
    "release",
    "level",
    "statusMessage",
    "providedModelName",
    "internalModelId",
    "modelId",
    "modelParameters",
    "usageDetails",
    "costDetails",
    "totalCost",
    "latency",
    "timeToFirstToken",
    "promptId",
    "promptName",
    "promptVersion",
    "input",
    "output",
    "metadata",
    "tags",
]
TRACE_SUMMARY_FIELDS = [
    "applicationRequestId",
    "expectedTraceName",
    "langfuseTraceId",
    "timestamp",
    "name",
    "sessionId",
    "environment",
    "version",
    "release",
    "latency",
    "totalCost",
    "observationCount",
    "observationTypeCounts",
    "usageTotals",
    "costTotals",
    "tags",
]


T = TypeVar("T")
MIN_API_INTERVAL_SECONDS = 1.1
_last_api_call_at = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export complete Langfuse traces and observations for VP request IDs."
    )
    parser.add_argument(
        "--response-log",
        type=Path,
        default=DEFAULT_RESPONSE_LOG,
        help="JSON API-response log used to discover request IDs.",
    )
    parser.add_argument(
        "--request-id",
        action="append",
        default=[],
        help="Request ID to export. Repeat the option or pass comma-separated IDs.",
    )
    parser.add_argument("--client", default="omantel", help="Client used in the VP trace name.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination directory. Defaults to traces/langfuse_export_<UTC timestamp>.",
    )
    parser.add_argument(
        "--expand-metadata",
        help="Comma-separated observation metadata keys that Langfuse should return without truncation.",
    )
    parser.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Exit non-zero when any requested trace is missing or fails to export.",
    )
    return parser.parse_args()


def load_request_ids(args: argparse.Namespace) -> list[str]:
    explicit = [part.strip() for value in args.request_id for part in value.split(",") if part.strip()]
    if explicit:
        return list(dict.fromkeys(explicit))

    path = args.response_log.expanduser().resolve()
    if not path.exists():
        raise SystemExit(
            f"Response log not found: {path}. Pass one or more --request-id values or a valid --response-log."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read response log {path}: {exc}") from exc

    if not isinstance(payload, list):
        raise SystemExit(f"Expected a JSON array in response log: {path}")
    request_ids = [
        str(item["request_id"]).strip()
        for item in payload
        if isinstance(item, dict) and item.get("request_id")
    ]
    if not request_ids:
        raise SystemExit(f"No request_id values found in response log: {path}")
    return list(dict.fromkeys(request_ids))


def create_client() -> tuple[Langfuse, str]:
    load_dotenv(PROJECT_DIR / ".env", override=False)
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    base_url = (
        os.getenv("LANGFUSE_BASE_URL")
        or os.getenv("LANGFUSE_HOST")
        or os.getenv("LANGFUSE_URL")
        or "https://cloud.langfuse.com"
    ).rstrip("/")
    if not public_key or not secret_key:
        raise SystemExit(
            "Langfuse credentials are missing. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY "
            "in the environment or project .env file."
        )
    return Langfuse(public_key=public_key, secret_key=secret_key, base_url=base_url), base_url


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if hasattr(value, "dict"):
        return jsonable(value.dict(by_alias=True))
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def api_call(operation: Callable[[], T], *, attempts: int = 4) -> T:
    global _last_api_call_at
    for attempt in range(1, attempts + 1):
        remaining = MIN_API_INTERVAL_SECONDS - (time.monotonic() - _last_api_call_at)
        if remaining > 0:
            time.sleep(remaining)
        try:
            result = operation()
            _last_api_call_at = time.monotonic()
            return result
        except Exception as exc:
            _last_api_call_at = time.monotonic()
            status_code = getattr(exc, "status_code", None)
            retryable = status_code in {408, 429} or (isinstance(status_code, int) and status_code >= 500)
            if attempt == attempts or (status_code is not None and not retryable):
                raise
            retry_after = 0.0
            body = getattr(exc, "body", None)
            if isinstance(body, dict):
                details = body.get("details")
                if isinstance(details, dict):
                    retry_after = float(details.get("retryAfterSeconds") or 0)
            time.sleep(max(retry_after, 2 ** (attempt - 1)))
    raise RuntimeError("Unreachable retry state")


def exact_trace_matches(langfuse: Langfuse, trace_name: str) -> list[Any]:
    response = api_call(
        lambda: langfuse.api.trace.list(
            name=trace_name,
            limit=100,
            fields=TRACE_FIELDS,
        )
    )
    return [trace for trace in response.data if trace.name == trace_name]


def fetch_observations(
    langfuse: Langfuse,
    trace_id: str,
    *,
    expand_metadata: str | None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    parameters = inspect.signature(langfuse.api.observations.get_many).parameters
    cursor: str | None = None
    page_number = 1
    while True:
        kwargs: dict[str, Any] = {"trace_id": trace_id, "limit": 100}
        if "fields" in parameters:
            kwargs["fields"] = OBSERVATION_FIELDS
        if "expand_metadata" in parameters and expand_metadata:
            kwargs["expand_metadata"] = expand_metadata
        if "cursor" in parameters:
            kwargs["cursor"] = cursor
        elif "page" in parameters:
            kwargs["page"] = page_number
        page = api_call(
            lambda kwargs=kwargs: langfuse.api.observations.get_many(**kwargs)
        )
        observations.extend(jsonable(item) for item in page.data)
        if "cursor" in parameters:
            cursor = getattr(page.meta, "cursor", None)
            if not cursor:
                return observations
        else:
            total_pages = int(getattr(page.meta, "total_pages", page_number))
            if page_number >= total_pages:
                return observations
            page_number += 1


def fetch_trace(langfuse: Langfuse, trace_id: str) -> Any:
    parameters = inspect.signature(langfuse.api.trace.get).parameters
    if "fields" in parameters:
        return api_call(lambda: langfuse.api.trace.get(trace_id, fields=TRACE_FIELDS))
    return api_call(lambda: langfuse.api.trace.get(trace_id))


def sum_numeric_maps(items: Iterable[dict[str, Any]], field: str) -> dict[str, float | int]:
    totals: Counter[str] = Counter()
    all_integers = True
    for item in items:
        values = item.get(field)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[str(key)] += value
                all_integers = all_integers and isinstance(value, int)
    if all_integers:
        return {key: int(value) for key, value in sorted(totals.items())}
    return {key: float(value) for key, value in sorted(totals.items())}


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def observation_csv_row(request_id: str, trace_id: str, item: dict[str, Any]) -> dict[str, Any]:
    row = {field: item.get(field) for field in OBSERVATION_CSV_FIELDS}
    row["applicationRequestId"] = request_id
    row["langfuseTraceId"] = trace_id
    return {key: csv_value(value) for key, value in row.items()}


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    request_ids = load_request_ids(args)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else (PROJECT_DIR / "traces" / f"langfuse_export_{timestamp}")
    )
    bundle_dir = output_dir / "requests"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    langfuse, base_url = create_client()
    manifest_entries: list[dict[str, Any]] = []
    all_observations: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    try:
        for index, request_id in enumerate(request_ids, start=1):
            trace_name = f"vp-build:{args.client}:{request_id}"
            print(f"[{index:02d}/{len(request_ids)}] {trace_name}", flush=True)
            try:
                matches = exact_trace_matches(langfuse, trace_name)
                if not matches:
                    manifest_entries.append(
                        {
                            "applicationRequestId": request_id,
                            "expectedTraceName": trace_name,
                            "status": "missing",
                            "error": None,
                        }
                    )
                    print("  missing", flush=True)
                    continue

                request_matches: list[dict[str, Any]] = []
                for match in matches:
                    trace = fetch_trace(langfuse, match.id)
                    trace_data = jsonable(trace)
                    observations = fetch_observations(
                        langfuse,
                        match.id,
                        expand_metadata=args.expand_metadata,
                    )
                    request_matches.append({"trace": trace_data, "observations": observations})

                    type_counts = dict(sorted(Counter(item.get("type") or "UNKNOWN" for item in observations).items()))
                    usage_totals = sum_numeric_maps(observations, "usageDetails")
                    cost_totals = sum_numeric_maps(observations, "costDetails")
                    summary_rows.append(
                        {
                            "applicationRequestId": request_id,
                            "expectedTraceName": trace_name,
                            "langfuseTraceId": match.id,
                            "timestamp": csv_value(trace_data.get("timestamp")),
                            "name": csv_value(trace_data.get("name")),
                            "sessionId": csv_value(trace_data.get("sessionId")),
                            "environment": csv_value(trace_data.get("environment")),
                            "version": csv_value(trace_data.get("version")),
                            "release": csv_value(trace_data.get("release")),
                            "latency": csv_value(trace_data.get("latency")),
                            "totalCost": csv_value(trace_data.get("totalCost")),
                            "observationCount": len(observations),
                            "observationTypeCounts": csv_value(type_counts),
                            "usageTotals": csv_value(usage_totals),
                            "costTotals": csv_value(cost_totals),
                            "tags": csv_value(trace_data.get("tags")),
                        }
                    )
                    for observation in observations:
                        flattened = {
                            "applicationRequestId": request_id,
                            "langfuseTraceId": match.id,
                            **observation,
                        }
                        all_observations.append(flattened)
                        observation_rows.append(observation_csv_row(request_id, match.id, observation))

                bundle = {
                    "applicationRequestId": request_id,
                    "expectedTraceName": trace_name,
                    "matchCount": len(request_matches),
                    "matches": request_matches,
                }
                write_json(bundle_dir / f"{request_id}.json", bundle)
                manifest_entries.append(
                    {
                        "applicationRequestId": request_id,
                        "expectedTraceName": trace_name,
                        "status": "exported",
                        "matchCount": len(request_matches),
                        "traceIds": [item["trace"]["id"] for item in request_matches],
                        "observationCount": sum(len(item["observations"]) for item in request_matches),
                        "error": None,
                    }
                )
                print(
                    f"  exported {len(request_matches)} trace(s), "
                    f"{manifest_entries[-1]['observationCount']} observation(s)",
                    flush=True,
                )
            except Exception as exc:
                manifest_entries.append(
                    {
                        "applicationRequestId": request_id,
                        "expectedTraceName": trace_name,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(f"  error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

        with (output_dir / "observations.jsonl").open("w", encoding="utf-8") as handle:
            for item in all_observations:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        write_csv(output_dir / "observations.csv", OBSERVATION_CSV_FIELDS, observation_rows)
        write_csv(output_dir / "trace_summary.csv", TRACE_SUMMARY_FIELDS, summary_rows)

        status_counts = dict(sorted(Counter(item["status"] for item in manifest_entries).items()))
        manifest = {
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "langfuseBaseUrl": base_url,
            "client": args.client,
            "traceFields": TRACE_FIELDS,
            "observationFields": OBSERVATION_FIELDS,
            "sourceResponseLog": None if args.request_id else str(args.response_log.expanduser().resolve()),
            "requestedRequestIds": request_ids,
            "requestedCount": len(request_ids),
            "statusCounts": status_counts,
            "exportedObservationCount": len(all_observations),
            "entries": manifest_entries,
        }
        write_json(output_dir / "manifest.json", manifest)

        print(f"Saved export to: {output_dir}")
        print(f"Status counts: {json.dumps(status_counts, sort_keys=True)}")
        print(f"Observations: {len(all_observations)}")
        failed = any(item["status"] != "exported" for item in manifest_entries)
        return 1 if args.fail_on_missing and failed else 0
    finally:
        langfuse.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
