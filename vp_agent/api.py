from __future__ import annotations

import re
import uuid
from typing import Any, Literal
import json

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from vp_agent.console_trace import console_trace_enabled, log_line
from vp_agent.config import load_settings
from vp_agent.observability import configure_langfuse, langfuse_status, observation, record_exception, update_observation
from vp_agent.orchestrator import run_request
from vp_agent.schemas import ToolState


PARENT_CONDITION_RE = re.compile(r"([A-Za-z0-9_{}$\"'=<> ().;+*/-]+?\$\{operator\}\s+\$\{value\}[A-Za-z0-9_{}$\"'=<> ().;+*/-]*)")


class VPBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: Literal["omantel", "airtel"]
    sentence: str = Field(min_length=1)
    request_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    user_id: str | None = Field(default=None, min_length=1)


class VPBuildResponse(BaseModel):
    ok: bool
    mode: Literal["agent"] = "agent"
    request_id: str
    session_id: str
    orchestrator_model: str
    subagent_model: str
    client: str
    sentence: str
    parent_condition: str | None = None
    selected_columns: list[str] = Field(default_factory=list)
    seed: str | None = None
    path: str | None = None
    snapshot: bool | None = None
    slots: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    raw_text: str | None = None
    failure_reason: str | None = None
    diagnostics: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


app = FastAPI(
    title="VP Agent API",
    description="Virtual Profile parent-condition builder backed by Claude Agent SDK.",
    version="0.1.0",
)
configure_langfuse()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/observability")
async def observability() -> dict[str, Any]:
    return {"langfuse": langfuse_status()}


@app.post("/vp/build", response_model=VPBuildResponse)
async def build_vp(request: VPBuildRequest) -> VPBuildResponse:
    request_id = _request_id(request)
    session_id = request.session_id or f"vp-{request.client}"
    user_id = request.user_id or "local-api"
    settings = load_settings()
    trace_name = f"vp-build:{request.client}:{request_id}"
    with observation(
        trace_name,
        **{
            "langfuse.user.id": user_id,
            "langfuse.session.id": session_id,
            "langfuse.tags": ["vp-agent", request.client],
            "input.value": request.sentence,
            "vp.request_id": request_id,
            "vp.orchestrator_model": settings.orchestrator_model,
            "vp.subagent_model": settings.subagent_model,
            "vp.client": request.client,
            "vp.mode": "agent",
            "vp.sentence": request.sentence,
        },
    ) as span:
        try:
            response = await _build_agentic(request, request_id=request_id, session_id=session_id)
        except Exception as exc:
            record_exception(span, exc)
            raise
        update_observation(
            span,
            **{
                "vp.ok": response.ok,
                "vp.parent_condition": response.parent_condition,
                "vp.selected_columns": response.selected_columns,
                "vp.warnings": response.warnings,
                "output.value": response.parent_condition or response.failure_reason,
            },
        )
        return response


async def _build_agentic(request: VPBuildRequest, *, request_id: str | None = None, session_id: str | None = None) -> VPBuildResponse:
    chunks: list[str] = []
    parent_condition: str | None = None
    state = ToolState(request_id=request_id or request.request_id, console_trace=console_trace_enabled())
    sdk_stderr: list[str] = []
    log_line(state, f"Request started: client={request.client}")
    try:
        async for message in run_request(request.sentence, request.client, state=state, stderr_callback=sdk_stderr.append):
            chunks.extend(_message_text(message))
            parent_condition = parent_condition or _message_parent_condition(message)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claude Agent SDK request failed: {type(exc).__name__}: {exc}") from exc

    raw_text = "\n".join(chunk for chunk in chunks if chunk).strip()
    parent_condition = parent_condition or _extract_parent_condition(raw_text)
    failure_reason = None if parent_condition else _missing_parent_condition_reason(state, raw_text, sdk_stderr)
    settings = load_settings()
    if parent_condition and not state.render_seen:
        log_line(state, f"Parent condition: {parent_condition}")
    log_line(state, f"Request completed: ok={str(bool(parent_condition)).lower()}")
    if failure_reason:
        log_line(state, f"Failure reason: {failure_reason}")
    return VPBuildResponse(
        ok=bool(parent_condition),
        mode="agent",
        request_id=request_id or _request_id(request),
        session_id=session_id or request.session_id or f"vp-{request.client}",
        orchestrator_model=settings.orchestrator_model,
        subagent_model=settings.subagent_model,
        client=request.client,
        sentence=request.sentence,
        parent_condition=parent_condition,
        raw_text=raw_text or None,
        failure_reason=failure_reason,
        diagnostics=None if parent_condition else _missing_parent_condition_diagnostics(state, raw_text, sdk_stderr),
        warnings=[] if parent_condition else _missing_parent_condition_warnings(failure_reason, state, sdk_stderr),
    )


def _message_text(message: object) -> list[str]:
    texts: list[str] = []
    result = getattr(message, "result", None)
    if isinstance(result, str) and result.strip():
        texts.append(result)

    content = getattr(message, "content", None)
    if content:
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text)
    return texts


def _message_parent_condition(message: object) -> str | None:
    condition = _extract_parent_condition(getattr(message, "result", None))
    if condition:
        return condition

    content = getattr(message, "content", None)
    if content:
        for block in content:
            condition = _extract_parent_condition(getattr(block, "content", None))
            if condition:
                return condition
            condition = _extract_parent_condition(getattr(block, "text", None))
            if condition:
                return condition
    return None


def _extract_parent_condition(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        condition = value.get("parent_condition")
        if isinstance(condition, str) and condition.strip():
            return condition.strip()
        for item in value.values():
            condition = _extract_parent_condition(item)
            if condition:
                return condition
        return None
    if isinstance(value, list):
        for item in value:
            condition = _extract_parent_condition(item)
            if condition:
                return condition
        return None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        condition = _extract_parent_condition(parsed)
        if condition:
            return condition

    json_line_match = re.search(r'"parent_condition"\s*:\s*"((?:\\.|[^"])*)"', text)
    if json_line_match:
        return json.loads(f'"{json_line_match.group(1)}"').strip()

    for line in text.splitlines():
        if "${operator}" in line and "${value}" in line:
            condition = line.strip().strip("`")
            condition = re.sub(r"^\s*(?:PARENT_CONDITION|parent_condition)\s*[:=]\s*", "", condition, flags=re.I)
            condition = condition.strip().strip('"')
            if _looks_like_parent_condition(condition):
                return condition
    match = PARENT_CONDITION_RE.search(text)
    if match and _looks_like_parent_condition(match.group(1)):
        return match.group(1).strip()
    return None


def _looks_like_parent_condition(condition: str) -> bool:
    if "`" in condition or condition.lstrip().startswith("-"):
        return False
    if "${operator}" not in condition or "${value}" not in condition:
        return False
    return bool(re.search(r"\b[A-Za-z][A-Za-z0-9_]*\s+\$\{operator\}\s+\$\{value\}", condition))


def _request_id(request: VPBuildRequest) -> str:
    return request.request_id or uuid.uuid4().hex[:10]


def _missing_parent_condition_reason(state: ToolState, raw_text: str, sdk_stderr: list[str]) -> str:
    if _sdk_hook_error_seen(sdk_stderr):
        return "Claude Agent SDK reported a hook callback/control-stream error before a parseable parent condition was returned."
    if not state.trace:
        return "The agent did not execute any VP tools before finishing."
    if not state.render_seen:
        tools = _tools_seen(state)
        if tools:
            return "The agent did not call mcp__vp__render_condition. Tools completed before render: " + ", ".join(tools) + "."
        return "The agent did not call mcp__vp__render_condition."
    if raw_text:
        return "mcp__vp__render_condition ran, but the final SDK response did not expose a parseable parent_condition."
    return "mcp__vp__render_condition ran, but the agent returned no text containing a parseable parent_condition."


def _missing_parent_condition_warnings(failure_reason: str | None, state: ToolState, sdk_stderr: list[str]) -> list[str]:
    warnings = [failure_reason or "Agent response did not contain a parseable parent condition."]
    if state.render_seen:
        warnings.append("Check the render_condition tool output in the trace; parsing may need to be extended for the SDK message shape.")
    else:
        warnings.append("The parent condition is only legal after mcp__vp__render_condition runs.")
    if _sdk_hook_error_seen(sdk_stderr):
        warnings.append("Restart FastAPI after hook changes and rerun with --debug-sdk/--trace-file if this repeats.")
    return warnings


def _missing_parent_condition_diagnostics(state: ToolState, raw_text: str, sdk_stderr: list[str]) -> dict[str, Any]:
    return {
        "render_condition_called": state.render_seen,
        "tools_seen": _tools_seen(state),
        "hook_event_count": len(state.trace),
        "sdk_hook_error_seen": _sdk_hook_error_seen(sdk_stderr),
        "sdk_stderr_tail": sdk_stderr[-5:],
        "raw_text_present": bool(raw_text),
    }


def _tools_seen(state: ToolState) -> list[str]:
    tools: list[str] = []
    for event in state.trace:
        tool = event.get("tool")
        if isinstance(tool, str) and tool and tool not in tools:
            tools.append(tool)
    return tools


def _sdk_hook_error_seen(stderr: list[str]) -> bool:
    return any("Error in hook callback" in line or "Stream closed" in line for line in stderr)


def _selected_columns(plan: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    main = plan.get("main_column") or {}
    if main.get("feature_name"):
        columns.append(main["feature_name"])
    for item in plan.get("filter_columns") or []:
        column = item.get("column") or item
        if isinstance(column, dict) and column.get("feature_name"):
            columns.append(column["feature_name"])
    render_input = plan.get("render_input") or {}
    for item in render_input.get("filters") or plan.get("filters") or []:
        column = item.get("col") if isinstance(item, dict) else None
        if column:
            columns.append(column)
    return list(dict.fromkeys(columns))


def _seed_id(plan: dict[str, Any]) -> str | None:
    seed = plan.get("seed")
    if isinstance(seed, dict):
        return seed.get("seed_id")
    return None


def _warnings(validation: dict[str, Any]) -> list[str]:
    warnings = validation.get("warnings") if isinstance(validation, dict) else None
    return list(warnings or [])
