from __future__ import annotations

import json
import os
from typing import Any

from vp_agent.schemas import ToolState


def console_trace_enabled() -> bool:
    return os.getenv("VP_CONSOLE_TRACE", "").lower() in {"1", "true", "yes", "on"}


def log_line(state: ToolState, message: str) -> None:
    if not state.console_trace:
        return
    request_id = state.request_id or "unknown"
    print(f"[vp-agent {request_id}] {message}", flush=True)


def log_tool_start(state: ToolState, tool_name: str, tool_input: dict[str, Any]) -> None:
    if tool_name == "Agent":
        agent_name = tool_input.get("agent_name") or tool_input.get("subagent_type") or "unknown"
        log_line(state, f"Agent: {agent_name}")
        return
    if tool_name:
        log_line(state, f"Tool: {tool_name}")


def log_tool_output(state: ToolState, tool_name: str, tool_response: Any) -> None:
    summary = summarize_tool_output(tool_name, tool_response)
    if summary:
        log_line(state, summary)


def summarize_tool_output(tool_name: str, tool_response: Any) -> str | None:
    structured = _structured_content(tool_response)
    if not isinstance(structured, dict):
        return None

    if tool_name == "mcp__vp__normalize_slots":
        return _extractor_summary(structured)
    if tool_name == "mcp__vp__route_table":
        table = structured.get("table")
        return f"Tool output: table={table}" if table else None
    if tool_name == "mcp__vp__retrieve_columns":
        return _retrieve_summary(structured)
    if tool_name == "mcp__vp__retrieve_existing_vps":
        candidates = structured.get("candidates") or []
        top = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        return f"Tool output: existing_vp={top.get('name')}" if top.get("name") else None
    if tool_name == "mcp__vp__select_seed":
        return _seed_summary(structured)
    if tool_name == "mcp__vp__record_resolution":
        comparison = (structured.get("slots") or {}).get("comparison") or {}
        text = _kv_summary(
            {
                "path": structured.get("path"),
                "seed": structured.get("seed_id"),
                "older": comparison.get("older_period"),
                "newer": comparison.get("newer_period"),
            }
        )
        return f"Agent resolution: {text}" if text else None
    if tool_name == "mcp__vp__build_condition_plan":
        return _resolver_summary(structured)
    if tool_name == "mcp__vp__render_condition":
        condition = structured.get("parent_condition")
        return f"Parent condition: {condition}" if condition else None
    if tool_name == "mcp__vp__validate_rule":
        ok = structured.get("ok")
        warnings = structured.get("warnings") or []
        return f"Verifier decision: ok={str(bool(ok)).lower()}, warnings={len(warnings)}"
    return None


def _extractor_summary(data: dict[str, Any]) -> str | None:
    fields = {
        "domain": data.get("domain"),
        "kpi": data.get("kpi_phrase"),
        "time": data.get("time_token"),
    }
    text = _kv_summary(fields)
    return f"Extractor decision: {text}" if text else None


def _retrieve_summary(data: dict[str, Any]) -> str | None:
    candidates = data.get("metric_candidates") or data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    top = candidates[0]
    if not isinstance(top, dict):
        return None
    feature = top.get("feature_name") or top.get("id")
    score = top.get("score")
    if feature and isinstance(score, (int, float)):
        return f"Tool output: top={feature}, score={score:.3f}"
    return f"Tool output: top={feature}" if feature else None


def _seed_summary(data: dict[str, Any]) -> str | None:
    selected = data.get("proposed_selected_seed") or data.get("promoted_seed") or data.get("selected") or data.get("seed") or data
    if not isinstance(selected, dict):
        return None
    seed = selected.get("seed_id")
    confidence = selected.get("confidence")
    if seed and isinstance(confidence, (int, float)):
        return f"Tool output: seed={seed}, confidence={confidence:.3f}"
    return f"Tool output: seed={seed}" if seed else None


def _resolver_summary(data: dict[str, Any]) -> str | None:
    render_input = data.get("render_input") if isinstance(data.get("render_input"), dict) else {}
    variables = render_input.get("variables") if isinstance(render_input.get("variables"), dict) else {}
    main_column = variables.get("main_column") or data.get("main_column")
    table = data.get("table")
    seed = render_input.get("seed_id") or data.get("seed_id")
    text = _kv_summary({"table": table, "seed": seed, "main_column": main_column})
    return f"Resolver decision: {text}" if text else None


def _structured_content(tool_response: Any) -> Any:
    if isinstance(tool_response, dict):
        if "structuredContent" in tool_response:
            return tool_response["structuredContent"]
        content = tool_response.get("content")
        if isinstance(content, list):
            parsed = _parse_content_text(content)
            if parsed is not None:
                return parsed
        return tool_response
    return None


def _parse_content_text(content: list[Any]) -> Any:
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                continue
    return None


def _kv_summary(fields: dict[str, Any]) -> str:
    parts = [f"{key}={value}" for key, value in fields.items() if value not in (None, "", [], {})]
    return ", ".join(parts)
