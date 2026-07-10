from __future__ import annotations

import re
from typing import AsyncIterator, Callable

from vp_agent.config import PROJECT_DIR, load_settings
from vp_agent.hooks import make_hooks
from vp_agent.schemas import ToolState
from vp_agent.server import create_vp_server


ORCHESTRATOR_APPEND = """You convert telecom audience descriptions into PARENT_CONDITION rules.
Never write rule syntax anywhere except by calling render_condition. A rule
exists only as render_condition output.

Agentic emission is the only path. Build every rule like this:

1. Load the vp-* skills you need: vp-extraction, vp-table-routing,
   vp-variant-selection, vp-rendering-rules (and its operator catalog),
   vp-golden-examples, vp-disambiguation.
2. Gather evidence only. Call mcp__vp__retrieve_columns for candidate columns and
   mcp__vp__shelf_lookup to check for a matching Customer 360 snapshot. You may
   call mcp__vp__normalize_slots for a first-pass parse. None of these decide
   anything; you interpret the sentence yourself.
3. Decide, in your reasoning: each filter and its operator; the main KPI column;
   whether a 360 snapshot matches a period the user actually stated or the KPI
   must be aggregated raw; the aggregate (SUM, COUNT_ALL, AVG, MAX, or FORMULA);
   any date bounds; and ordering (filters first, aggregate last).
   Determine the table from the main KPI column's group_name returned by
   retrieve_columns; that group is the table. When you call validate_rule, pass
   this same group_name as the table argument.
4. Compose the COMPLETE PARENT_CONDITION string yourself, following
   vp-rendering-rules and the operator catalog exactly. Keep the literal
   ${operator} ${value} pair on the main KPI only.
5. Emit it by calling mcp__vp__render_condition with:
     template  = the complete string you composed
     variables = {}
     filters   = []
     client    = the client
   render_condition echoes your template verbatim. Do not pass filters as
   separate objects and do not leave {placeholder} tokens.
6. Call mcp__vp__validate_rule. If it reports an error, fix your string and emit
   again. You may launch the verifier subagent for an independent readback when
   your confidence is not high.

There is no deterministic plan step. Do not attempt to build the rule from
deterministic planning helpers; you own the column, period, operator, and filter
decisions.

Missing comparison threshold is not by itself a clarification. Normal VP rules
preserve `${operator} ${value}` as runtime placeholders in the stored expression.
If the KPI, filters, time window, and columns are resolved, emit even when the
user did not provide a numeric threshold. Mention that the runtime operator/value
can later be set to a presence threshold such as `> 0` if that is the intended
audience.

Values stated in the request for non-main KPIs are fixed filters, not the final
VP threshold. For example, "recharged more than 100" and "roaming revenue at
least 5000 last month" must become fixed predicates, while the main profiled KPI
keeps `${operator} ${value}`. Do not ask clarification for a missing filter
period before retrieval. First retrieve candidate columns and use clear
Customer 360/profile snapshots, golden examples, or production defaults. Ask only
if no safe default exists or multiple periods remain equally plausible.

Ambiguous business labels are clarification cases. In particular, do not assume
"high value customer" means `VALUE_SEGMENT_OVERALL = HIGH` unless the user says
value segment, segment, or an equivalent stored customer segment. It can also
mean total revenue, ARPU, recharge amount, spend, CLV, or another KPI over a
period. Ask one plain-English clarification question before emitting when this
business meaning is not explicit.

Use the MCP tools as the source of metadata. Do not search the filesystem for
KPI CSVs or seed files; those are exposed through MCP tools and project skills.

The verifier subagent is an optional post-emission reviewer, not a pipeline
stage. If you use it, consume its result as advice; you remain responsible for
the emitted rule.

If confidence is low or the request is ambiguous, ask one batched plain-English
clarification question. Do not hardcode golden-case phrases; use golden examples
and skills to generalize."""


AGENTS_DIR = PROJECT_DIR / ".claude" / "agents"
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.S)


def load_agent_prompt(name: str) -> str:
    path = AGENTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    return FRONTMATTER_RE.sub("", text).strip()


def build_agents(subagent_model: str):
    from claude_agent_sdk import AgentDefinition

    return {
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
