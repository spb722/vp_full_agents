from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from vp_agent.console_trace import log_tool_output, log_tool_start
from vp_agent.schemas import ToolState
from vp_agent.tools.validate import validate_rule


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
                        request=str(tool_input.get("request", "")),
                        table=str(tool_input.get("table", "")),
                    )
                    if not validation["ok"]:
                        return _hook_warning(
                            "PostToolUse",
                            "render_condition validation failed: " + json.dumps(validation["errors"], sort_keys=True),
                        )

            if tool_name != "mcp__vp__render_condition" and _contains_final_parent_condition(tool_response):
                return _hook_warning(
                    "PostToolUse",
                    "A non-render tool returned a parent_condition. Treat it as evidence only and call render_condition for the final rule.",
                )
        except Exception as exc:
            return _hook_warning("PostToolUse", f"VP post-tool hook failed without blocking execution: {type(exc).__name__}: {exc}")

        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
    }


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
