#!/usr/bin/env python3
"""
Resumable Group E VP API test runner.

- Calls localhost /vp/build for each case
- Saves results after every case (safe to Ctrl+C / re-run)
- Skips cases already marked done
- Does NOT score structure — agent reviews after the run finishes

Usage:
  python3 scripts/run_group_e_suite.py

Watch progress:
  outputs/group_e_suite_20260918/PROGRESS.md
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "http://127.0.0.1:8000/vp/build"
TIMEOUT = 300  # 5 min hard cap per request
GAP_SEC = 15  # short pause between cases
OUT_DIR = Path(__file__).resolve().parents[1] / "outputs" / "group_e_suite_20260918"
RESULTS_PATH = OUT_DIR / "results.json"
PROGRESS_PATH = OUT_DIR / "PROGRESS.md"

CASES = [
    ("E01", "Find customers whose average daily SMS revenue over the last 90 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-90DAYS AND INSTFCTDATE < CurrentTime AND SUM(V{AVG_DAILY_OG_SMS_REV}=f{Total_SMS_Revenue/90}) ${operator} ${value}"),
    ("E02", "Find customers whose total SMS revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Total_SMS_Revenue) ${operator} ${value}"),
    ("E03", "Find customers whose total data revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Total_Data_Revenue) ${operator} ${value}"),
    ("E04", "Find customers whose total uploaded data volume in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Upload) ${operator} ${value}"),
    ("E05", "Find customers whose total data roaming revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Roaming_Revenue) ${operator} ${value}"),
    ("E06", "Find customers whose total out-of-bundle data usage in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Outbundle_Usage) ${operator} ${value}"),
    ("E07", "Find customers whose total out-of-bundle data revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Outbundle_Revenue) ${operator} ${value}"),
    ("E08", "Find customers whose total in-bundle data usage in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Inbundle_Usage) ${operator} ${value}"),
    ("E09", "Find customers whose total in-bundle data revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Inbundle_Revenue) ${operator} ${value}"),
    ("E10", "Find customers whose total downloaded data volume in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Dwnload) ${operator} ${value}"),
    ("E11", "Find customers whose average daily outgoing roaming voice revenue over the last 90 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-90DAYS AND INSTFCTDATE < CurrentTime AND SUM(V{AVG_DAILY_ROAM_VOICE_REV}=f{OG_Voice_Roaming_Revenue/90}) ${operator} ${value}"),
    ("E12", "Find customers whose average daily outgoing roaming SMS revenue over the last 90 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-90DAYS AND INSTFCTDATE < CurrentTime AND SUM(V{AVG_DAILY_ROAM_SMS_REV}=f{OG_SMS_Roaming_Revenue/90}) ${operator} ${value}"),
    ("E13", "Find customers whose total voice revenue in the last 7 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-7DAYS AND INSTFCTDATE < CurrentTime AND SUM(Total_Voice_Revenue) ${operator} ${value}"),
    ("E14", "Find customers whose total voice revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Total_Voice_Revenue) ${operator} ${value}"),
    ("E15", "Find customers whose total outgoing on-net voice revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Onnet_Revenue) ${operator} ${value}"),
    ("E16", "Find customers whose total outgoing off-net voice revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Offnet_Revenue) ${operator} ${value}"),
    ("E17", "Find customers whose total outgoing IDD voice revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Idd_Revenue) ${operator} ${value}"),
    ("E18", "Find customers whose total outgoing call count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Total_Voice_count) ${operator} ${value}"),
    ("E19", "Find customers whose total outgoing on-net call count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Onnet_count) ${operator} ${value}"),
    ("E20", "Find customers whose total outgoing off-net call count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Offnet_count) ${operator} ${value}"),
    ("E21", "Find customers whose total outgoing IDD call count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Idd_count) ${operator} ${value}"),
    ("E22", "Find customers whose total outgoing roaming call count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Roaming_count) ${operator} ${value}"),
    ("E23", "Find customers whose total outgoing roaming SMS count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_SMS_Roaming_count) ${operator} ${value}"),
    ("E24", "Find customers whose total outgoing call minutes in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Total_Voice_MOU) ${operator} ${value}"),
    ("E25", "Find customers whose total outgoing on-net call minutes in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Onnet_MOU) ${operator} ${value}"),
    ("E26", "Find customers whose total outgoing off-net call minutes in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Offnet_MOU) ${operator} ${value}"),
    ("E27", "Find customers whose total outgoing IDD call minutes in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Idd_MOU) ${operator} ${value}"),
    ("E28", "Find customers whose total outgoing roaming call minutes in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Voice_Roaming_MOU) ${operator} ${value}"),
    ("E29", "Find customers whose total data usage in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Total_Data_Usage) ${operator} ${value}"),
    ("E30", "Find customers whose total outgoing on-net SMS revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_SMS_Onnet_Revenue) ${operator} ${value}"),
    ("E31", "Find customers whose total outgoing off-net SMS revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_SMS_Offnet_Revenue) ${operator} ${value}"),
    ("E32", "Find customers whose total outgoing IDD SMS revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_SMS_Idd_Revenue) ${operator} ${value}"),
    ("E33", "Find customers whose total total revenue yesterday is above a given threshold.",
     "INSTFCTDATE = CurrentTime-1DAYS AND SUM(Total_Revenue) ${operator} ${value}"),
    ("E34", "Find customers whose total total revenue in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Total_Revenue) ${operator} ${value}"),
    ("E35", "Find customers whose total total revenue this month is above a given threshold.",
     "INSTFCTDATE = CurrentMonth AND SUM(Total_Revenue) ${operator} ${value}"),
    ("E36", "Find customers whose total data roaming usage in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Data_Roaming_Usage) ${operator} ${value}"),
    ("E37", "Find customers whose total recharge count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Recharge_count) ${operator} ${value}"),
    ("E38", "Find customers whose total recharge count last month is above a given threshold.",
     "INSTFCTDATE = CurrentMonth-1MONTHS AND SUM(Recharge_count) ${operator} ${value}"),
    ("E39", "Find customers whose average weekly recharge amount over the last 4 weeks is above a given threshold.",
     "INSTFCTDATE >= CurrentWeek-4WEEKS AND INSTFCTDATE < CurrentWeek AND SUM(V{AVG_WEEKLY_RECHARGE_REV}=f{Recharge_revenue/4}) ${operator} ${value}"),
    ("E40", "Find customers whose average monthly recharge amount over the last 3 months is above a given threshold.",
     "INSTFCTDATE >= CurrentMonth-3MONTHS AND INSTFCTDATE < CurrentMonth AND SUM(V{AVG_MONTHLY_RECHARGE_REV}=f{Recharge_revenue/3}) ${operator} ${value}"),
    ("E41", "Find customers whose total recharge amount last week is above a given threshold.",
     "INSTFCTDATE = CurrentWeek-1WEEKS AND SUM(Recharge_revenue) ${operator} ${value}"),
    ("E42", "Find customers whose total recharge amount in the last 14 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-14DAYS AND INSTFCTDATE < CurrentTime AND SUM(Recharge_revenue) ${operator} ${value}"),
    ("E43", "Find customers whose total recharge amount in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(Recharge_revenue) ${operator} ${value}"),
    ("E44", "Find customers whose highest single recharge amount last week is above a given threshold.",
     "INSTFCTDATE = CurrentWeek-1WEEKS AND Max(Recharge_revenue) ${operator} ${value}"),
    ("E45", "Find customers whose highest single recharge amount in the last 7 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-7DAYS AND INSTFCTDATE < CurrentTime AND Max(Recharge_revenue) ${operator} ${value}"),
    ("E46", "Find customers whose highest single recharge amount in the last 14 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-14DAYS AND INSTFCTDATE < CurrentTime AND Max(Recharge_revenue) ${operator} ${value}"),
    ("E47", "Find customers whose highest single recharge amount in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND Max(Recharge_revenue) ${operator} ${value}"),
    ("E48", "Find customers whose highest single recharge amount last month is above a given threshold.",
     "INSTFCTDATE = CurrentMonth-1MONTHS AND Max(Recharge_revenue) ${operator} ${value}"),
    ("E49", "Find customers whose lowest single recharge amount last week is above a given threshold.",
     "INSTFCTDATE = CurrentWeek-1WEEKS AND Min(Recharge_revenue) ${operator} ${value}"),
    ("E50", "Find customers whose lowest single recharge amount in the last 7 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-7DAYS AND INSTFCTDATE < CurrentTime AND Min(Recharge_revenue) ${operator} ${value}"),
    ("E51", "Find customers whose lowest single recharge amount in the last 14 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-14DAYS AND INSTFCTDATE < CurrentTime AND Min(Recharge_revenue) ${operator} ${value}"),
    ("E52", "Find customers whose lowest single recharge amount in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND Min(Recharge_revenue) ${operator} ${value}"),
    ("E53", "Find customers whose lowest single recharge amount last month is above a given threshold.",
     "INSTFCTDATE = CurrentMonth-1MONTHS AND Min(Recharge_revenue) ${operator} ${value}"),
    ("E54", "Find customers based on the date of their most recent recharge amount activity within the last 90 days.",
     "INSTFCTDATE >= CurrentTime-90DAYS AND INSTFCTDATE < CurrentTime AND Recharge_revenue > 0 AND Max(INSTFCTDATE) ${operator} ${value}"),
    ("E55", "Find customers whose total outgoing SMS count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_Total_SMS_count) ${operator} ${value}"),
    ("E56", "Find customers whose total outgoing on-net SMS count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_SMS_Onnet_count) ${operator} ${value}"),
    ("E57", "Find customers whose total outgoing off-net SMS count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_SMS_Offnet_count) ${operator} ${value}"),
    ("E58", "Find customers whose total outgoing IDD SMS count in the last 30 days is above a given threshold.",
     "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime AND SUM(OG_SMS_Idd_count) ${operator} ${value}"),
    ("E59", "Find customers in a specified segment who received a promotion in the last X days.",
     "L_SENT_DATE >= CurrentTime-${X}DAYS AND L_SEGMENT_NAME ${operator} ${value} AND L_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) AND COUNT_ALL(L_MSISDN) > 0"),
    ("E60", "Find customers in a specified segment who received a bonus in the last X days.",
     "L_SENT_DATE >= CurrentTime-${X}DAYS AND L_SEGMENT_NAME ${operator} ${value} AND L_ACTION_TYPE IN LIST (BONUS;Bonus;bonus) AND COUNT_ALL(L_MSISDN) > 0"),
    ("E61", "Find customers in a specified segment who did not receive a promotion in the last X days.",
     "L_SENT_DATE >= CurrentTime-${X}DAYS AND L_SEGMENT_NAME ${operator} ${value} AND L_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) AND COUNT_ALL(L_MSISDN) = 0"),
    ("E62", "Find customers in a specified segment who did not receive a bonus in the last X days.",
     "L_SENT_DATE >= CurrentTime-${X}DAYS AND L_SEGMENT_NAME ${operator} ${value} AND L_ACTION_TYPE IN LIST (BONUS;Bonus;bonus) AND COUNT_ALL(L_MSISDN) = 0"),
]


def empty_case(case_id: str, sentence: str, expected: str) -> dict:
    return {
        "case": case_id,
        "sentence": sentence,
        "expected_parent_condition": expected,
        "request_id": None,
        "returned_parent_condition": None,
        "clarification": None,
        "ok": None,
        "failure_reason": None,
        "elapsed_sec": None,
        "error": None,
        "status": "pending",
        "finished_at": None,
    }


def load_state() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog = {c: (s, e) for c, s, e in CASES}

    if RESULTS_PATH.exists():
        state = json.loads(RESULTS_PATH.read_text())
        by_id = {c["case"]: c for c in state.get("cases", [])}
        merged = []
        for case_id, sentence, expected in CASES:
            if case_id in by_id:
                row = by_id[case_id]
                # keep prior API results; drop old scoring fields if present
                row.pop("structure_verdict", None)
                row.pop("remarks", None)
                if row.get("status") == "running":
                    row["status"] = "pending"
                    row["request_id"] = None
                row["sentence"] = sentence
                row["expected_parent_condition"] = expected
                merged.append(row)
            else:
                merged.append(empty_case(case_id, sentence, expected))
        state["cases"] = merged
        state["total"] = len(merged)
    else:
        state = {
            "total": len(CASES),
            "completed": 0,
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "cases": [empty_case(c, s, e) for c, s, e in CASES],
        }

    save_state(state)
    return state


def save_state(state: dict) -> None:
    state["completed"] = sum(1 for c in state["cases"] if c["status"] == "done")
    state["updated_at"] = datetime.now().isoformat()
    RESULTS_PATH.write_text(json.dumps(state, indent=2))
    write_progress(state)


def write_progress(state: dict) -> None:
    done = state["completed"]
    total = state["total"]
    running = next((c for c in state["cases"] if c["status"] == "running"), None)
    pending = sum(1 for c in state["cases"] if c["status"] == "pending")
    with_rule = sum(1 for c in state["cases"] if c["status"] == "done" and c.get("returned_parent_condition"))
    clar = sum(1 for c in state["cases"] if c["status"] == "done" and c.get("clarification") and not c.get("returned_parent_condition"))
    errors = sum(1 for c in state["cases"] if c["status"] == "done" and c.get("error"))

    lines = [
        "# Group E suite progress",
        "",
        f"**Updated:** {state['updated_at']}",
        f"**Done:** {done} / {total} ({(100 * done / total) if total else 0:.0f}%)",
        f"**Pending:** {pending}",
        f"**Currently running:** {running['case'] if running else '—'}",
        "",
        "## Completed so far",
        f"- returned a rule: {with_rule}",
        f"- clarification only: {clar}",
        f"- errors/timeouts: {errors}",
        "",
        "## Cases",
        "",
        "| Case | Status | OK | Elapsed (s) | Has rule | Clarification |",
        "|---|---|---|---:|---|---|",
    ]
    for c in state["cases"]:
        has_rule = "yes" if c.get("returned_parent_condition") else ("—" if c["status"] != "done" else "no")
        clar_flag = "yes" if c.get("clarification") else ("—" if c["status"] != "done" else "no")
        lines.append(
            f"| {c['case']} | {c['status']} | {c.get('ok') if c.get('ok') is not None else '—'} | "
            f"{c.get('elapsed_sec') if c.get('elapsed_sec') is not None else '—'} | {has_rule} | {clar_flag} |"
        )
    PROGRESS_PATH.write_text("\n".join(lines) + "\n")


def call_api(sentence: str, request_id: str) -> dict:
    payload = {
        "client": "omantel",
        "sentence": sentence,
        "request_id": request_id,
        "session_id": request_id,
        "user_id": "group-e-suite",
    }
    req = urllib.request.Request(
        BASE,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    state = load_state()
    remaining = [c for c in state["cases"] if c["status"] != "done"]
    print(f"Checkpoint: {state['completed']}/{state['total']} done, {len(remaining)} remaining", flush=True)
    print(f"Progress file: {PROGRESS_PATH}", flush=True)

    if not remaining:
        print("Nothing to do — suite already complete.", flush=True)
        return

    first = True
    for case in remaining:
        if not first:
            print(f"Gap {GAP_SEC}s before {case['case']}...", flush=True)
            time.sleep(GAP_SEC)
        first = False

        req_id = f"{case['case']}-20260918-{int(time.time())}"
        case["status"] = "running"
        case["request_id"] = req_id
        save_state(state)
        print(f"[{state['completed']}/{state['total']}] Starting {case['case']} ...", flush=True)

        started = time.time()
        try:
            body = call_api(case["sentence"], req_id)
            case["elapsed_sec"] = round(time.time() - started, 1)
            case["ok"] = body.get("ok")
            case["returned_parent_condition"] = body.get("parent_condition")
            case["clarification"] = body.get("clarification_question")
            case["failure_reason"] = body.get("failure_reason")
            case["error"] = None
        except Exception as e:  # noqa: BLE001
            case["elapsed_sec"] = round(time.time() - started, 1)
            case["error"] = str(e)
            case["ok"] = False
            case["returned_parent_condition"] = None
            case["clarification"] = None
            case["failure_reason"] = None

        case["status"] = "done"
        case["finished_at"] = datetime.now().isoformat()
        save_state(state)
        print(
            f"Finished {case['case']} in {case['elapsed_sec']}s "
            f"ok={case['ok']} ({state['completed']}/{state['total']})",
            flush=True,
        )

    print("ALL_DONE", RESULTS_PATH, flush=True)


if __name__ == "__main__":
    main()
