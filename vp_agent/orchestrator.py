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

1. Load skills in two stages so you don't pay for bodies you won't use.

   ALWAYS load these two first, before reasoning:
   - vp-extraction (parse the request into intent/slots)
   - vp-rendering-rules (emission contract, operator-catalog pointer, and the
     date-bound / tenure-vs-window / snapshot rules that apply to almost every
     request)

   Then parse the request and gather evidence. Load a further skill ONLY when
   its trigger below applies to THIS request:
   - vp-disambiguation: the request has an ambiguity trigger — "high value",
     "premium", "valuable", "high spender", an uplift/downlift with an unclear
     base, "active" with no context, or a main KPI with no stated period and no
     safe default.
   - vp-variant-selection: the rule will use columns from more than one
     group/table (Variant 2), OR the request uses metric/uplift/downlift
     language (Variant 3).
   - vp-metrics-comparison: the request compares two explicit periods or asks
     for uplift, downlift, decline, percentage drop/change, growth, or ratio.
     Read its Rakesh Variant-3 reference before rendering or verifying.
   - vp-table-routing: after retrieval, more than one candidate table is
     plausible, or the KPI and its filters live in different groups.
   - vp-golden-examples: retrieval returns multiple plausible KPI families, OR
     you must choose between a Customer 360 snapshot and an event-table aggregate
     for a stated period.

   Reference files are Read only on demand, not with the skill:
   - references/operator-catalog.md: Read before emitting any NON-comparison
     predicate (IN LIST / NOT IN LIST, range, null, pattern). For a rule whose
     only operators are =, !=, <, <=, >, >=, the rendering-rules summary is
     enough; do not Read it.
   - references/golden-patterns.md: Read only if you remain unsure between
     candidate KPI families or snapshot-vs-raw after the vp-golden-examples body.
2. Gather evidence only. You may call mcp__vp__normalize_slots for a first-pass
   parse, then correct/complete its slots semantically. Call
   mcp__vp__retrieve_columns ONCE with the complete metric, filters, and time
   slots. Read its independent metric/filter candidate lists and each metric's
   time_window_support; do not run separate broad metric, filter, or date
   retrieval calls and do not retrieve configured group dates as KPI columns.
   The first page contains up to five candidates per role. Expand only one
   unresolved role with the same audit_id: page 2 for ranks 6-10, then page 3
   for ranks 11-15. Expansion requires a concrete trigger: no eligible
   candidate, missing qualifier/time/group coverage, routing/validation failure,
   or a near-duplicate set with no meaningful choice. Do not expand successful
   roles. A role is unresolved when every returned candidate adds an
   unrequested business qualifier such as prepaid/postpaid, incoming/outgoing,
   IDD, onnet/offnet, roaming, bundle/PAYG/free, or a fixed specialty period.
   In that situation expand the role; never settle for a narrower proxy while
   has-more evidence is available. Call mcp__vp__select_seed for template evidence when the request needs
   an aggregate, formula, guard, grouping, join, or non-trivial composition.
   It returns one complete proposed_selected_seed and up to three compact,
   structurally diverse alternatives. If you promote an alternative, fetch its
   one complete entry with the same seed audit_id and seed_id; never reconstruct
   omitted metadata. For Variant 3, call
   mcp__vp__retrieve_existing_vps separately for each period operand so you can
   reuse semantically matching helper VPs by exact name. None of these decide
   anything; you interpret the sentence yourself.
3. Decide, in your reasoning: each filter and its operator; the main KPI column;
   whether a 360 snapshot matches a period the user actually stated or the KPI
   must be aggregated raw; the aggregate (SUM, COUNT_ALL, AVG, MAX, or FORMULA);
   any date bounds; and ordering (filters first, aggregate last).
   Determine the table from the main KPI column's group_name returned by
   retrieve_columns; that group is the table. When you call validate_rule, pass
   this same group_name as the table argument.
4. Record your semantic decision with mcp__vp__record_resolution: extracted
   slots (including both comparison-period roles when applicable), selected
   columns, reused helper VP names, final seed ID, proposed seed ID, whether the
   final seed overrode the proposal, path, snapshot status, and any missing
   helper dependencies. This is an audit record of your decision, not a planning
   or decision tool.
5. Compose the COMPLETE PARENT_CONDITION string yourself, following
   vp-rendering-rules and the operator catalog exactly. Keep the literal
   ${operator} ${value} pair on the main KPI only.
6. Emit it by calling mcp__vp__render_condition with:
     template  = the complete string you composed
     variables = {}
     filters   = []
     client    = the client
   render_condition echoes your template verbatim. Do not pass filters as
   separate objects and do not leave {placeholder} tokens.
7. Call mcp__vp__validate_rule. If it reports an error, fix your string and emit
   again. You may launch the verifier subagent for an independent readback when
   your confidence is not high. For Variant 3, the verifier is required: give it
   the original request, extracted comparison roles, helper-VP evidence, seed
   evidence, and emitted rule; wait for its completed decision. If it requests a
   retry, revise, re-emit, and revalidate before finishing. Do not announce a
   background verifier and finish before its result.

Variant 3 is a dependency composition, not a Variant-1 formula. Extract both
periods with semantic roles (older/newer), distinguish percentage from absolute
metrics, retrieve/reuse helper VPs by exact name, and apply the reviewed
vp-metrics-comparison convention. The final metric references helper VP names
directly; do not wrap it in V{name}=f{...} and do not silently replace helpers
with raw Customer 360 columns. If a helper is missing, report it as a dependency
that must be provisioned before the final VP.

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
KPI CSVs, VP-description CSVs, or seed files; those are exposed through MCP
tools and project skills.

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
        skills=[
            "vp-rendering-rules",
            "vp-golden-examples",
            "vp-disambiguation",
            "vp-variant-selection",
            "vp-metrics-comparison",
        ],
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
            "mcp__vp__retrieve_existing_vps",
            "mcp__vp__normalize_slots",
            "mcp__vp__record_resolution",
            "mcp__vp__shelf_lookup",
            "mcp__vp__select_seed",
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
