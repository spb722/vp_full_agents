from __future__ import annotations

import re
from typing import AsyncIterator, Callable

from vp_agent.config import PROJECT_DIR, load_settings
from vp_agent.hooks import make_hooks
from vp_agent.schemas import ToolState
from vp_agent.server import create_vp_server


ORCHESTRATOR_APPEND = """You convert telecom audience descriptions into PARENT_CONDITION rules.
Never write rule syntax yourself. Rules exist only as render_condition output.

Mandatory API workflow:
1. Load only the vp-* skills needed for guidance.
2. Call mcp__vp__normalize_slots for the request.
3. Correct the slots semantically in your reasoning when needed, then call
   mcp__vp__retrieve_columns.
4. Call shelf_lookup/route_table/select_seed/build_condition_plan as needed to
   prepare render input.
5. Call mcp__vp__render_condition. Do not produce a final answer before this
   tool has run, unless you are asking a plain-English clarification.
6. Call mcp__vp__validate_rule, then return the validated PARENT_CONDITION plus
   selected columns, seed/template, operator/value interpretation, and warnings.

Missing comparison threshold is not by itself a clarification. Normal VP rules
preserve `${operator} ${value}` as runtime placeholders in the stored expression.
If the KPI, filters, time window, table, and seed are resolved, call
render_condition even when the user did not provide a numeric threshold. Mention
that the runtime operator/value can later be set to a presence threshold such as
`> 0` if that is the intended audience.

Use the MCP tools as the source of metadata and rendering truth. Do not search
the filesystem for KPI CSVs or seed files; those are already exposed through MCP
tools and project skills.

Subagents are optional reviewers, not blocking pipeline stages. Do not launch an
extractor/resolver subagent and then wait for it before calling the MCP pipeline.
If you use a subagent, consume its result only as advice and still complete the
mandatory MCP workflow yourself.

If confidence is low or the request is ambiguous, ask one batched plain-English
clarification question. Do not hardcode golden-case phrases in your reasoning;
use golden examples and skills as memory to generalize."""


AGENTS_DIR = PROJECT_DIR / ".claude" / "agents"
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.S)


def load_agent_prompt(name: str) -> str:
    path = AGENTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    return FRONTMATTER_RE.sub("", text).strip()


def build_agents(subagent_model: str):
    from claude_agent_sdk import AgentDefinition

    return {
        "extractor": AgentDefinition(
            description="Extracts telecom VP slots from a marketer sentence. Use first for every new audience request.",
            prompt=load_agent_prompt("extractor"),
            tools=["Skill", "Read"],
            model=subagent_model,
            skills=["vp-extraction"],
            maxTurns=4,
        ),
        "resolver": AgentDefinition(
            description="Resolves extracted VP slots to candidate columns and seed/template choices.",
            prompt=load_agent_prompt("resolver"),
            tools=[
                "Skill",
                "Read",
                "mcp__vp__normalize_slots",
                "mcp__vp__retrieve_columns",
                "mcp__vp__shelf_lookup",
                "mcp__vp__route_table",
                "mcp__vp__select_seed",
                "mcp__vp__build_condition_plan",
                "mcp__vp__episodic_lookup",
            ],
            model=subagent_model,
            skills=["vp-table-routing", "vp-variant-selection", "vp-golden-examples", "vp-disambiguation"],
            maxTurns=8,
        ),
        "verifier": AgentDefinition(
            description="Independently verifies a rendered VP rule against the original request and KPI metadata.",
            prompt=load_agent_prompt("verifier"),
            tools=["Skill", "Read", "mcp__vp__validate_rule"],
            model=subagent_model,
            skills=["vp-rendering-rules", "vp-golden-examples", "vp-disambiguation"],
            maxTurns=5,
        ),
    }


def build_options(
    state: ToolState | None = None,
    *,
    debug_sdk: bool = False,
    stderr_callback: Callable[[str], None] | None = None,
):
    from claude_agent_sdk import ClaudeAgentOptions

    settings = load_settings()
    tool_state = state or ToolState()
    extra_args = {"debug": "api,mcp,hooks"} if debug_sdk else {}
    return ClaudeAgentOptions(
        setting_sources=["project"],
        cwd=str(PROJECT_DIR),
        model=settings.orchestrator_model,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": ORCHESTRATOR_APPEND,
        },
        allowed_tools=[
            "Skill",
            "Read",
            "Agent",
            "mcp__vp__retrieve_columns",
            "mcp__vp__normalize_slots",
            "mcp__vp__shelf_lookup",
            "mcp__vp__route_table",
            "mcp__vp__select_seed",
            "mcp__vp__build_condition_plan",
            "mcp__vp__render_condition",
            "mcp__vp__validate_rule",
            "mcp__vp__episodic_lookup",
            "mcp__vp__queue_correction",
        ],
        disallowed_tools=["Bash"],
        skills="all",
        mcp_servers={"vp": create_vp_server()},
        agents=build_agents(settings.subagent_model),
        hooks=make_hooks(tool_state),
        max_turns=settings.max_turns,
        include_hook_events=debug_sdk,
        stderr=stderr_callback,
        extra_args=extra_args,
    )


async def run_request(
    request: str,
    client: str,
    *,
    state: ToolState | None = None,
    debug_sdk: bool = False,
    stderr_callback: Callable[[str], None] | None = None,
) -> AsyncIterator[object]:
    from claude_agent_sdk import ClaudeSDKClient

    prompt = f"""Client: {client}

Audience request:
{request}

Build the VP parent condition. Return the validated PARENT_CONDITION, selected
columns, seed/template, operator/value interpretation, and any clarification if
needed."""
    async with ClaudeSDKClient(options=build_options(state, debug_sdk=debug_sdk, stderr_callback=stderr_callback)) as sdk_client:
        await sdk_client.query(prompt)
        async for message in sdk_client.receive_response():
            yield message
