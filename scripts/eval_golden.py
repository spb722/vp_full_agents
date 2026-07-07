from __future__ import annotations

import argparse
import json

from vp_agent.golden import DEFAULT_GOLDEN_PATH, condition_column, find_360_snapshot_cases, has_date_condition, load_golden_cases
from vp_agent.tools.normalize import normalize_slots
from vp_agent.tools.plan import build_parent_condition


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate deterministic VP builder against golden CSV cases.")
    parser.add_argument("--path", default=str(DEFAULT_GOLDEN_PATH), help="Golden CSV path")
    parser.add_argument("--client", default="omantel", choices=["omantel", "airtel"])
    parser.add_argument("--snapshots-only", action="store_true", help="Only evaluate snapshot/no-date cases")
    args = parser.parse_args()

    rows = load_golden_cases(args.path)
    cases = find_360_snapshot_cases(rows) if args.snapshots_only else rows
    results = []

    for index, row in enumerate(cases, start=1):
        nl_input = row.get("NL Input", "")
        expected = row.get("Expected Output", "")
        expected_col = condition_column(expected)
        record = {
            "index": index,
            "input": nl_input,
            "expected_column": expected_col,
            "expected_has_date_condition": has_date_condition(expected),
        }
        try:
            slots = normalize_slots(nl_input, client=args.client)
            built = build_parent_condition(slots, client=args.client, request=nl_input)
            rule = built["rendered"]["parent_condition"]
            record.update(
                {
                    "ok": built["ok"],
                    "path": built["plan"]["path"],
                    "snapshot": built["plan"]["snapshot"],
                    "selected_column": built["plan"]["main_column"]["feature_name"],
                    "rule": rule,
                    "rule_has_date_condition": has_date_condition(rule),
                    "column_match": expected_col is not None and expected_col in rule,
                }
            )
        except Exception as exc:
            record.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        results.append(record)

    summary = {
        "path": args.path,
        "cases": len(results),
        "ok": sum(1 for row in results if row.get("ok")),
        "column_match": sum(1 for row in results if row.get("column_match")),
        "date_rule_violations": sum(1 for row in results if row.get("snapshot") and row.get("rule_has_date_condition")),
        "results": results,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

