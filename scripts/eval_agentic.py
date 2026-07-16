"""Evaluate the LIVE agentic path against a CSV of NL -> expected conditions.

Because agentic output varies run to run, this harness is how you know the
single path holds. It calls the real Claude Agent SDK, so it needs
ANTHROPIC_API_KEY and is slow / costs tokens.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    PYTHONPATH=. python scripts/eval_agentic.py \
        --client omantel \
        --path /path/to/bpagen_cases.csv \
        --repeat 3

CSV columns expected: "NL Input", "Expected Output".
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import re
from pathlib import Path

from vp_agent.orchestrator import run_request
from vp_agent.schemas import ToolState


def load_cases(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def normalize(condition: str) -> str:
    """Whitespace-insensitive comparison form. Keeps tokens/case intact."""
    if not condition:
        return ""
    text = condition.strip().strip("`").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*;\s*", ";", text)
    text = re.sub(r"\s*([()])\s*", r"\1", text)
    return text.lower()


def buckets(nl_input: str, expected: str) -> list[str]:
    tags = []
    e = expected.upper()
    n = nl_input.lower()
    if "IN LIST" in e:
        tags.append("in_list")
    if "COUNT_ALL(" in e:
        tags.append("count_all")
    if re.search(r"\bAON\b", e) or "on the network" in n or "age in the network" in n:
        tags.append("tenure")
    if re.search(r"_(M(TD|\d+)|W\d+|\d+D)\b", e) and "current" not in e.lower():
        tags.append("snapshot")
    if any(w in e for w in ("SUM(", "AVG(", "MAX(")):
        tags.append("aggregate")
    if any(w in e for w in ("OFFNET", "ONNET", "ROAMING", "IDD", "BUNDLE", "FREE", "FINANCE")):
        tags.append("direction_family")
    if "BETWEEN" in e or "IN RANGE" in e:
        tags.append("range")
    if "<> NULL" in e or "= NULL" in e:
        tags.append("null")
    if "* 100" in e and len(re.findall(r"\bM\d+[_A-Z]", e)) >= 2:
        tags.append("variant_3")
    return tags or ["other"]


async def build_one(sentence: str, client: str) -> str | None:
    state = ToolState()
    emitted: str | None = None
    async for _message in run_request(sentence, client, state=state):
        # rendered_parent_condition is set by the render hook when the agent emits.
        if state.rendered_parent_condition:
            emitted = state.rendered_parent_condition
    return emitted or state.rendered_parent_condition


async def main_async(args: argparse.Namespace) -> None:
    cases = load_cases(args.path)
    total = 0
    passed = 0
    bucket_tot: dict[str, int] = {}
    bucket_pass: dict[str, int] = {}
    unstable = 0

    for index, row in enumerate(cases, start=1):
        nl_input = row.get("NL Input") or row.get("nl_input") or ""
        expected = row.get("Expected Output") or row.get("expected_output") or ""
        if not nl_input:
            continue
        norm_expected = normalize(expected)
        tags = buckets(nl_input, expected)

        outputs: list[str] = []
        for _ in range(max(1, args.repeat)):
            try:
                emitted = await build_one(nl_input, args.client)
            except Exception as exc:
                emitted = f"<ERROR {type(exc).__name__}: {exc}>"
            outputs.append(emitted or "")

        norm_outputs = [normalize(output) for output in outputs]
        run_pass = [norm_output == norm_expected for norm_output in norm_outputs]
        case_pass = all(run_pass)
        if len(set(norm_outputs)) > 1:
            unstable += 1

        total += 1
        if case_pass:
            passed += 1
        for tag in tags:
            bucket_tot[tag] = bucket_tot.get(tag, 0) + 1
            bucket_pass[tag] = bucket_pass.get(tag, 0) + (1 if case_pass else 0)

        status = "PASS" if case_pass else "FAIL"
        print(f"[{index:03d}] {status}  ({','.join(tags)})")
        print(f"      NL       : {nl_input}")
        print(f"      expected : {expected}")
        for i, output in enumerate(outputs):
            mark = "=" if run_pass[i] else "x"
            print(f"      got[{i}] {mark}  : {output}")
        print()

    print("=" * 60)
    print(f"Cases: {total}   Passed: {passed}   Pass rate: {passed / max(total, 1):.1%}")
    if args.repeat > 1:
        print(f"Unstable across repeats: {unstable}/{total}")
    print("By bucket:")
    for tag in sorted(bucket_tot):
        print(f"  {tag:16s} {bucket_pass.get(tag, 0):3d}/{bucket_tot[tag]:<3d}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live agentic eval for VP conditions.")
    parser.add_argument("--client", default="omantel", choices=["omantel", "airtel"])
    parser.add_argument("--path", required=True, help="CSV with 'NL Input' and 'Expected Output'")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case (stability check)")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
