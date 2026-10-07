from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from vp_agent.console_trace import log_tool_output, log_tool_start
from vp_agent.schemas import ToolState
from vp_agent.tools.validate import seed_clause_regressions, seed_slot_coverage, validate_rule


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_hooks(state: ToolState):
    from claude_agent_sdk import HookMatcher

    async def pre_tool_use(input_data: dict[str, Any], tool_use_id: str, context: Any):
        try:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})
            log_tool_start(state, tool_name, tool_input)

            if tool_name in {
                "mcp__vp__normalize_slots",
                "mcp__vp__retrieve_columns",
                "mcp__vp__record_resolution",
            }:
                state.slots_seen = True

            if tool_name.startswith("mcp__vp__"):
                state.tools_called.add(tool_name)

            # The pipeline tools are only callable once ToolSearch has loaded
            # their schemas, and a model can get stuck on the loading half:
            # one run searched for normalize_slots six times, called it zero
            # times, then invented column names because retrieval never ran.
            if tool_name == "ToolSearch":
                requested = _selected_tool_names(str(tool_input.get("query") or ""))
                stalled = sorted((requested & state.tools_loaded) - state.tools_called)
                for name in stalled:
                    state.redundant_searches[name] = state.redundant_searches.get(name, 0) + 1
                # One repeat is a plausible retry after a failed load; beyond
                # that the model is looping rather than making progress.
                if stalled and max(state.redundant_searches[name] for name in stalled) > 1:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"{', '.join(stalled)} already loaded and never called. Stop "
                                "searching and call the tool directly with its arguments."
                            ),
                        }
                    }
                state.tools_loaded |= requested

            if tool_name == "mcp__vp__render_condition" and not state.slots_seen:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "render_condition cannot run before extracted slots are resolved",
                    }
                }

            if tool_name == "Agent":
                if not state.render_seen:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "Complete the VP MCP pipeline directly before launching subagents: normalize_slots or retrieve_columns, render_condition, validate_rule.",
                        }
                    }
                agent_name = tool_input.get("agent_name") or tool_input.get("subagent_type") or "unknown"
                state.subagent_counts[agent_name] = state.subagent_counts.get(agent_name, 0) + 1
                if state.subagent_counts[agent_name] > 3:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": f"subagent '{agent_name}' exceeded the per-request cap",
                        }
                    }

            state.trace.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "PreToolUse",
                    "tool": tool_name,
                    "tool_use_id": tool_use_id,
                    "input_hash": _digest(tool_input),
                }
            )
        except Exception as exc:
            return _hook_warning("PreToolUse", f"VP pre-tool hook failed without blocking execution: {type(exc).__name__}: {exc}")
        return {}

    async def post_tool_use(input_data: dict[str, Any], tool_use_id: str, context: Any):
        try:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})
            tool_response = input_data.get("tool_response", {})
            log_tool_output(state, tool_name, tool_response)
            structured = _extract_structured_content(tool_response)

            state.trace.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "PostToolUse",
                    "tool": tool_name,
                    "tool_use_id": tool_use_id,
                    "input_hash": _digest(tool_input),
                    "result_hash": _digest(tool_response),
                }
            )

            if tool_name == "mcp__vp__normalize_slots" and isinstance(structured, dict):
                state.normalized_slots = structured

            if tool_name == "mcp__vp__record_resolution" and isinstance(structured, dict):
                state.resolution = structured

            if tool_name == "mcp__vp__retrieve_columns" and isinstance(structured, dict):
                audit_id = structured.get("audit_id")
                if audit_id and str(audit_id) not in state.retrieval_audit_ids:
                    state.retrieval_audit_ids.append(str(audit_id))
                candidates: list[dict[str, Any]] = []
                metric_candidates = structured.get("metric_candidates")
                if isinstance(metric_candidates, list):
                    candidates.extend(item for item in metric_candidates if isinstance(item, dict))
                for role in structured.get("filter_candidates") or []:
                    if isinstance(role, dict) and isinstance(role.get("candidates"), list):
                        candidates.extend(item for item in role["candidates"] if isinstance(item, dict))
                if candidates:
                    known = {str(item.get("candidate_id")) for item in state.column_candidates}
                    for item in candidates:
                        if str(item.get("candidate_id")) not in known:
                            state.column_candidates.append(item)
                            known.add(str(item.get("candidate_id")))

                from vp_agent.tools.retrieve import unaddressed_request_terms

                submitted_slots = tool_input.get("slots")
                # The metric role gates key on `aggregate`: without it, nothing
                # stops a categorical or numeric column ranking first for "what
                # should I count?". One run omitted it and got LC_ACTION_TYPE at
                # rank 1 with no count column anywhere on the page; declaring
                # COUNT_ALL on the same slots returns L_AGG_CNT and L_AGG_MSISDN.
                if isinstance(submitted_slots, dict) and not (
                    submitted_slots.get("aggregate") or submitted_slots.get("formula")
                ):
                    return _hook_warning(
                        "PostToolUse",
                        "These slots declare no `aggregate`, so the metric candidates were ranked "
                        "without knowing what you intend to do with the column. Almost every VP "
                        "aggregates; set aggregate (COUNT_ALL, SUM, AVG, MAX or FORMULA) and "
                        "retrieve again before choosing a metric column.",
                    )
                if state.request and isinstance(submitted_slots, dict):
                    missing = unaddressed_request_terms(state.request, submitted_slots)
                    if missing:
                        state.unaddressed_terms = missing
                        return _hook_warning(
                            "PostToolUse",
                            "No role you sent to retrieve_columns mentions these words from the "
                            f"request: {', '.join(missing)}. If a word names an attribute, it is a "
                            "selector filter with operator 'runtime' and value null, not a "
                            "clarification — add the role and retrieve again. Only a word that names "
                            "no attribute at all is a clarification.",
                        )

            if tool_name == "mcp__vp__retrieve_existing_vps" and isinstance(structured, dict):
                candidates = structured.get("candidates")
                if isinstance(candidates, list):
                    known = {str(item.get("name")) for item in state.existing_vp_candidates}
                    for item in candidates:
                        if isinstance(item, dict) and str(item.get("name")) not in known:
                            state.existing_vp_candidates.append(item)
                            known.add(str(item.get("name")))

            if tool_name == "mcp__vp__select_seed" and isinstance(structured, dict):
                audit_id = structured.get("audit_id")
                if audit_id and str(audit_id) not in state.seed_audit_ids:
                    state.seed_audit_ids.append(str(audit_id))
                selected = structured.get("proposed_selected_seed") or structured.get("promoted_seed")
                if isinstance(selected, dict) and selected.get("seed_id"):
                    state.selected_seed = str(selected["seed_id"])

            if tool_name == "mcp__vp__validate_rule" and isinstance(structured, dict):
                state.validation = structured

            if tool_name == "Agent":
                verdict = _extract_verdict(tool_response)
                if verdict:
                    verdict["agent"] = tool_input.get("subagent_type") or tool_input.get("agent_name") or "unknown"
                    state.verifier_verdict = verdict

            if tool_name == "mcp__vp__render_condition":
                state.render_seen = True
                condition = None
                if isinstance(structured, dict) and structured.get("parent_condition"):
                    condition = structured["parent_condition"]
                if not condition:
                    condition = _extract_parent_condition(tool_response)
                if condition:
                    state.rendered_parent_condition = condition
                    validation = validate_rule(
                        condition,
                        request=state.request,
                        table=str(tool_input.get("table", "")),
                        client=state.client,
                    )
                    if not validation["ok"]:
                        return _hook_warning(
                            "PostToolUse",
                            "render_condition validation failed: " + json.dumps(validation["errors"], sort_keys=True),
                        )
                    # `ok` is `not errors`, so cue warnings were computed and
                    # thrown away here. Surfacing them at render time is the
                    # point of validating against the real request: a dropped
                    # negation is worth catching before the rule is presented,
                    # not only in the agent's own closing validate_rule call.
                    advisories = list(validation["warnings"])
                    # The seed catalog exists for exactly these shapes, and the
                    # prompt's "call select_seed for an aggregate ... or
                    # non-trivial composition" left enough room to skip it. One
                    # run rendered a counted lifecycle rule without ever asking,
                    # and so never saw the selector the family always carries.
                    if (
                        "mcp__vp__select_seed" not in state.tools_called
                        and re.search(r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\s*\(", condition)
                    ):
                        advisories.append(
                            {
                                "class": "seed_clause",
                                "detail": (
                                    "this rule aggregates but select_seed was never called, so no "
                                    "reviewed template was consulted. Call it with your columns and "
                                    "compare before finishing."
                                ),
                            }
                        )
                    if state.selected_seed:
                        from vp_agent.tools.seed import seed_template_by_id

                        template = seed_template_by_id(state.selected_seed)

                        advisories.extend(
                            {"class": "seed_clause", "seed_id": state.selected_seed, **item}
                            for item in (
                                seed_clause_regressions(condition, template)
                                + seed_slot_coverage(condition, template)
                            )
                        )
                    if advisories:
                        return _hook_warning(
                            "PostToolUse",
                            "render_condition produced a valid rule with warnings you must address "
                            "explicitly before finishing: "
                            + json.dumps(advisories, sort_keys=True, default=str),
                        )

            if tool_name != "mcp__vp__render_condition" and _contains_final_parent_condition(tool_response):
                return _hook_warning(
                    "PostToolUse",
                    "A non-render tool returned a parent_condition. Treat it as evidence only and call render_condition for the final rule.",
                )
        except Exception as exc:
            return _hook_warning("PostToolUse", f"VP post-tool hook failed without blocking execution: {type(exc).__name__}: {exc}")

        return {}

    async def stop(input_data: dict[str, Any], tool_use_id: str, context: Any):
        """Refuse to finish with neither a rendered rule nor a question.

        The PreToolUse gate stops render_condition running too early; nothing
        stopped the agent never reaching it at all. One run ended by writing the
        rule in prose — which invariant 1 forbids — and the caller received no
        condition and no clarification, the worst of both.
        """
        try:
            # `stop_hook_active` is the CLI's own re-entry flag; `stop_blocks`
            # keeps our intervention to exactly one so a genuine clarification
            # can still terminate the run.
            if input_data.get("stop_hook_active") or state.render_seen or state.stop_blocks:
                return {}
            state.stop_blocks += 1
            if not state.retrieval_audit_ids:
                # Asking before retrieving is the expensive mistake: one run
                # asked what "non-responder" meant while the client's own VP
                # names defined it twenty times over.
                reason = (
                    "You are finishing without having called mcp__vp__retrieve_columns, so you "
                    "have gathered no evidence at all. A question about what the data contains "
                    "or what a domain term means is answered by retrieve_columns and "
                    "retrieve_existing_vps, not by the user — client VP names encode the "
                    "client's vocabulary. Gather that evidence first; ask only if two readings "
                    "remain equally supported afterwards."
                )
            else:
                reason = (
                    "You are finishing without having called mcp__vp__render_condition, so no "
                    "legal rule exists — a rule written in prose does not count. Either finish "
                    "the pipeline (render_condition, then validate_rule), or finish with exactly "
                    "one line: `Clarification question: <one batched plain-English question>`."
                )
            return {"decision": "block", "reason": reason}
        except Exception:
            return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
        "Stop": [HookMatcher(hooks=[stop])],
    }


def _selected_tool_names(query: str) -> set[str]:
    """Tool names in a `select:a,b,c` ToolSearch query; empty for keyword searches."""
    prefix = "select:"
    if not query.lower().startswith(prefix):
        return set()
    return {name.strip() for name in query[len(prefix) :].split(",") if name.strip()}


VERDICT_RE = re.compile(r"\bVERDICT\s*:\s*(pass|retry|ask)\b[\s—\-:]*(.*)", re.I)


def _collect_text(value: Any, parts: list[str]) -> None:
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_text(item, parts)
    elif isinstance(value, list):
        for item in value:
            _collect_text(item, parts)


def _extract_verdict(tool_response: Any) -> dict[str, Any] | None:
    """Pull the verifier's decision out of the subagent result.

    Without this the review exists only inside the model's context: it never
    reaches the API response or a trace, so nobody can tell how often the
    verifier runs, what it says, or whether reworking it helped.
    """
    parts: list[str] = []
    _collect_text(tool_response, parts)
    for text in reversed(parts):
        match = VERDICT_RE.search(text)
        if match:
            return {
                "decision": match.group(1).lower(),
                "detail": " ".join(match.group(2).split())[:500],
            }
    return None


def _hook_warning(event_name: str, message: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": message}}


def _contains_final_parent_condition(value: Any) -> bool:
    if isinstance(value, dict):
        if isinstance(value.get("parent_condition"), str):
            return True
        return any(_contains_final_parent_condition(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_final_parent_condition(item) for item in value)
    return False


def _extract_parent_condition(value: Any) -> str | None:
    if isinstance(value, dict):
        condition = value.get("parent_condition")
        if isinstance(condition, str) and condition.strip():
            return condition.strip()
        return next((found for item in value.values() if (found := _extract_parent_condition(item))), None)
    if isinstance(value, list):
        return next((found for item in value if (found := _extract_parent_condition(item))), None)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return _extract_parent_condition(parsed)
    return None


def _extract_structured_content(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        structured = value.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for item in value.values():
            found = _extract_structured_content(item)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _extract_structured_content(item)
            if found:
                return found
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None
