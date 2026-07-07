from __future__ import annotations

import argparse
import asyncio

from vp_agent.orchestrator import build_options
from vp_agent.schemas import ToolState
from vp_agent.server import create_vp_server


async def smoke_imports() -> None:
    import claude_agent_sdk  # noqa: F401

    state = ToolState()
    options = build_options(state)
    assert options is not None
    server = create_vp_server()
    assert server is not None
    print("SDK imports/options/server: ok")


async def smoke_live(prompt: str) -> None:
    from claude_agent_sdk import ClaudeSDKClient, ResultMessage

    async with ClaudeSDKClient(options=build_options()) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                print(message.result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test Claude Agent SDK wiring.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run a live Claude request. Requires Claude Agent SDK authentication.",
    )
    args = parser.parse_args()

    if args.live:
        asyncio.run(smoke_live("Use the vp tools only if needed. Reply with 'vp sdk live smoke ok'."))
    else:
        asyncio.run(smoke_imports())


if __name__ == "__main__":
    main()

