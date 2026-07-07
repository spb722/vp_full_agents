from __future__ import annotations

import argparse
import asyncio
import sys

from vp_agent.config import load_settings
from vp_agent.orchestrator import run_request
from vp_agent.schemas import ToolState
from vp_agent.trace import TraceRecorder


async def _run(args: argparse.Namespace) -> None:
    settings = load_settings()
    recorder = (
        TraceRecorder(
            mode="agent",
            client=args.client,
            request=args.request,
            orchestrator_model=settings.orchestrator_model,
            subagent_model=settings.subagent_model,
        )
        if args.trace_file or args.debug_sdk
        else None
    )

    state = ToolState()
    try:
        async for message in run_request(
            args.request,
            args.client,
            state=state,
            debug_sdk=args.debug_sdk,
            stderr_callback=recorder.add_stderr if recorder else None,
        ):
            if recorder:
                recorder.add_message(message)
            if hasattr(message, "result"):
                print(message.result)
            elif hasattr(message, "content"):
                for block in message.content:
                    text = getattr(block, "text", None)
                    if text:
                        print(text)
    except Exception as exc:
        if recorder:
            recorder.add_error(exc)
            _write_trace(args, recorder, state.trace)
        print(f"vp-agent: Claude Agent SDK failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    _write_trace(args, recorder, state.trace)


def _write_trace(args: argparse.Namespace, recorder: TraceRecorder | None, hook_trace: list[dict]) -> None:
    if not recorder or not args.trace_file:
        return
    target = recorder.write(args.trace_file, hook_trace)
    print(f"Trace written to {target}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a VP PARENT_CONDITION from an audience sentence.")
    parser.add_argument("request", help="Natural language audience request")
    parser.add_argument("--client", required=True, choices=["omantel", "airtel"], help="Client/site name")
    parser.add_argument("--debug-sdk", action="store_true", help="Enable Claude SDK debug logging and hook-event capture for agent mode.")
    parser.add_argument("--trace-file", help="Write a structured JSON trace to this path.")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
