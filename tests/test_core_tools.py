from __future__ import annotations

import json
import os
import subprocess
import sys
import asyncio

from vp_agent.config import PROJECT_DIR
from vp_agent.domain_config import date_column_for_group
from vp_agent.golden import DEFAULT_GOLDEN_PATH, condition_column, find_360_snapshot_cases, has_date_condition, load_golden_cases
from vp_agent.tools.normalize import normalize_slots
from vp_agent.tools.plan import build_condition_plan, build_parent_condition, is_customer_360_snapshot
from vp_agent.tools.render import render_condition
from vp_agent.tools.retrieve import (
    build_retrieval_audit,
    compact_retrieval_page,
    retrieve_columns,
    serialize_retrieval_audit,
)
from vp_agent.tools.retrieve_vps import retrieve_existing_vps
from vp_agent.tools.router import route_table
from vp_agent.tools.seed import build_seed_audit, select_seed, serialize_seed_audit
from vp_agent.tools.shelf import shelf_lookup
from vp_agent.tools.validate import validate_rule


def test_default_models_are_sonnet5_and_haiku45(monkeypatch):
    from vp_agent.config import load_settings

    monkeypatch.delenv("VP_ORCHESTRATOR_MODEL", raising=False)
    monkeypatch.delenv("VP_SUBAGENT_MODEL", raising=False)

    settings = load_settings()

    assert settings.orchestrator_model == "claude-sonnet-5"
    assert settings.subagent_model == "claude-haiku-4-5-20251001"


def test_orchestrator_uses_agentic_emission_and_disallows_bash():
    from vp_agent.orchestrator import ORCHESTRATOR_APPEND, build_options

    assert "Agentic emission is the only path" in ORCHESTRATOR_APPEND
    assert "Load skills in two stages" in ORCHESTRATOR_APPEND
    assert "ALWAYS load these two first" in ORCHESTRATOR_APPEND
    assert "Load a further skill ONLY when" in ORCHESTRATOR_APPEND
    assert "Reference files are Read only on demand" in ORCHESTRATOR_APPEND
    assert "references/operator-catalog.md: Read before emitting any NON-comparison" in ORCHESTRATOR_APPEND
    assert "There is no deterministic plan step" in ORCHESTRATOR_APPEND
    assert "template  = the complete string you composed" in ORCHESTRATOR_APPEND
    assert "main KPI column's group_name" in ORCHESTRATOR_APPEND
    assert "same group_name as the table argument" in ORCHESTRATOR_APPEND
    assert "mcp__vp__render_condition" in ORCHESTRATOR_APPEND
    assert "Mandatory API workflow" not in ORCHESTRATOR_APPEND
    assert "planned render_input" not in ORCHESTRATOR_APPEND
    assert "Missing comparison threshold is not by itself a clarification" in ORCHESTRATOR_APPEND
    assert "Values stated in the request for non-main KPIs are fixed filters" in ORCHESTRATOR_APPEND
    assert "Do not ask clarification for a missing filter" in ORCHESTRATOR_APPEND
    assert '"high value customer"' in ORCHESTRATOR_APPEND
    assert "Do not search the filesystem" in ORCHESTRATOR_APPEND

    options = build_options()

    assert "Bash" in options.disallowed_tools
    for tool in (
        "mcp__vp__build_condition_plan",
        "mcp__vp__route_table",
    ):
        assert tool not in options.allowed_tools
    for tool in (
        "mcp__vp__retrieve_columns",
        "mcp__vp__retrieve_existing_vps",
        "mcp__vp__shelf_lookup",
        "mcp__vp__select_seed",
        "mcp__vp__record_resolution",
        "mcp__vp__render_condition",
        "mcp__vp__validate_rule",
    ):
        assert tool in options.allowed_tools


def test_api_request_schema_is_agent_only():
    import pytest
    from pydantic import ValidationError

    from vp_agent.api import VPBuildRequest

    request = VPBuildRequest(
        client="omantel",
        sentence="Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days",
        request_id="demo-001",
        session_id="vp-demo",
    )

    assert request.client == "omantel"
    assert request.sentence
    assert request.request_id == "demo-001"
    assert request.session_id == "vp-demo"
    assert not hasattr(request, "mode")
    with pytest.raises(ValidationError):
        VPBuildRequest(
            client="omantel",
            sentence="Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days",
            mode="deterministic",
        )


def test_langfuse_env_setup_from_credentials(monkeypatch):
    from vp_agent.observability import _configure_otel_env

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    _configure_otel_env()

    assert (
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    )
    assert os.environ["OTEL_EXPORTER_OTLP_TRACES_HEADERS"].startswith("Authorization=Basic ")


def test_langfuse_tags_are_sent_as_string_array():
    from types import SimpleNamespace

    from vp_agent.observability import _set_attribute

    calls = []
    span = SimpleNamespace(set_attribute=lambda key, value: calls.append((key, value)))

    _set_attribute(span, "langfuse.tags", ["vp-agent", "omantel"])

    assert calls == [("langfuse.tags", ["vp-agent", "omantel"])]


def test_console_trace_summarizes_tool_outputs():
    from vp_agent.console_trace import summarize_tool_output

    assert (
        summarize_tool_output(
            "mcp__vp__normalize_slots",
            {
                "structuredContent": {
                    "domain": "usage",
                    "kpi_phrase": "outgoing revenue",
                    "time_token": "4W",
                }
            },
        )
        == "Extractor decision: domain=usage, kpi=outgoing revenue, time=4W"
    )
    assert (
        summarize_tool_output(
            "mcp__vp__build_condition_plan",
            {
                "structuredContent": {
                    "table": "Event",
                    "render_input": {
                        "seed_id": "S161_raw_kpi_no_time",
                        "variables": {"main_column": "AVERAGE_WEEKLY_REVENUE_FROM_OUTGOING_VOICE_CALLS_W4"},
                    },
                }
            },
        )
        == "Resolver decision: table=Event, seed=S161_raw_kpi_no_time, main_column=AVERAGE_WEEKLY_REVENUE_FROM_OUTGOING_VOICE_CALLS_W4"
    )
    assert (
        summarize_tool_output(
            "mcp__vp__validate_rule",
            {"structuredContent": {"ok": True, "warnings": []}},
        )
        == "Verifier decision: ok=true, warnings=0"
    )


def test_api_request_id_generation_and_session_default():
    from vp_agent.api import VPBuildRequest, _request_id

    request = VPBuildRequest(client="omantel", sentence="Data revenue for smartphone users")

    assert len(_request_id(request)) == 10


def test_api_returns_structured_agent_resolution(monkeypatch):
    import vp_agent.api as api_module
    from vp_agent.schemas import ToolState

    condition = "(M2_Data_Revenue - M1_Data_Revenue) / M1_Data_Revenue * 100 ${operator} ${value}"
    state = ToolState(
        render_seen=True,
        rendered_parent_condition=condition,
        resolution={
            "slots": {
                "kpi_phrase": "data revenue",
                "aggregate": "FORMULA",
                "comparison": {"older_period": "M2", "newer_period": "M1"},
            },
            "selected_columns": ["COMMON_Data_Revenue"],
            "selected_vps": ["M2_Data_Revenue", "M1_Data_Revenue"],
            "seed_id": "S77_percentage_drop",
            "path": "variant_3",
            "snapshot": False,
            "dependencies": [],
        },
        selected_seed="S77_percentage_drop",
        validation={"ok": True, "warnings": [], "referenced_columns": []},
    )

    async def fake_run_request(*args, **kwargs):
        if False:
            yield None

    monkeypatch.setattr(api_module, "ToolState", lambda **kwargs: state)
    monkeypatch.setattr(api_module, "run_request", fake_run_request)

    response = asyncio.run(
        api_module._build_agentic(
            api_module.VPBuildRequest(
                client="omantel",
                sentence="Data revenue decline comparing 2 months ago vs last month",
            )
        )
    )

    assert response.parent_condition == condition
    assert response.selected_columns == ["COMMON_Data_Revenue"]
    assert response.selected_vps == ["M2_Data_Revenue", "M1_Data_Revenue"]
    assert response.seed == "S77_percentage_drop"
    assert response.path == "variant_3"
    assert response.validation["ok"] is True


def test_api_extracts_parent_condition_from_agent_text():
    from vp_agent.api import _extract_explicit_parent_condition

    text = "Done.\nPARENT_CONDITION: CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}"

    assert _extract_explicit_parent_condition(text) == "CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}"


def test_api_extracts_aggregate_parent_condition_from_agent_text():
    from vp_agent.api import _extract_explicit_parent_condition

    condition = (
        'Profile_Cdr_Handset_Type = "SP" AND Profile_Line_Type = "PREPAID" '
        "AND COMMON_Event_Date >= CurrentTime-7DAYS "
        "AND SUM(COMMON_OG_Local_Offnet_Sms_Revenue) ${operator} ${value}"
    )
    text = f"**PARENT_CONDITION:**\n```\n{condition}\n```"

    assert _extract_explicit_parent_condition(text) == condition


def test_api_does_not_extract_placeholder_instruction_as_condition():
    from vp_agent.api import _extract_parent_condition

    text = "- Preserve `${operator} ${value}` in the stored VP expression."

    assert _extract_parent_condition(text) is None


def test_api_missing_condition_reason_when_render_not_called():
    from vp_agent.api import _missing_parent_condition_diagnostics, _missing_parent_condition_reason, _missing_parent_condition_warnings
    from vp_agent.schemas import ToolState

    state = ToolState()
    state.trace.append({"event": "PreToolUse", "tool": "mcp__vp__select_seed"})

    reason = _missing_parent_condition_reason(state, "agent text", [])

    assert "did not call mcp__vp__render_condition" in reason
    assert "mcp__vp__select_seed" in reason
    assert _missing_parent_condition_warnings(reason, state, [])[0] == reason
    diagnostics = _missing_parent_condition_diagnostics(state, "agent text", [])
    assert diagnostics["render_condition_called"] is False
    assert diagnostics["tools_seen"] == ["mcp__vp__select_seed"]


def test_api_missing_condition_reason_for_sdk_hook_error():
    from vp_agent.api import _missing_parent_condition_diagnostics, _missing_parent_condition_reason
    from vp_agent.schemas import ToolState

    stderr = ["Error in hook callback hook_0: error: Stream closed"]

    assert "hook callback" in _missing_parent_condition_reason(ToolState(), "", stderr)
    assert _missing_parent_condition_diagnostics(ToolState(), "", stderr)["sdk_hook_error_seen"] is True


def test_api_extracts_parent_condition_from_tool_result_json():
    from vp_agent.api import _extract_parent_condition

    text = json.dumps(
        {
            "client": "omantel",
            "parent_condition": 'CUST_360_HANDSET_TYPE = "SP" AND CUST_360_RECHARGE_AMOUNT_90D > 100 AND CUST_360_AON > 35 AND CUST_360_TOTAL_ROAMING_REV_FINANCE_REV_W4 ${operator} ${value}',
            "seed_id": "S161_raw_kpi_no_time",
        }
    )

    assert (
        _extract_parent_condition(text)
        == 'CUST_360_HANDSET_TYPE = "SP" AND CUST_360_RECHARGE_AMOUNT_90D > 100 AND CUST_360_AON > 35 AND CUST_360_TOTAL_ROAMING_REV_FINANCE_REV_W4 ${operator} ${value}'
    )


def test_api_extracts_parent_condition_from_render_tool_block():
    from types import SimpleNamespace

    from vp_agent.api import _message_render_parent_condition

    message = SimpleNamespace(
        content=[
            SimpleNamespace(
                name="mcp__vp__render_condition",
                content={
                    "parent_condition": 'CUST_360_HANDSET_TYPE = "SP" AND CUST_360_TOTAL_ROAMING_REV_FINANCE_REV_W4 ${operator} ${value}'
                }
            )
        ]
    )

    assert (
        _message_render_parent_condition(message)
        == 'CUST_360_HANDSET_TYPE = "SP" AND CUST_360_TOTAL_ROAMING_REV_FINANCE_REV_W4 ${operator} ${value}'
    )


def test_api_ignores_seed_template_as_parent_condition():
    from types import SimpleNamespace

    from vp_agent.api import _message_render_parent_condition

    message = SimpleNamespace(
        content=[
            SimpleNamespace(
                name="mcp__vp__select_seed",
                content={
                    "candidates": [
                        {
                            "seed_id": "S60_audience_segment",
                            "output_template": "( AS_SEGMENT_ID ${operator} ${value} AND AS_EXECUTION_COUNTER = ${SEGMENT_EXECUTION_COUNTER} )",
                        }
                    ]
                },
            )
        ]
    )

    assert _message_render_parent_condition(message) is None


def test_api_extracts_clarification_question():
    from vp_agent.api import _extract_clarification_question

    text = "Clarification needed:\nShould high value mean a stored customer value segment such as HIGH, or a revenue/spend threshold over the stated period?"

    question = _extract_clarification_question(text, "high value customers who are smartphone users recorded in the last 30 day")

    assert question.startswith("When you say high value customers")
    assert "existing High Value segment" in question
    assert "last 30 days" in question
    assert "VALUE_SEGMENT" not in question
    assert "PARENT_CONDITION" not in question


def test_api_clarification_question_filters_internal_terms():
    from vp_agent.api import _extract_clarification_question

    text = """
    **Clarification needed before I can render the rule:**
    Should **"high value customer"** mean:
    1. A stored customer value segment (e.g., `VALUE_SEGMENT_OVERALL = HIGH`), or
    2. A revenue/spend-based KPI over a period, e.g., total revenue, ARPU, recharge amount, or CLV, with a threshold?
    Once I have that, I'll resolve columns, pick the seed/template, and render the validated `PARENT_CONDITION`.
    """

    question = _extract_clarification_question(text, "high value customers who are smartphone users recorded in the last 30 day")

    assert "VALUE_SEGMENT_OVERALL" not in question
    assert "seed" not in question
    assert "PARENT_CONDITION" not in question
    assert "revenue, spend, recharge amount, ARPU, or CLV" in question


def test_api_clarification_prefers_final_labelled_question_over_skill_examples():
    from vp_agent.api import _extract_clarification_question

    text = """
    For "high value" ambiguity, ask in business terms, for example:
    "Should high value mean customers in an existing High Value segment, or
    customers whose revenue, spend, recharge amount, ARPU, or CLV crosses a
    threshold over the stated period?"

    Filter 2 ("recharged more than 100") is an aggregate/event condition with no
    stated timeframe.

    **Question:** For the "recharged more than 100" condition, what time period
    should this recharge amount apply to - last month, last 30 days, or month
    till date?
    """

    question = _extract_clarification_question(text, "local financial services revenue in Month till date")

    assert question.startswith('For the "recharged more than 100" condition')
    assert "threshold over the stated period" not in question
    assert "last 30 days" in question


def test_api_returns_clean_percentage_of_amount_clarification():
    from vp_agent.api import _extract_clarification_question

    text = """
    A skill example asked: threshold over the stated period?
    I need one clarification: what should the calculated 20% be used for?
    """
    question = _extract_clarification_question(
        text,
        "To check 20% of recharge amount of prepaid subscribers in the last 2 months",
    )

    assert question == (
        "What should the calculated 20% of the recharge amount be compared with or used for? "
        "For example, should it be compared with a specific threshold or with another amount?"
    )
    assert "stated period" not in question


def test_stable_percentage_of_amount_gate_requires_missing_comparison():
    from vp_agent.api import _stable_clarification_question

    ambiguous = _stable_clarification_question(
        "To check 20% of recharge amount of prepaid subscribers in the last 2 months"
    )
    explicit = _stable_clarification_question(
        "Customers where 20% of recharge amount exceeds the specified threshold"
    )

    assert ambiguous and ambiguous.startswith("What should the calculated 20%")
    assert explicit is None


def test_post_tool_hook_allows_intermediate_templates():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    hooks = make_hooks(ToolState())
    hook = hooks["PostToolUse"][0].hooks[0]
    result = asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__select_seed",
                "tool_input": {"client": "omantel"},
                "tool_response": {
                    "structuredContent": {
                        "selected": {
                            "output_template": "CUST_360_DATA_REVENUE_LOCAL_FINANCE_REV_W6 ${operator} ${value}"
                        }
                    }
                },
            },
            "tool-1",
            {"signal": None},
        )
    )

    assert result == {}


def test_pre_tool_hook_denies_subagent_before_render():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    hooks = make_hooks(ToolState())
    hook = hooks["PreToolUse"][0].hooks[0]
    result = asyncio.run(
        hook(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "verifier"},
            },
            "tool-1",
            {"signal": None},
        )
    )

    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "MCP pipeline" in output["permissionDecisionReason"]


def test_render_hook_stores_parent_condition():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    state = ToolState()
    hook = make_hooks(state)["PostToolUse"][0].hooks[0]

    result = asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__render_condition",
                "tool_input": {"client": "omantel"},
                "tool_response": {
                    "structuredContent": {
                        "parent_condition": 'CUST_360_HANDSET_TYPE = "SP" AND VALUE_SEGMENT_OVERALL ${operator} ${value}'
                    }
                },
            },
            "tool-1",
            {"signal": None},
        )
    )

    assert result == {}
    assert state.rendered_parent_condition == 'CUST_360_HANDSET_TYPE = "SP" AND VALUE_SEGMENT_OVERALL ${operator} ${value}'


def test_render_hook_stores_parent_condition_from_text_json():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    condition = (
        'Profile_Cdr_Handset_Type = "SP" AND Profile_Line_Type = "PREPAID" '
        "AND COMMON_Event_Date >= CurrentTime-7DAYS "
        "AND SUM(COMMON_OG_Local_Offnet_Sms_Revenue) ${operator} ${value}"
    )
    state = ToolState()
    hook = make_hooks(state)["PostToolUse"][0].hooks[0]

    result = asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__render_condition",
                "tool_input": {"client": "omantel"},
                "tool_response": [
                    {
                        "type": "text",
                        "text": json.dumps({"client": "omantel", "parent_condition": condition}),
                    }
                ],
            },
            "tool-1",
            {"signal": None},
        )
    )

    assert result == {}
    assert state.rendered_parent_condition == condition


def test_hooks_capture_agent_resolution_seed_vps_and_validation():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    state = ToolState()
    hook = make_hooks(state)["PostToolUse"][0].hooks[0]
    resolution = {
        "client": "omantel",
        "slots": {
            "kpi_phrase": "data revenue",
            "aggregate": "FORMULA",
            "comparison": {
                "metric_intent": "percentage decline",
                "older_period": "M2",
                "newer_period": "M1",
            },
        },
        "selected_columns": ["COMMON_Data_Revenue"],
        "selected_vps": ["M2_Data_Revenue", "M1_Data_Revenue"],
        "seed_id": "S77_percentage_drop",
        "path": "variant_3",
        "snapshot": False,
        "dependencies": [],
    }

    asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__record_resolution",
                "tool_input": resolution,
                "tool_response": {"structuredContent": resolution},
            },
            "tool-resolution",
            {"signal": None},
        )
    )
    asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__select_seed",
                "tool_input": {"client": "omantel"},
                    "tool_response": {
                        "structuredContent": {
                            "audit_id": "seed-audit",
                            "proposed_selected_seed": {"seed_id": "S77_percentage_drop"},
                        }
                    },
            },
            "tool-seed",
            {"signal": None},
        )
    )
    asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__retrieve_existing_vps",
                "tool_input": {"client": "omantel", "query": "M1 data revenue"},
                "tool_response": {
                    "structuredContent": {
                        "candidates": [{"name": "M1_Data_Revenue", "parent_condition": "evidence"}]
                    }
                },
            },
            "tool-vps",
            {"signal": None},
        )
    )
    validation = {"ok": True, "warnings": [], "referenced_columns": []}
    asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__validate_rule",
                "tool_input": {"client": "omantel"},
                "tool_response": {"structuredContent": validation},
            },
            "tool-validation",
            {"signal": None},
        )
    )

    assert state.resolution == resolution
    assert state.selected_seed == "S77_percentage_drop"
    assert state.seed_audit_ids == ["seed-audit"]
    assert state.existing_vp_candidates[0]["name"] == "M1_Data_Revenue"
    assert state.validation == validation


def test_post_tool_hook_warns_for_non_render_parent_condition_without_denying():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    hooks = make_hooks(ToolState())
    hook = hooks["PostToolUse"][0].hooks[0]
    result = asyncio.run(
        hook(
            {
                "tool_name": "mcp__vp__select_seed",
                "tool_input": {"client": "omantel"},
                "tool_response": {
                    "structuredContent": {
                        "parent_condition": "CUST_360_DATA_REVENUE_LOCAL_FINANCE_REV_W6 ${operator} ${value}"
                    }
                },
            },
            "tool-1",
            {"signal": None},
        )
    )

    output = result["hookSpecificOutput"]
    assert output["hookEventName"] == "PostToolUse"
    assert "additionalContext" in output
    assert "permissionDecision" not in output


def test_cli_accepts_debug_sdk_and_trace_file_flags():
    result = subprocess.run(
        [sys.executable, "-m", "vp_agent.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--debug-sdk" in result.stdout
    assert "--trace-file" in result.stdout
    assert "--deterministic" not in result.stdout


def test_verifier_uses_golden_examples_skill():
    from vp_agent.orchestrator import build_agents

    agents = build_agents("test-model")

    assert set(agents) == {"verifier"}
    assert "vp-golden-examples" in agents["verifier"].skills
    assert "vp-metrics-comparison" in agents["verifier"].skills


def test_verifier_prompt_lives_under_claude_agents():
    from vp_agent.orchestrator import load_agent_prompt

    agents_dir = PROJECT_DIR / ".claude" / "agents"

    assert (agents_dir / "verifier.md").is_file()
    assert "VP Verifier" in load_agent_prompt("verifier")


def test_retrieve_finds_recharge_30d_and_profile_filters():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "filters": [
            {"phrase": "Omani nationals", "value": "Omani"},
            {"phrase": "smartphones", "value": "smartphone"},
        ],
    }

    candidates = retrieve_columns(slots, client="omantel", top_k=20)
    names = {candidate.feature_name for candidate in candidates}

    assert "CUST_360_RECHARGE_AMOUNT_30D" in names or "RECHARGE_Denomination" in names


def test_retrieve_respects_exclude_list():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
    }

    candidates = retrieve_columns(
        slots,
        client="omantel",
        exclude=["CUST_360_RECHARGE_AMOUNT_30D", "3104"],
        top_k=30,
    )
    names = {candidate.feature_name for candidate in candidates}

    assert "CUST_360_RECHARGE_AMOUNT_30D" not in names


def test_retrieve_exposes_hybrid_scores():
    slots = {
        "domain": "profile",
        "kpi_phrase": "smartphone handset type",
        "filters": [{"phrase": "smartphones", "value": "SP"}],
    }

    candidates = retrieve_columns(slots, client="omantel", top_k=10)

    assert candidates
    assert any(candidate.bm25_score > 0 for candidate in candidates)
    assert any(candidate.semantic_score > 0 for candidate in candidates)
    top = candidates[0]
    assert top.hybrid_score == 0.5 * top.bm25_norm + 0.5 * top.embedding_norm
    assert "0.5*bm25_norm" in top.reason


def test_retrieve_diversifies_filter_candidates():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "operator": ">",
        "value": "5",
        "filters": [
            {"phrase": "Omani nationals", "value": "Omani"},
            {"phrase": "smartphones", "value": "smartphone"},
        ],
    }

    candidates = retrieve_columns(slots, client="omantel", top_k=12)
    names = {candidate.feature_name for candidate in candidates}

    assert "CUST_360_RECHARGE_AMOUNT_30D" in names
    assert {"CUST_360_NATIONALITY", "Profile_Cdr_Nationality"} & names
    assert {"CUST_360_HANDSET_TYPE", "Profile_Cdr_Handset_Type"} & names


def test_retrieve_defaults_to_five_candidates():
    candidates = retrieve_columns(
        {"domain": "usage", "kpi_phrase": "data revenue", "time_token": "2D"},
        client="omantel",
    )

    assert 0 < len(candidates) <= 5


def test_role_aware_batch_returns_compact_candidates_per_role():
    slots = {
        "raw_request": "Total pay-as-you-go data used on the local network by smartphone users over last 3 months",
        "domain": "usage",
        "kpi_phrase": "local pay as you go data usage",
        "aggregate": "SUM",
        "time_token": "M3",
        "filters": [{"phrase": "smartphone users", "operator": "=", "value": "smartphone"}],
    }
    audit = build_retrieval_audit(slots, client="omantel")
    result = compact_retrieval_page(audit, audit_id="audit-1")

    assert result["metric_candidates"][0]["feature_name"] == "COMMON_Data_Local_PayG_Volume"
    assert len(result["metric_candidates"]) <= 5
    assert len(result["filter_candidates"]) == 1
    assert len(result["filter_candidates"][0]["candidates"]) <= 5
    assert set(result["metric_candidates"][0]) == {
        "candidate_id",
        "feature_name",
        "group_name",
        "data_type",
        "description",
        "time_window_support",
        "score",
        "evidence",
    }
    assert "bm25_score" not in result["metric_candidates"][0]
    assert len(serialize_retrieval_audit(audit)["roles"]["metric"]["ranking"]) > 5


def test_targeted_retrieval_expansion_reuses_ranked_audit_pages():
    slots = {"domain": "usage", "kpi_phrase": "data revenue", "aggregate": "SUM", "time_token": "2D"}
    audit = build_retrieval_audit(slots, client="omantel")
    first = compact_retrieval_page(audit, audit_id="audit-2", role_ids=["metric"], page=1)
    second = compact_retrieval_page(audit, audit_id="audit-2", role_ids=["metric"], page=2)

    first_ids = {item["candidate_id"] for item in first["metric_candidates"]}
    second_ids = {item["candidate_id"] for item in second["metric_candidates"]}
    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


def test_group_date_configuration_and_subscription_override():
    assert date_column_for_group("Instant_cdr_group") == "FCT_DT"
    assert date_column_for_group("Common_Seg_Fct") == "COMMON_Event_Date"
    assert date_column_for_group("Subscriptions", {"raw_request": "subscription purchase"}) == "SUBSCRIPTIONS_DT"
    assert date_column_for_group("Subscriptions", {"raw_request": "subscription cancellation events"}) == "SUBSCRIPTIONS_EVENT_DATE"
    assert date_column_for_group("LIFECYCLE_CDR") == "L_SENT_DATE"


def test_week_token_aliases_keep_reviewed_four_week_average_snapshot_in_top_five():
    slots = {
        "raw_request": "average revenue from all international outgoing calls over last 4 weeks",
        "domain": "usage",
        "kpi_phrase": "average revenue from international outgoing (IDD) calls",
        "aggregate": "AVG",
        "time_token": "W4",
        "filters": [],
    }
    result = compact_retrieval_page(build_retrieval_audit(slots, "omantel"), audit_id="audit-w4")
    candidates = result["metric_candidates"]
    names = {item["feature_name"] for item in candidates}

    assert "CUST_360_VOICE_REVENUE_IDD_FINANCE_REV_4W_AVG" in names
    snapshot = next(item for item in candidates if item["feature_name"] == "CUST_360_VOICE_REVENUE_IDD_FINANCE_REV_4W_AVG")
    assert snapshot["time_window_support"] == "exact W4 snapshot"


def test_shelf_lookup_prefers_360_for_recharge_amount_30d():
    result = shelf_lookup("recharge amount 30 days", client="omantel")

    assert result["on_shelf"] is True
    assert any(match["feature_name"] == "CUST_360_RECHARGE_AMOUNT_30D" for match in result["matches"])


def test_route_uses_360_when_available():
    decision = route_table("recharge", "recharge amount 30 days", shelf_on_360=True)

    assert decision.table == "360_PROFILE"
    assert "precomputed" in decision.reason


def test_select_seed_uses_raw_for_precomputed_360_kpi():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "operator": ">",
        "value": "5",
    }
    columns = [
        {
            "feature_name": "CUST_360_RECHARGE_AMOUNT_30D",
            "group_name": "360_PROFILE",
            "data_type": "numeric",
        }
    ]

    result = select_seed(slots, client="omantel", columns=columns, table="360_PROFILE")

    selected = result["proposed_selected_seed"]
    assert selected["seed_id"] == "S161_raw_kpi_no_time"
    assert selected["suggested_variables"]["kpi_col"] == "CUST_360_RECHARGE_AMOUNT_30D"
    assert "selection_signature" in selected
    assert all("selection_signature" not in item for item in result["alternatives"])
    assert len(result["alternatives"]) <= 3


def test_select_seed_uses_bounded_days_for_event_recharge():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "operator": ">",
        "value": "5",
    }
    columns = [
        {
            "feature_name": "RECHARGE_Denomination",
            "group_name": "Recharge_Seg_Fct",
            "data_type": "numeric",
        }
    ]

    result = select_seed(slots, client="omantel", columns=columns, table="Recharge_Seg_Fct")

    selected = result["proposed_selected_seed"]
    assert selected["seed_id"] == "S05_last_n_days_bounded"
    assert selected["suggested_variables"]["N"] == 30
    assert selected["suggested_variables"]["kpi_col"] == "RECHARGE_Denomination"
    assert selected["suggested_variables"]["date_col"] == "RECHARGE_Event_Date"


def test_select_seed_uses_airtel_notnull_data_usage_window():
    slots = {
        "domain": "usage",
        "kpi_phrase": "data usage",
        "time_token": "30D",
        "operator": ">",
        "value": "0",
    }
    columns = [
        {
            "feature_name": "S_TOTAL_DATA_USAGE",
            "group_name": "Common_Seg_Fct",
            "data_type": "numeric",
        }
    ]

    result = select_seed(slots, client="airtel", columns=columns, table="Common_Seg_Fct")

    selected = result["proposed_selected_seed"]
    assert selected["seed_id"] in {"S06_last_n_days_bounded_notnull", "S124_airtel_data_usage_extended_bounded"}
    assert selected["suggested_variables"]["kpi_col"] == "S_TOTAL_DATA_USAGE"
    assert selected["suggested_variables"]["date_col"] == "S_FCT_DT"


def test_retrieve_existing_vps_finds_atomic_data_revenue_helpers():
    m1 = retrieve_existing_vps("M1 data revenue", client="omantel", top_k=5)
    m2 = retrieve_existing_vps("M2 data revenue", client="omantel", top_k=5)

    assert m1[0]["name"] == "M1_Data_Revenue"
    assert m2[0]["name"] == "M2_Data_Revenue"
    assert "CurrentMonth-1MONTHS" in m1[0]["parent_condition"]
    assert "CurrentMonth-2MONTHS" in m2[0]["parent_condition"]


def test_select_seed_uses_agent_extracted_period_comparison_as_evidence():
    slots = {
        "raw_request": "Data revenue decline comparing 2 months ago vs last month",
        "domain": "usage",
        "kpi_phrase": "data revenue decline",
        "aggregate": "FORMULA",
        "time_token": "none",
        "operator": "unknown",
        "value": "",
        "filters": [],
        "comparison": {
            "metric_intent": "percentage decline",
            "metric_unit": "percentage",
            "older_period": "M2",
            "newer_period": "M1",
        },
    }

    result = select_seed(slots, client="omantel", table="360_PROFILE", top_k=5)

    selected = result["proposed_selected_seed"]
    assert selected["seed_id"] == "S77_percentage_drop"
    assert "agent-extracted period comparison" in selected["reason"]

    audit = build_seed_audit(slots, client="omantel", table="360_PROFILE")
    serialized = serialize_seed_audit(audit)
    assert len(serialized["candidates"]) > 1
    assert any(item["gate_failures"] for item in serialized["candidates"] if not item["eligible"])


def test_s77_renders_reviewed_rakesh_decline_formula():
    rendered = render_condition(
        seed_id="S77_percentage_drop",
        template=None,
        variables={"older_vp": "M2_Data_Revenue", "newer_vp": "M1_Data_Revenue"},
        filters=[],
        client="omantel",
    )

    rule = rendered["parent_condition"]

    assert rule == "(M2_Data_Revenue - M1_Data_Revenue) / M1_Data_Revenue * 100 ${operator} ${value}"
    assert "V{" not in rule
    assert validate_rule(rule, request="Data revenue decline comparing 2 months ago vs last month", table="360_PROFILE")["ok"]


def test_render_and_validate_last_n_days_sum():
    rendered = render_condition(
        seed_id="S05_last_n_days_bounded",
        template=None,
        variables={
            "date_col": "RECHARGE_Event_Date",
            "N": 30,
            "kpi_col": "RECHARGE_Denomination",
        },
        filters=[
            {"col": "CUST_360_NATIONALITY", "operator": "=", "value": "OMANI"},
            {"col": "CUST_360_HANDSET_TYPE", "operator": "=", "value": "SP"},
        ],
        client="omantel",
    )

    rule = rendered["parent_condition"]
    assert "RECHARGE_Event_Date >= CurrentTime-30DAYS" in rule
    assert "SUM(RECHARGE_Denomination) ${operator} ${value}" in rule

    validation = validate_rule(rule, request="Omani smartphone recharge more than 5 last 30 days", table="Recharge_Seg_Fct")
    assert validation["ok"], validation
    assert "RECHARGE_Denomination" in validation["referenced_columns"]


def test_render_preserves_complete_virtual_formula_with_empty_variables():
    rendered = render_condition(
        seed_id=None,
        template="V{AVG_WEEKLY_REVENUE}=f{COMMON_OG_Call_Revenue/4} ${operator} ${value}",
        variables={},
        filters=[],
        client="omantel",
    )

    assert rendered["parent_condition"] == (
        "V{AVG_WEEKLY_REVENUE}=f{COMMON_OG_Call_Revenue/4} ${operator} ${value}"
    )


def test_render_seed_formula_collapses_template_braces_to_engine_syntax():
    rendered = render_condition(
        seed_id="S14_avg_formula_months",
        template=None,
        variables={
            "date_col": "COMMON_Event_Date",
            "N": 2,
            "vp_name": "AVG_MONTHLY_REVENUE",
            "kpi_col": "COMMON_Data_Local_Bundle_Revenue",
            "divisor": 2,
        },
        filters=[],
        client="omantel",
    )

    rule = rendered["parent_condition"]
    assert "V{AVG_MONTHLY_REVENUE}=f{COMMON_Data_Local_Bundle_Revenue/2}" in rule
    assert "V{{" not in rule


def test_generic_voice_revenue_ranks_above_unrequested_specializations():
    audit = build_retrieval_audit(
        {
            "raw_request": "total voice revenue over last 2 days",
            "domain": "usage",
            "kpi_phrase": "voice revenue",
            "aggregate": "SUM",
            "time_token": "2D",
            "filters": [],
        },
        client="omantel",
    )

    ranked = audit["roles"]["metric"]["ranking"]
    names = [item["candidate"].feature_name for item in ranked[:5]]
    assert names[0] == "Total_Voice_Revenue"
    assert names.index("Total_Voice_Revenue") < names.index("COMMON_Prepay_Voice_Revenue")


def test_generic_total_revenue_candidates_are_not_crowded_out_by_service_snapshots():
    audit = build_retrieval_audit(
        {
            "raw_request": "total revenue of smartphone users in the last 60 days",
            "domain": "usage",
            "kpi_phrase": "total revenue",
            "aggregate": "SUM",
            "time_token": "60D",
            "filters": [],
        },
        client="omantel",
    )

    names = [item["candidate"].feature_name for item in audit["roles"]["metric"]["ranking"][:15]]
    assert "COMMON_Total_Revenue" in names[:5]
    assert "CUST_360_REVENUE_60D" in names
    assert names.index("COMMON_Total_Revenue") < names.index("CUST_360_TOTAL_REV_VOICE_60")


def test_build_condition_plan_360_path_for_example():
    slots = {
        "raw_request": "Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days",
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "operator": ">",
        "value": "5",
        "filters": [
            {"phrase": "Omani nationals", "operator": "=", "value": "Omani"},
            {"phrase": "smartphones", "operator": "=", "value": "smartphone"},
        ],
    }

    result = build_parent_condition(slots, client="omantel")
    rule = result["rendered"]["parent_condition"]

    assert result["ok"], result["validation"]
    assert result["plan"]["path"] == "360"
    assert result["plan"]["seed"]["seed_id"] == "S161_raw_kpi_no_time"
    assert 'CUST_360_NATIONALITY = "OMANI"' in rule
    assert 'CUST_360_HANDSET_TYPE = "SP"' in rule
    assert "CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}" in rule


def test_build_condition_plan_event_fallback_for_example():
    slots = {
        "raw_request": "Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days",
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "operator": ">",
        "value": "5",
        "filters": [
            {"phrase": "Omani nationals", "operator": "=", "value": "Omani"},
            {"phrase": "smartphones", "operator": "=", "value": "smartphone"},
        ],
    }

    result = build_parent_condition(slots, client="omantel", prefer_360=False, force_event=True)
    plan = result["plan"]
    rule = result["rendered"]["parent_condition"]

    assert result["ok"], result["validation"]
    assert plan["path"] == "event"
    assert plan["seed"]["seed_id"] == "S05_last_n_days_bounded"
    assert plan["render_input"]["variables"]["date_col"] == "RECHARGE_Event_Date"
    assert plan["render_input"]["variables"]["N"] == 30
    assert "RECHARGE_Event_Date >= CurrentTime-30DAYS" in rule
    assert "RECHARGE_Event_Date < CurrentTime" in rule
    assert "SUM(RECHARGE_Denomination) ${operator} ${value}" in rule


def test_build_condition_plan_does_not_emit_rule_syntax():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "operator": ">",
        "value": "5",
        "filters": [],
    }

    plan = build_condition_plan(slots, client="omantel")

    assert "parent_condition" not in plan
    assert plan["render_input"]["seed_id"] == "S161_raw_kpi_no_time"


def test_normalize_slots_for_example_sentence():
    sentence = "Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days"

    slots = normalize_slots(sentence, client="omantel")

    assert slots["needs_clarification"] is False
    assert slots["domain"] == "recharge"
    assert slots["kpi_phrase"] == "recharge amount"
    assert slots["time_token"] == "30D"
    assert slots["operator"] == ">"
    assert slots["value"] == "5"
    assert {"phrase": "Omani nationals", "operator": "=", "value": "Omani"} in slots["filters"]
    assert {"phrase": "smartphones", "operator": "=", "value": "smartphone"} in slots["filters"]


def test_normalize_slots_does_not_require_main_kpi_threshold():
    sentence = "Revenue from outgoing off-net SMS for prepaid smartphone users based on events recorded in the last 7 days"

    slots = normalize_slots(sentence, client="omantel")

    assert slots["needs_clarification"] is False
    assert slots["operator"] == "unknown"
    assert slots["value"] == ""
    assert "operator" not in slots["missing"]
    assert "value" not in slots["missing"]
    assert slots["time_token"] == "7D"


def test_raw_sentence_to_parent_condition_360_path():
    sentence = "Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days"
    slots = normalize_slots(sentence, client="omantel")

    result = build_parent_condition(slots, client="omantel", request=sentence)
    rule = result["rendered"]["parent_condition"]

    assert result["ok"], result["validation"]
    assert result["plan"]["path"] == "360"
    assert 'CUST_360_NATIONALITY = "OMANI"' in rule
    assert 'CUST_360_HANDSET_TYPE = "SP"' in rule
    assert "CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}" in rule


def test_raw_sentence_to_parent_condition_event_fallback():
    sentence = "Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days"
    slots = normalize_slots(sentence, client="omantel")

    result = build_parent_condition(slots, client="omantel", request=sentence, prefer_360=False, force_event=True)
    rule = result["rendered"]["parent_condition"]

    assert result["ok"], result["validation"]
    assert result["plan"]["path"] == "event"
    assert "RECHARGE_Event_Date >= CurrentTime-30DAYS" in rule
    assert "RECHARGE_Event_Date < CurrentTime" in rule
    assert "SUM(RECHARGE_Denomination) ${operator} ${value}" in rule


def test_cli_rejects_removed_deterministic_flag():
    sentence = "Omani nationals with smartphones who recharged more than 5 OMR in the last 30 days"
    completed = subprocess.run(
        [
            str(PROJECT_DIR / ".venv/bin/python"),
            "-m",
            "vp_agent.cli",
            "--deterministic",
            "--client",
            "omantel",
            sentence,
        ],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    assert "unrecognized arguments: --deterministic" in completed.stderr


def test_360_m1_snapshot_has_no_date_condition():
    sentence = "customers whose recharge amount last month is more than 5 OMR"
    slots = normalize_slots(sentence, client="omantel")

    result = build_parent_condition(slots, client="omantel", request=sentence)
    rule = result["rendered"]["parent_condition"]

    assert slots["time_token"] == "M1"
    assert result["plan"]["path"] == "360"
    assert result["plan"]["main_column"]["feature_name"] == "CUST_360_RECHARGE_M1"
    assert result["plan"]["seed"]["seed_id"] == "S161_raw_kpi_no_time"
    assert rule == "CUST_360_RECHARGE_M1 ${operator} ${value}"
    assert "CurrentMonth" not in rule
    assert "CurrentTime" not in rule
    assert "Event_Date" not in rule


def test_360_m2_snapshot_has_no_date_condition():
    sentence = "customers whose recharge amount in M2 is more than 5 OMR"
    slots = normalize_slots(sentence, client="omantel")

    result = build_parent_condition(slots, client="omantel", request=sentence)
    rule = result["rendered"]["parent_condition"]

    assert slots["time_token"] == "M2"
    assert result["plan"]["path"] == "360"
    assert result["plan"]["main_column"]["feature_name"] == "CUST_360_RECHARGE_M2"
    assert result["plan"]["seed"]["seed_id"] == "S161_raw_kpi_no_time"
    assert rule == "CUST_360_RECHARGE_M2 ${operator} ${value}"
    assert "CurrentMonth" not in rule
    assert "CurrentTime" not in rule
    assert "Event_Date" not in rule


def test_360_w1_snapshot_has_no_date_condition():
    sentence = "customers whose total recharge in W1 is more than 5 OMR"
    slots = normalize_slots(sentence, client="omantel")

    result = build_parent_condition(slots, client="omantel", request=sentence)
    rule = result["rendered"]["parent_condition"]

    assert slots["time_token"] == "W1"
    assert result["plan"]["path"] == "360"
    assert result["plan"]["snapshot"] is True
    assert result["plan"]["seed"]["seed_id"] == "S161_raw_kpi_no_time"
    assert result["plan"]["main_column"]["time_window_value"] == "W1"
    assert "SUM(" not in rule
    assert "CurrentMonth" not in rule
    assert "CurrentTime" not in rule
    assert "Event_Date" not in rule


def test_360_w2_snapshot_has_no_date_condition():
    sentence = "customers whose total recharge in W2 is more than 5 OMR"
    slots = normalize_slots(sentence, client="omantel")

    result = build_parent_condition(slots, client="omantel", request=sentence)
    rule = result["rendered"]["parent_condition"]

    assert slots["time_token"] == "W2"
    assert result["plan"]["path"] == "360"
    assert result["plan"]["snapshot"] is True
    assert result["plan"]["seed"]["seed_id"] == "S161_raw_kpi_no_time"
    assert result["plan"]["main_column"]["time_window_value"] == "W2"
    assert "SUM(" not in rule
    assert "CurrentMonth" not in rule
    assert "CurrentTime" not in rule
    assert "Event_Date" not in rule


def test_360_30d_snapshot_has_no_date_condition():
    sentence = "customers whose recharge amount in the last 30 days is more than 5 OMR"
    slots = normalize_slots(sentence, client="omantel")

    result = build_parent_condition(slots, client="omantel", request=sentence)
    rule = result["rendered"]["parent_condition"]

    assert result["plan"]["path"] == "360"
    assert result["plan"]["snapshot"] is True
    assert result["plan"]["seed"]["seed_id"] == "S161_raw_kpi_no_time"
    assert result["plan"]["main_column"]["feature_name"] == "CUST_360_RECHARGE_AMOUNT_30D"
    assert rule == "CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}"
    assert "CurrentTime-30DAYS" not in rule


def test_snapshot_detector_generalizes_customer_360_windows():
    assert is_customer_360_snapshot({"group_name": "360_PROFILE", "feature_name": "CUST_360_RECHARGE_M1", "time_window_value": "M1"})
    assert is_customer_360_snapshot({"group_name": "360_PROFILE", "feature_name": "CUST_360_TOTAL_RECHARGE_WK1", "time_window_value": "W1"})
    assert is_customer_360_snapshot({"group_name": "360_PROFILE", "feature_name": "CUST_360_RECHARGE_AMOUNT_30D", "time_window_value": "30D"})
    assert not is_customer_360_snapshot({"group_name": "Recharge_Seg_Fct", "feature_name": "RECHARGE_Denomination", "time_window_value": ""})


def test_loads_actual_golden_dataset():
    rows = load_golden_cases(DEFAULT_GOLDEN_PATH)

    assert len(rows) == 57
    assert set(rows[0]) == {"NL Input", "Expected Output"}


def test_golden_snapshot_outputs_have_no_date_conditions():
    rows = load_golden_cases(DEFAULT_GOLDEN_PATH)
    snapshot_cases = find_360_snapshot_cases(rows)

    assert snapshot_cases
    for row in snapshot_cases:
        expected = row["Expected Output"]
        assert not has_date_condition(expected), row


def test_selected_golden_snapshot_cases_render_raw_when_supported():
    rows = load_golden_cases(DEFAULT_GOLDEN_PATH)
    supported_inputs = {
        "total data bundle revenue of a customer for the last 1 months": "TOTAL_DATA_BUNDLE_REVENUE_M1",
        "Total offnet finance revenue generated by a customer in the last month": "CUST_360_VOICE_REVENUE_OFFNET_FINANCE_REV_M1",
    }
    by_input = {row["NL Input"]: row for row in rows}

    for nl_input, expected_column in supported_inputs.items():
        assert nl_input in by_input
        expected = by_input[nl_input]["Expected Output"]
        assert condition_column(expected) == expected_column
        assert not has_date_condition(expected)

        slots = normalize_slots(nl_input, client="omantel")
        result = build_parent_condition(slots, client="omantel", request=nl_input)
        rule = result["rendered"]["parent_condition"]

        assert result["plan"]["path"] == "360"
        assert result["plan"]["snapshot"] is True
        assert result["plan"]["seed"]["seed_id"] == "S161_raw_kpi_no_time"
        assert "CurrentMonth" not in rule
        assert "CurrentTime" not in rule
        assert "Event_Date" not in rule
