from __future__ import annotations

import json
import traceback
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class TraceRecorder:
    def __init__(
        self,
        *,
        mode: str,
        client: str,
        request: str,
        orchestrator_model: str | None = None,
        subagent_model: str | None = None,
    ) -> None:
        self.trace_id = str(uuid.uuid4())
        self.started_at = utc_now()
        self.mode = mode
        self.client = client
        self.request = request
        self.orchestrator_model = orchestrator_model
        self.subagent_model = subagent_model
        self.events: list[dict[str, Any]] = []
        self.stderr: list[str] = []
        self.final_error: dict[str, Any] | None = None

    def add_event(self, event: str, **fields: Any) -> None:
        self.events.append({"ts": utc_now(), "event": event, **to_jsonable(fields)})

    def add_stderr(self, line: str) -> None:
        self.stderr.append(line.rstrip("\n"))

    def add_message(self, message: object) -> None:
        self.add_event("SDKMessage", **summarize_message(message))

    def add_error(self, exc: BaseException) -> None:
        self.final_error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exception(type(exc), exc, exc.__traceback__),
        }
        self.add_event("Error", **self.final_error)

    def payload(self, hook_trace: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "mode": self.mode,
            "client": self.client,
            "request": self.request,
            "orchestrator_model": self.orchestrator_model,
            "subagent_model": self.subagent_model,
            "events": self.events,
            "hook_trace": hook_trace or [],
            "stderr": self.stderr,
            "final_error": self.final_error,
        }

    def write(self, path: str | Path, hook_trace: list[dict[str, Any]] | None = None) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.payload(hook_trace), indent=2, sort_keys=True), encoding="utf-8")
        return target


def summarize_message(message: object) -> dict[str, Any]:
    message_type = type(message).__name__
    summary: dict[str, Any] = {"message_type": message_type}

    for attr in (
        "subtype",
        "model",
        "session_id",
        "uuid",
        "stop_reason",
        "is_error",
        "api_error_status",
        "errors",
        "permission_denials",
        "total_cost_usd",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
    ):
        if hasattr(message, attr):
            value = getattr(message, attr)
            if value not in (None, [], {}):
                summary[attr] = to_jsonable(value)

    result = getattr(message, "result", None)
    if isinstance(result, str) and result:
        summary["result_preview"] = result[:1000]

    data = getattr(message, "data", None)
    if isinstance(data, dict):
        summary["data"] = to_jsonable(data)

    content = getattr(message, "content", None)
    if content:
        summary["content"] = [summarize_block(block) for block in content]

    return summary


def summarize_block(block: object) -> dict[str, Any]:
    block_type = type(block).__name__
    summary: dict[str, Any] = {"block_type": block_type}
    for attr in ("id", "name", "tool_use_id", "is_error"):
        if hasattr(block, attr):
            value = getattr(block, attr)
            if value is not None:
                summary[attr] = to_jsonable(value)

    text = getattr(block, "text", None)
    if isinstance(text, str):
        summary["text_preview"] = text[:1200]

    thinking = getattr(block, "thinking", None)
    if isinstance(thinking, str):
        summary["thinking_preview"] = thinking[:1200]

    block_input = getattr(block, "input", None)
    if block_input is not None:
        summary["input"] = to_jsonable(block_input)

    content = getattr(block, "content", None)
    if content is not None:
        summary["content"] = to_jsonable(content)

    return summary
