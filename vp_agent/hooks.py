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

            if tool_name in {"mcp__vp__normalize_slots", "mcp__vp__retrieve_columns", "mcp__vp__build_condition_plan"}:
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
                            "permissionDecisionReason": "Complete the VP MCP pipeline directly before launching subagents: normalize_slots, retrieve_columns, route/select/plan, render_condition, validate_rule.",
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

            if tool_name == "mcp__vp__render_condition":
                state.render_seen = True
                structured = tool_response.get("structuredContent") if isinstance(tool_response, dict) else None
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
            if tool_name == "mcp__vp__build_condition_plan":
                state.plan_seen = True
        except Exception as exc:
            return _hook_warning("PostToolUse", f"VP post-tool hook failed without blocking execution: {type(exc).__name__}: {exc}")

        return {}

    async def stop(input_data: dict[str, Any], tool_use_id: str, context: Any):
        if state.plan_seen and not state.render_seen and state.stop_blocks < 1:
            state.stop_blocks += 1
            return {
                "decision": "block",
                "reason": (
                    "A renderable VP condition plan already exists. Do not stop for a missing comparison threshold: "
                    "normal VP expressions preserve runtime `${operator} ${value}` placeholders. "
                    "Call mcp__vp__render_condition with the planned render_input, then call mcp__vp__validate_rule."
                ),
            }
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
        "Stop": [HookMatcher(hooks=[stop])],
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
