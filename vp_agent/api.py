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
    selected_vps: list[str] = Field(default_factory=list)
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    seed: str | None = None
    path: str | None = None
    snapshot: bool | None = None
    slots: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    raw_text: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
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
    return {"variant": load_settings().variant, "langfuse": langfuse_status()}


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
            "langfuse.tags": ["vp-agent", request.client, settings.variant],
            "input.value": request.sentence,
            "vp.request_id": request_id,
            "vp.orchestrator_model": settings.orchestrator_model,
            "vp.subagent_model": settings.subagent_model,
            "vp.client": request.client,
            "vp.mode": "agent",
            "vp.variant": settings.variant,
            "vp.sentence": request.sentence,
        },
    ) as span:
        try:
            stable_clarification = _stable_clarification_question(request.sentence)
            if stable_clarification:
                response = _clarification_response(
                    request,
                    request_id=request_id,
                    session_id=session_id,
                    question=stable_clarification,
                    source="stable_percentage_of_amount",
                )
            else:
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


def _stable_clarification_question(request: str) -> str | None:
    """Guard a small reviewed ambiguity primitive before expensive orchestration."""
    percentage_amount = re.search(
        r"\b(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?(?:recharge\s+)?amount\b",
        request,
        flags=re.I,
    )
    if not percentage_amount:
        return None
    explicit_comparison = re.search(
        r"\b(?:greater than|more than|less than|at least|at most|equal(?:s| to)?|above|below|exceeds?|under)\b",
        request,
        flags=re.I,
    )
    if explicit_comparison:
        return None
    percentage = percentage_amount.group(1)
    return (
        f"What should the calculated {percentage}% of the recharge amount be compared with or used for? "
        "For example, should it be compared with a specific threshold or with another amount?"
    )


def _clarification_response(
    request: VPBuildRequest,
    *,
    request_id: str,
    session_id: str,
    question: str,
    source: str,
) -> VPBuildResponse:
    settings = load_settings()
    trace_state = ToolState(request_id=request_id, console_trace=console_trace_enabled())
    log_line(trace_state, f"Stable clarification: client={request.client}, source={source}")
    return VPBuildResponse(
        ok=False,
        mode="agent",
        request_id=request_id,
        session_id=session_id,
        orchestrator_model=settings.orchestrator_model,
        subagent_model=settings.subagent_model,
        client=request.client,
        sentence=request.sentence,
        parent_condition=None,
        slots={"needs_clarification": True, "questions": [question]},
        raw_text=None,
        needs_clarification=True,
        clarification_question=question,
        failure_reason="Clarification needed before rendering.",
        diagnostics={"clarification_source": source},
        warnings=[question],
    )


async def _build_agentic(request: VPBuildRequest, *, request_id: str | None = None, session_id: str | None = None) -> VPBuildResponse:
    chunks: list[str] = []
    parent_condition: str | None = None
    state = ToolState(request_id=request_id or request.request_id, console_trace=console_trace_enabled())
    sdk_stderr: list[str] = []
    log_line(state, f"Request started: client={request.client}")
    try:
        async for message in run_request(request.sentence, request.client, state=state, stderr_callback=sdk_stderr.append):
            chunks.extend(_message_text(message))
            parent_condition = _message_render_parent_condition(message) or parent_condition
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claude Agent SDK request failed: {type(exc).__name__}: {exc}") from exc

    raw_text = "\n".join(chunk for chunk in chunks if chunk).strip()
    parent_condition = state.rendered_parent_condition or parent_condition or _extract_explicit_parent_condition(raw_text)
    clarification_question = None if parent_condition else _extract_clarification_question(raw_text, request.sentence)
    needs_clarification = bool(clarification_question)
    failure_reason = (
        None
        if parent_condition
        else "Clarification needed before rendering." if needs_clarification else _missing_parent_condition_reason(state, raw_text, sdk_stderr)
    )
    settings = load_settings()
    resolution = state.resolution or {}
    validation = state.validation
    selected_columns = list(resolution.get("selected_columns") or [])
    if not selected_columns and isinstance(validation, dict):
        selected_columns = list(validation.get("referenced_columns") or [])
    selected_vps = list(resolution.get("selected_vps") or [])
    if not selected_vps and parent_condition:
        selected_vps = [
            str(item.get("name"))
            for item in state.existing_vp_candidates
            if item.get("name") and str(item["name"]) in parent_condition
        ]
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
        selected_columns=list(dict.fromkeys(map(str, selected_columns))),
        selected_vps=list(dict.fromkeys(map(str, selected_vps))),
        dependencies=[item for item in resolution.get("dependencies") or [] if isinstance(item, dict)],
        seed=resolution.get("seed_id") or state.selected_seed,
        path=resolution.get("path"),
        snapshot=resolution.get("snapshot"),
        slots=resolution.get("slots") or state.normalized_slots,
        validation=validation,
        raw_text=raw_text or None,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        failure_reason=failure_reason,
        diagnostics=None if parent_condition else _missing_parent_condition_diagnostics(state, raw_text, sdk_stderr),
        warnings=(
            _validation_warning_strings(validation)
            if parent_condition
            else _missing_parent_condition_warnings(failure_reason, state, sdk_stderr, clarification_question)
        ),
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


def _message_render_parent_condition(message: object) -> str | None:
    return _extract_render_parent_condition(_to_plain_data(message))


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


def _extract_render_parent_condition(value: Any, in_render_tool: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        tool_name = str(
            value.get("tool_name")
            or value.get("toolName")
            or value.get("name")
            or value.get("tool")
            or value.get("commandName")
            or ""
        )
        in_render_tool = in_render_tool or "mcp__vp__render_condition" in tool_name or tool_name == "render_condition"
        if in_render_tool:
            condition = _extract_parent_condition_from_key(value)
            if condition:
                return condition
        for item in value.values():
            condition = _extract_render_parent_condition(item, in_render_tool)
            if condition:
                return condition
        return None
    if isinstance(value, list):
        for item in value:
            condition = _extract_render_parent_condition(item, in_render_tool)
            if condition:
                return condition
        return None
    if in_render_tool and isinstance(value, str):
        return _extract_parent_condition(value)
    return None


def _extract_parent_condition_from_key(value: Any) -> str | None:
    if isinstance(value, dict):
        condition = value.get("parent_condition")
        if isinstance(condition, str) and condition.strip():
            return condition.strip()
        for item in value.values():
            condition = _extract_parent_condition_from_key(item)
            if condition:
                return condition
    elif isinstance(value, list):
        for item in value:
            condition = _extract_parent_condition_from_key(item)
            if condition:
                return condition
    elif isinstance(value, str):
        try:
            parsed = json.loads(value.strip())
        except json.JSONDecodeError:
            return None
        return _extract_parent_condition_from_key(parsed)
    return None


def _extract_explicit_parent_condition(text: str) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        if re.match(r"^\s*(?:\*\*)?\s*(?:PARENT_CONDITION|parent_condition)\s*(?:\*\*)?\s*[:=]", line, flags=re.I):
            condition = re.sub(r"^\s*(?:\*\*)?\s*(?:PARENT_CONDITION|parent_condition)\s*(?:\*\*)?\s*[:=]\s*", "", line, flags=re.I)
            condition = condition.strip().strip("`").strip('"')
            if _looks_like_parent_condition(condition):
                return condition
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.search(r"\bPARENT_CONDITION\b", line, flags=re.I):
            for candidate in lines[index + 1 : index + 5]:
                condition = candidate.strip().strip("`").strip('"')
                if _looks_like_parent_condition(condition):
                    return condition
    return None


def _looks_like_parent_condition(condition: str) -> bool:
    if "`" in condition or condition.lstrip().startswith("-"):
        return False
    if "${operator}" not in condition or "${value}" not in condition:
        return False
    return bool(
        re.search(
            r"(?:\b[A-Za-z][A-Za-z0-9_]*|\b(?:SUM|COUNT_ALL|AVG|MAX|MIN)\([^)]*\))\s+\$\{operator\}\s+\$\{value\}",
            condition,
            flags=re.I,
        )
    )


def _extract_clarification_question(text: str, request: str = "") -> str | None:
    if re.search(r"\bhigh[- ]?value|valuable customer|premium customer|high spender\b", request, flags=re.I):
        return (
            "When you say high value customers, should that mean customers in an existing High Value segment, "
            "or customers whose revenue, spend, recharge amount, ARPU, or CLV crosses a threshold? "
            "If it is threshold-based, please specify which measure and whether the last 30 days should apply to it."
        )
    percentage_amount = re.search(
        r"\b(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?(?:recharge\s+)?amount\b",
        request,
        flags=re.I,
    )
    if percentage_amount:
        percentage = percentage_amount.group(1)
        return (
            f"What should the calculated {percentage}% of the recharge amount be compared with or used for? "
            "For example, should it be compared with a specific threshold or with another amount?"
        )
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    explicit_question = _extract_labelled_clarification_question(lines)
    if explicit_question:
        return explicit_question
    if not re.search(r"clarification|clarify|ambiguous|which|what do you mean", text, flags=re.I):
        return None
    search_lines = lines[-30:]
    question_lines = [
        _clean_clarification_line(line)
        for line in search_lines
        if "?" in line and not _line_has_internal_terms(line)
    ]
    question_lines = [line for line in question_lines if line]
    if question_lines:
        return _dedupe_sentences(" ".join(question_lines))[:1000]
    for index, line in enumerate(search_lines):
        if re.search(r"clarification needed|clarification question|please clarify", line, flags=re.I):
            public_lines = [
                _clean_clarification_line(item)
                for item in search_lines[index : index + 6]
                if not _line_has_internal_terms(item)
            ]
            public_lines = [line for line in public_lines if line]
            if public_lines:
                return _dedupe_sentences(" ".join(public_lines))[:1000]
    return None


def _extract_labelled_clarification_question(lines: list[str]) -> str | None:
    for index in range(len(lines) - 1, -1, -1):
        line = _clean_clarification_line(lines[index])
        if not re.match(r"^(?:Question|Clarification question)\s*:", line, flags=re.I):
            continue
        candidate = re.sub(r"^(?:Question|Clarification question)\s*:\s*", "", line, flags=re.I).strip()
        if "?" not in candidate:
            following = " ".join(_clean_clarification_line(item) for item in lines[index + 1 : index + 4])
            candidate = f"{candidate} {following}".strip()
        if "?" in candidate and not _line_has_internal_terms(candidate):
            return _dedupe_sentences(candidate)[:1000]
    return None


def _line_has_internal_terms(line: str) -> bool:
    return bool(
        re.search(
            r"\b[A-Z][A-Z0-9_]{2,}\b|PARENT_CONDITION|seed|template|column|table|render|omantel",
            line,
        )
    )


def _clean_clarification_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = line.strip("-* ")
    line = re.sub(r"^\d+\.\s*", "", line)
    line = line.replace("`", "")
    return line.strip()


def _dedupe_sentences(text: str) -> str:
    parts = re.split(r"(?<=[?.!])\s+", text.strip())
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            result.append(part)
    return " ".join(result)


def _to_plain_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(item) for item in value]
    data: dict[str, Any] = {"__class__": type(value).__name__}
    for attr in (
        "type",
        "name",
        "tool_name",
        "toolName",
        "commandName",
        "content",
        "text",
        "result",
        "input",
        "output",
        "tool_response",
        "structuredContent",
    ):
        if hasattr(value, attr):
            data[attr] = _to_plain_data(getattr(value, attr))
    if len(data) == 1:
        return str(value)
    return data


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


def _missing_parent_condition_warnings(
    failure_reason: str | None,
    state: ToolState,
    sdk_stderr: list[str],
    clarification_question: str | None = None,
) -> list[str]:
    warnings = [failure_reason or "Agent response did not contain a parseable parent condition."]
    if clarification_question:
        warnings.append(clarification_question)
        return warnings
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


def _validation_warning_strings(validation: dict[str, Any] | None) -> list[str]:
    if not isinstance(validation, dict):
        return []
    result: list[str] = []
    for warning in validation.get("warnings") or []:
        if isinstance(warning, str):
            result.append(warning)
        else:
            result.append(json.dumps(warning, sort_keys=True, default=str))
    return result
