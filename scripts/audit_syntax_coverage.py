"""Audit whether the agent's procedural memory covers real production syntax.

Step-1 harness check. The skills in `.claude/skills` are the agent's ONLY
procedural memory: if a construct appears in production VPs but is documented
nowhere, the agent cannot reliably produce it no matter how capable the model
is or whether a matching seed exists.

This is deterministic and makes no model calls.

Usage:
    PYTHONPATH=. python scripts/audit_syntax_coverage.py
    PYTHONPATH=. python scripts/audit_syntax_coverage.py --client airtel --json
    PYTHONPATH=. python scripts/audit_syntax_coverage.py --strict   # exit 1 on gaps
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

from vp_agent.config import PROJECT_DIR
from vp_agent.data import load_vp_descriptions


SKILLS_DIR = PROJECT_DIR / ".claude" / "skills"

# Functions the renderer/validator already treat as known engine syntax. Any
# other `Word(` in production is an unknown-unknown worth surfacing.
KNOWN_FUNCTIONS = {"sum", "count_all", "avg", "max", "min", "f", "v"}


@dataclass(frozen=True)
class Construct:
    name: str
    description: str
    in_rule: re.Pattern[str]
    in_docs: re.Pattern[str]


CONSTRUCTS: tuple[Construct, ...] = (
    Construct(
        "in_list",
        "membership list: COLUMN IN LIST (a;b)",
        re.compile(r"\bIN\s+LIST\b", re.I),
        re.compile(r"IN LIST", re.I),
    ),
    Construct(
        "not_in_list",
        "negated membership: COLUMN NOT IN LIST (a;b)",
        re.compile(r"\bNOT\s+IN\s+LIST\b", re.I),
        re.compile(r"NOT IN LIST", re.I),
    ),
    Construct(
        "not_null_guard",
        "presence guard: COLUMN <> NULL",
        re.compile(r"<>\s*NULL", re.I),
        re.compile(r"<>\s*NULL", re.I),
    ),
    Construct(
        "is_null",
        "absence guard: COLUMN = NULL",
        re.compile(r"(?<![<>!])=\s*NULL", re.I),
        re.compile(r"=\s*NULL", re.I),
    ),
    Construct(
        "between",
        "range: COLUMN BETWEEN low AND high",
        re.compile(r"\bBETWEEN\b", re.I),
        re.compile(r"\bBETWEEN\b", re.I),
    ),
    Construct(
        "in_range",
        "range: COLUMN IN RANGE (low;high)",
        re.compile(r"\bIN\s+RANGE\b", re.I),
        re.compile(r"IN RANGE", re.I),
    ),
    Construct(
        "like",
        "pattern match: COLUMN LIKE / NOT LIKE",
        re.compile(r"\bLIKE\b", re.I),
        re.compile(r"\bLIKE\b", re.I),
    ),
    Construct(
        "groupby_single",
        "per-entity aggregate: AGG(col)__groupby_KEY",
        re.compile(r"__groupby_[A-Za-z][A-Za-z0-9_]*"),
        re.compile(r"__groupby_"),
    ),
    Construct(
        "groupby_multi",
        "multi-key grouping: AGG(col)__groupby_KEY1,KEY2",
        re.compile(r"__groupby_[A-Za-z][A-Za-z0-9_]*\s*,\s*[A-Za-z]"),
        re.compile(r"__groupby_[A-Za-z][A-Za-z0-9_]*\s*,\s*[A-Za-z]"),
    ),
    Construct(
        "virtual_formula",
        "virtual KPI formula: V{name}=f{expr}",
        re.compile(r"V\s*\{[^}]*\}\s*=\s*f\s*\{"),
        re.compile(r"V\{[^}]*\}\s*=\s*f\{"),
    ),
    Construct(
        "max_date_guard",
        "recency guard: Max(DATE_COLUMN) <> NULL",
        re.compile(r"\bMax\s*\([^)]*\)\s*<>\s*NULL", re.I),
        re.compile(r"Max\s*\([^)]*\)\s*<>\s*NULL", re.I),
    ),
    Construct(
        "or_group",
        "alternation group: (A ... OR B ...)",
        re.compile(r"\(\s*[^()]*\s+OR\s+[^()]*\)"),
        re.compile(r"\(\s*[^()]*\s+OR\s+[^()]*\)"),
    ),
    Construct(
        "or_cg_pair",
        "control-group twin: (KEY ${operator} ${value} OR KEY ${operator} ${value}_CG)",
        re.compile(r"\$\{value\}_CG"),
        re.compile(r"\$\{value\}_CG"),
    ),
    Construct(
        "runtime_parameter",
        "extra runtime parameter beyond operator/value, e.g. ${X} or ${NoOfDays}",
        re.compile(r"\$\{(?!operator\b|value\b)[A-Za-z_][A-Za-z0-9_]*\}"),
        re.compile(r"\$\{(?:X|NoOfDays)\}"),
    ),
    Construct(
        "anchor_days",
        "rolling day window: CurrentTime-NDAYS",
        re.compile(r"CurrentTime\s*-\s*\$?\{?[A-Za-z0-9_]+\}?\s*DAYS", re.I),
        re.compile(r"CurrentTime-\{?N?[A-Za-z0-9_]*\}?DAYS", re.I),
    ),
    Construct(
        "anchor_weeks",
        "calendar week window: CurrentWeek-NWEEKS",
        re.compile(r"CurrentWeek\s*-\s*\$?\{?[A-Za-z0-9_]+\}?\s*WEEKS", re.I),
        re.compile(r"CurrentWeek-\{?N?[A-Za-z0-9_]*\}?WEEKS", re.I),
    ),
    Construct(
        "anchor_months",
        "calendar month window: CurrentMonth-NMONTHS",
        re.compile(r"CurrentMonth\s*-\s*\$?\{?[A-Za-z0-9_]+\}?\s*MONTHS", re.I),
        re.compile(r"CurrentMonth-\{?N?[A-Za-z0-9_]*\}?MONTHS", re.I),
    ),
    Construct(
        "date_equality_period",
        "period equality rather than a range: DATE = CurrentMonth-1MONTHS",
        re.compile(r"[A-Za-z0-9_]+\s*=\s*Current(?:Time|Week|Month)", re.I),
        # The lookbehind keeps ">= CurrentMonth" from counting as documentation
        # for the equality form.
        re.compile(r"(?<![<>!])=\s*Current(?:Time|Week|Month)", re.I),
    ),
    Construct(
        "arithmetic_expression",
        "arithmetic between KPI operands: a - b / c * 100",
        re.compile(r"[A-Za-z0-9_)]\s*[*/]\s*[A-Za-z0-9_(]"),
        re.compile(r"\*\s*100|f\{[^}]*[/*]"),
    ),
)


def _skills_text() -> str:
    parts = [path.read_text(encoding="utf-8") for path in sorted(SKILLS_DIR.rglob("*.md"))]
    return "\n".join(parts)


def _placeholder_pair_counts(condition: str) -> tuple[int, int]:
    return condition.count("${operator}"), condition.count("${value}")


def audit(client: str) -> dict:
    rows = load_vp_descriptions(client)
    docs = _skills_text()

    findings: list[dict] = []
    for construct in CONSTRUCTS:
        users = [
            str(row.get("VIRTUAL_PROFILE_NAME") or "").strip()
            for row in rows
            if construct.in_rule.search(str(row.get("PARENT_CONDITION") or ""))
        ]
        users = [name for name in users if name]
        if not users:
            continue
        findings.append(
            {
                "construct": construct.name,
                "description": construct.description,
                "vp_count": len(users),
                "documented": bool(construct.in_docs.search(docs)),
                "examples": users[:3],
            }
        )

    unknown_functions: dict[str, list[str]] = {}
    placeholder_outliers: list[dict] = []
    for row in rows:
        condition = str(row.get("PARENT_CONDITION") or "")
        name = str(row.get("VIRTUAL_PROFILE_NAME") or "").strip()
        if not condition or not name:
            continue
        for function in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(", condition):
            if function.lower() in KNOWN_FUNCTIONS:
                continue
            unknown_functions.setdefault(function, []).append(name)
        operators, values = _placeholder_pair_counts(condition)
        if operators != 1 or values != 1:
            placeholder_outliers.append(
                {"vp_name": name, "operator_count": operators, "value_count": values}
            )

    findings.sort(key=lambda item: (item["documented"], -item["vp_count"]))
    return {
        "client": client,
        "vps_scanned": len(rows),
        "constructs": findings,
        "undocumented": [item for item in findings if not item["documented"]],
        "unknown_functions": [
            {"function": function, "vp_count": len(names), "examples": names[:3]}
            for function, names in sorted(unknown_functions.items(), key=lambda pair: -len(pair[1]))
        ],
        "placeholder_rule_outliers": {
            "count": len(placeholder_outliers),
            "note": "validate_rule requires exactly one ${operator} and one ${value}; these production VPs would be rejected",
            "examples": placeholder_outliers[:10],
        },
    }


def render(report: dict) -> str:
    lines = [
        f"SYNTAX COVERAGE — {report['client']} ({report['vps_scanned']} production VPs)",
        "",
        "DOCUMENTED",
    ]
    for item in report["constructs"]:
        if item["documented"]:
            lines.append(f"  {item['construct']:<24} {item['vp_count']:>4} VPs")
    lines += ["", "UNDOCUMENTED  (agent has no written guidance for these)"]
    if not report["undocumented"]:
        lines.append("  none")
    for item in report["undocumented"]:
        lines.append(f"  {item['construct']:<24} {item['vp_count']:>4} VPs   {item['description']}")
        lines.append(f"  {'':<24}      e.g. {', '.join(item['examples'])}")
    if report["unknown_functions"]:
        lines += ["", "UNKNOWN FUNCTIONS  (not in the renderer/validator's known set)"]
        for item in report["unknown_functions"]:
            lines.append(f"  {item['function']:<24} {item['vp_count']:>4} VPs   e.g. {', '.join(item['examples'])}")
    outliers = report["placeholder_rule_outliers"]
    lines += ["", f"PLACEHOLDER-RULE OUTLIERS  {outliers['count']} VPs", f"  {outliers['note']}"]
    for item in outliers["examples"]:
        lines.append(f"    {item['vp_name']:<40} operator={item['operator_count']} value={item['value_count']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit production VP syntax against documented skills.")
    parser.add_argument("--client", default="omantel", choices=["omantel", "airtel"])
    parser.add_argument("--json", action="store_true", help="Emit the raw report as JSON")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when undocumented constructs exist")
    args = parser.parse_args()

    report = audit(args.client)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    if args.strict and report["undocumented"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
