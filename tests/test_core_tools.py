from __future__ import annotations

import json
import os
import re
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
    assert "Clarification question: <one batched plain-English question>" in ORCHESTRATOR_APPEND
    assert "Do not search the filesystem" in ORCHESTRATOR_APPEND
    assert "never a reason to discard a well-matched" in ORCHESTRATOR_APPEND
    assert "supply.advisory" in ORCHESTRATOR_APPEND
    assert "Warnings are advisory, not blocking" in ORCHESTRATOR_APPEND
    assert "Never finish having" in ORCHESTRATOR_APPEND
    assert "rolling and renders CurrentTime-NDAYS" in ORCHESTRATOR_APPEND

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

    text = (
        "Clarification question: Should high value mean an existing customer value segment, "
        "or a revenue, spend, recharge amount, ARPU, or CLV threshold over the last 30 days?"
    )

    question = _extract_clarification_question(text)

    assert question.startswith("Should high value mean")
    assert "existing customer value segment" in question
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

    Clarification question: Should high value mean an existing customer value segment, or a revenue, spend, recharge amount, ARPU, or CLV threshold?
    """

    question = _extract_clarification_question(text)

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

    question = _extract_clarification_question(text)

    assert question.startswith('For the "recharged more than 100" condition')
    assert "threshold over the stated period" not in question
    assert "last 30 days" in question


def test_api_prefers_structured_clarification_question():
    from vp_agent.api import _clarification_question_from_slots

    question = _clarification_question_from_slots(
        {
            "needs_clarification": True,
            "questions": ["Does 20% refer to the top subscribers by recharge amount, or to another population?"],
        }
    )

    assert question == "Does 20% refer to the top subscribers by recharge amount, or to another population?"


def test_api_clarification_allows_adjustable_business_threshold():
    from vp_agent.api import _extract_clarification_question

    text = (
        "A skill example asked: threshold over the stated period?\n"
        "Clarification question: Could you provide the recharge-amount cutoff, "
        "or should the audience use an adjustable threshold?"
    )

    assert _extract_clarification_question(text) == (
        "Could you provide the recharge-amount cutoff, or should the audience use an adjustable threshold?"
    )


def test_api_routes_percentage_request_to_agent(monkeypatch):
    from contextlib import nullcontext

    import vp_agent.api as api_module

    calls = []

    async def fake_build_agentic(request, *, request_id, session_id):
        calls.append((request.sentence, request_id, session_id))
        return api_module.VPBuildResponse(
            ok=False,
            request_id=request_id,
            session_id=session_id,
            orchestrator_model="test-orchestrator",
            subagent_model="test-subagent",
            client=request.client,
            sentence=request.sentence,
            failure_reason="test response",
        )

    monkeypatch.setattr(api_module, "_build_agentic", fake_build_agentic)
    monkeypatch.setattr(api_module, "observation", lambda *args, **kwargs: nullcontext(None))
    monkeypatch.setattr(api_module, "update_observation", lambda *args, **kwargs: None)

    response = asyncio.run(
        api_module.build_vp(
            api_module.VPBuildRequest(
                client="omantel",
                sentence="To check 20% of recharge amount of prepaid subscribers in the last 2 months",
                request_id="percentage-agent-path",
            )
        )
    )

    assert calls == [
        (
            "To check 20% of recharge amount of prepaid subscribers in the last 2 months",
            "percentage-agent-path",
            "vp-omantel",
        )
    ]
    assert response.request_id == "percentage-agent-path"


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


def _search(hook, query: str):
    return asyncio.run(
        hook({"tool_name": "ToolSearch", "tool_input": {"query": query}}, "tool-1", {"signal": None})
    )


def test_tool_search_loop_is_broken_after_one_retry():
    # B1: six searches for normalize_slots, zero calls, then invented columns.
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    hook = make_hooks(ToolState())["PreToolUse"][0].hooks[0]
    query = "select:mcp__vp__normalize_slots"

    assert _search(hook, query) == {}  # first load
    assert _search(hook, query) == {}  # one retry is allowed
    blocked = _search(hook, query)

    output = blocked["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "call the tool directly" in output["permissionDecisionReason"]


def test_tool_search_is_not_blocked_once_the_tool_has_been_called():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    hook = make_hooks(ToolState())["PreToolUse"][0].hooks[0]
    query = "select:mcp__vp__normalize_slots"

    _search(hook, query)
    asyncio.run(
        hook({"tool_name": "mcp__vp__normalize_slots", "tool_input": {}}, "tool-2", {"signal": None})
    )

    assert _search(hook, query) == {}
    assert _search(hook, query) == {}


def test_stop_hook_blocks_finishing_without_a_rendered_rule():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    state = ToolState(retrieval_audit_ids=["audit-1"])
    hook = make_hooks(state)["Stop"][0].hooks[0]

    result = asyncio.run(hook({"stop_hook_active": False}, "stop-1", {"signal": None}))
    assert result["decision"] == "block"
    assert "render_condition" in result["reason"]

    # Exactly one intervention, so a genuine clarification can still finish.
    assert asyncio.run(hook({"stop_hook_active": False}, "stop-2", {"signal": None})) == {}


def test_stop_hook_names_the_missing_evidence_when_nothing_was_retrieved():
    # B4: asked what "non-responder" meant without calling a single tool, while
    # the client's own VP names define the term twenty times over.
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    hook = make_hooks(ToolState())["Stop"][0].hooks[0]
    result = asyncio.run(hook({"stop_hook_active": False}, "stop-1", {"signal": None}))

    assert result["decision"] == "block"
    assert "retrieve_columns" in result["reason"]
    assert "retrieve_existing_vps" in result["reason"]


def test_production_usage_separates_identically_documented_columns():
    # B5: L_AGG_MSISDN and L_AGG_CNT share a description word for word, so only
    # how often production uses each can tell them apart.
    from vp_agent.tools.retrieval_index import client_column_usage

    usage = client_column_usage("omantel")

    assert usage.get("L_AGG_MSISDN", 0) > usage.get("L_AGG_CNT", 0)


def test_seed_clause_regression_catches_a_dropped_null_guard():
    # B5 selected S42_promo_groupby_max and rendered everything except its
    # `Max({date_col}) <> NULL` clause — the "confirmed by the sent date" part.
    from vp_agent.tools.validate import seed_clause_regressions

    template = (
        "{date_col} >= CurrentTime-{N}DAYS AND {key_col} ${operator} ${value} "
        "AND COUNT_ALL({count_col})__groupby_{key_col} > 0 AND Max({date_col}) <> NULL"
    )
    rendered = (
        "L_SENT_DATE >= CurrentTime-2DAYS AND L_ACTION_KEY ${operator} ${value} "
        "AND COUNT_ALL(L_AGG_CNT)__groupby_L_ACTION_KEY > 0"
    )

    clauses = [item["clause"] for item in seed_clause_regressions(rendered, template)]
    assert clauses == ["null_guard"]


def test_seed_clause_regression_is_silent_when_the_rule_keeps_the_skeleton():
    from vp_agent.tools.validate import seed_clause_regressions

    template = (
        "{date_col} >= CurrentTime-{N}DAYS AND {key_col} ${operator} ${value} "
        "AND COUNT_ALL({count_col})__groupby_{key_col} > 0 AND Max({date_col}) <> NULL"
    )
    rendered = (
        "L_PROMO_SENT_DATE >= CurrentTime-2DAYS AND L_ACTION_KEY ${operator} ${value} "
        "AND COUNT_ALL(L_AGG_MSISDN)__groupby_L_ACTION_KEY > 0 AND Max(L_PROMO_SENT_DATE) <> NULL"
    )

    assert seed_clause_regressions(rendered, template) == []


def test_convention_check_reports_a_production_guard_the_rule_drops():
    # The wrong aggregated column used to silence this check entirely: the
    # production twin aggregates L_AGG_MSISDN, so keying on L_AGG_CNT matched
    # nothing. The selector-column fallback finds it anyway.
    from vp_agent.tools.validate import production_shape_differences

    rendered = (
        "L_SENT_DATE >= CurrentTime-2DAYS AND L_ACTION_KEY ${operator} ${value} "
        "AND COUNT_ALL(L_AGG_CNT)__groupby_L_ACTION_KEY > 0"
    )

    findings = production_shape_differences(rendered, "omantel")
    joined = " ".join(difference for item in findings for difference in item["differences"])

    assert findings
    assert "presence guard" in joined, joined


def test_compound_variants_bridge_marketer_spacing_to_catalog_naming():
    # "non-responders" tokenizes to non + responders; the VP name carries the
    # single token nonresponder, so without bridging they never match at all.
    from vp_agent.text import tokens
    from vp_agent.tools.retrieve_vps import _compound_variants

    variants = _compound_variants(tokens("non-responders to a bonus"))

    assert "nonresponder" in variants
    assert "nonresponders" in variants
    assert "actionkey" in _compound_variants(tokens("a particular action key"))


def test_existing_vp_lookup_finds_the_nonresponder_family():
    from vp_agent.tools.retrieve_vps import retrieve_existing_vps

    candidates = retrieve_existing_vps("non-responders to a bonus in the last 4 days", "omantel")
    names = [item["name"] for item in candidates]

    assert any("NONRESPONDER" in name for name in names), names


def test_stop_hook_stays_out_of_the_way_once_the_rule_is_rendered():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    state = ToolState(render_seen=True)
    hook = make_hooks(state)["Stop"][0].hooks[0]

    assert asyncio.run(hook({"stop_hook_active": False}, "stop-1", {"signal": None})) == {}


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
    # An aggregate rendered without consulting select_seed now draws an advisory,
    # so mark it called: this test is about storing the condition, not warnings.
    state = ToolState(tools_called={"mcp__vp__select_seed"})
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


def test_verifier_reads_evidence_not_the_orchestrators_instructions():
    """The verifier only adds value when it can reach a different conclusion.

    Loading the orchestrator's own procedural skills made it restate that
    reasoning, so it would have passed the rule that silently dropped a stated
    threshold. It keeps `vp-metrics-comparison` because the reviewed Variant-3
    convention is a business fact it cannot infer from evidence.
    """
    from vp_agent.orchestrator import build_agents

    agents = build_agents("test-model")
    verifier = agents["verifier"]

    assert set(agents) == {"verifier"}
    assert verifier.skills == ["vp-metrics-comparison"]
    for echoed in ("vp-rendering-rules", "vp-golden-examples", "vp-variant-selection", "vp-disambiguation"):
        assert echoed not in verifier.skills
    # It needs production VPs to have an independent view to compare against.
    assert "mcp__vp__retrieve_existing_vps" in verifier.tools


def test_orchestrator_triggers_the_verifier_on_objective_conditions():
    from vp_agent.orchestrator import ORCHESTRATOR_APPEND

    assert "confidence is not high" not in ORCHESTRATOR_APPEND
    assert "confidence is highest" in ORCHESTRATOR_APPEND
    for trigger in ("supply.advisory", "unexplained_terms", "Variant-3 period comparison"):
        assert trigger in ORCHESTRATOR_APPEND


def test_hook_captures_the_verifier_verdict():
    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    state = ToolState()
    hook = make_hooks(state)["PostToolUse"][0].hooks[0]

    asyncio.run(
        hook(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "verifier"},
                "tool_response": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Restated request: prepaid smartphone customers...\n"
                                "VERDICT: retry — the stated count threshold of 4 is absent from the rule"
                            ),
                        }
                    ]
                },
            },
            "tool-verifier",
            {"signal": None},
        )
    )

    assert state.verifier_verdict["decision"] == "retry"
    assert "threshold of 4" in state.verifier_verdict["detail"]
    assert state.verifier_verdict["agent"] == "verifier"


def test_verifier_verdict_reaches_the_api_response(monkeypatch):
    import vp_agent.api as api_module
    from vp_agent.schemas import ToolState

    state = ToolState(
        render_seen=True,
        rendered_parent_condition="CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}",
        verifier_verdict={"decision": "pass", "detail": "rule matches the request", "agent": "verifier"},
    )

    async def fake_run_request(*args, **kwargs):
        if False:
            yield None

    monkeypatch.setattr(api_module, "ToolState", lambda **kwargs: state)
    monkeypatch.setattr(api_module, "run_request", fake_run_request)

    response = asyncio.run(
        api_module._build_agentic(
            api_module.VPBuildRequest(client="omantel", sentence="recharge amount in the last 30 days")
        )
    )

    assert response.verifier_verdict["decision"] == "pass"


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
        "observed_values",
        "production_uses",
        "adaptations",
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
    # Instant_cdr_group was configured to FCT_DT, which appears in 0 production
    # rules; created_date appears in 8.
    assert date_column_for_group("Instant_cdr_group") == "created_date"
    assert date_column_for_group("Common_Seg_Fct") == "COMMON_Event_Date"
    assert date_column_for_group("Subscriptions", {"raw_request": "subscription purchase"}) == "SUBSCRIPTIONS_EVENT_DATE"
    assert date_column_for_group("Subscriptions", {"raw_request": "subscription cancellation events"}) == "SUBSCRIPTIONS_EVENT_DATE"


def test_lifecycle_date_column_follows_the_event_type():
    # Production: L_PROMO_SENT_DATE 47 rules, L_BONUS_SENT_DATE 12, L_SENT_DATE 16.
    # A single default put the wrong date into every promotion and bonus rule.
    assert date_column_for_group("LIFECYCLE_CDR") == "L_SENT_DATE"
    assert date_column_for_group("LIFECYCLE_CDR", {"raw_request": "got a promotion delivered"}) == "L_PROMO_SENT_DATE"
    assert date_column_for_group("LIFECYCLE_CDR", {"raw_request": "received fewer than 3 bonuses"}) == "L_BONUS_SENT_DATE"
    assert (
        date_column_for_group(
            "LIFECYCLE_CDR",
            {"kpi_phrase": "count of customers", "filters": [{"phrase": "promotion", "value": ["Promotion"]}]},
        )
        == "L_PROMO_SENT_DATE"
    )
    assert date_column_for_group("LIFECYCLE_CDR", {"raw_request": "any lifecycle event"}) == "L_SENT_DATE"


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


def _candidate(feature_name: str, description: str, group_name: str = "Profile_Cdr_group"):
    from vp_agent.schemas import Candidate

    return Candidate(
        id="test",
        feature_name=feature_name,
        group_name=group_name,
        description=description,
        data_type="categorical",
        time_window_value="",
        score=1.0,
        reason="",
    )


def test_value_vocabulary_links_marketer_values_to_their_columns():
    from vp_agent.tools.retrieval_index import column_value_vocabulary

    vocabulary = column_value_vocabulary()

    # kpi_meta records value_references "NONE" for nationality, so the only
    # path from the word "Indian" to this column is mined usage.
    assert "indian" in vocabulary.get("Profile_Cdr_Nationality", ())
    assert "iphone" in vocabulary.get("Profile_Cdr_Handset_Type", ())


def test_observed_values_preserve_production_casing():
    from vp_agent.tools.retrieval_index import column_observed_values

    action_type = column_observed_values().get("LC_ACTION_TYPE", ())

    # The casing is the whole point: a single-case equality under-selects, which
    # is why every production rule matches this column as a membership list.
    assert {"Promotion", "PROMOTION", "promotion"} <= set(action_type)
    assert {"BONUS", "Bonus", "bonus"} <= set(action_type)


def test_observed_values_exclude_date_anchors_and_keywords():
    from vp_agent.tools.retrieval_index import column_observed_values

    anchor = re.compile(r"Current(?:Time|Week|Month)", re.I)
    for column, values in column_observed_values().items():
        for value in values:
            assert not anchor.search(value), f"{column} kept a date anchor: {value}"
            assert value.upper() != "NULL", f"{column} kept a NULL guard"


def test_categorical_candidates_expose_their_observed_values():
    slots = {
        "raw_request": "customers who got a promotion in the last 4 days",
        "domain": "lifecycle",
        "kpi_phrase": "promotion count",
        "aggregate": "COUNT",
        "time_token": "4D",
        "filters": [{"phrase": "promotion", "operator": "=", "value": "promotion", "domain": "lifecycle"}],
    }

    page = compact_retrieval_page(build_retrieval_audit(slots, "omantel"), audit_id="promo")
    shown = {
        item["feature_name"]: item["observed_values"]
        for role in page["filter_candidates"]
        for item in role["candidates"]
    }

    assert "LC_ACTION_TYPE" in shown
    assert "Promotion" in shown["LC_ACTION_TYPE"]


def test_retrieval_finds_nationality_from_a_bare_value():
    candidates = retrieve_columns({"kpi_phrase": "Indian"}, client="omantel", top_k=5)

    assert "Profile_Cdr_Nationality" in {candidate.feature_name for candidate in candidates}


def test_unexplained_terms_flag_a_compound_filter_phrase():
    from vp_agent.tools.retrieve import unexplained_phrase_terms

    handset = _candidate(
        "Profile_Cdr_Handset_Type",
        "The category of the device being used (e.g., Smartphone, Feature Phone).",
    )

    assert unexplained_phrase_terms("Indian iPhone customers", [handset]) == ["indian"]


def test_unexplained_terms_flag_a_metric_column_that_is_too_broad():
    from vp_agent.tools.retrieve import unexplained_phrase_terms

    broad = _candidate("COMMON_OG_Sms_Revenue", "Revenue from outgoing SMS.", "Common_Seg_Fct")

    assert unexplained_phrase_terms("outgoing international sms revenue", [broad]) == ["international"]


def test_unexplained_terms_tolerate_plurals_and_synonyms():
    from vp_agent.tools.retrieve import unexplained_phrase_terms

    handset = _candidate(
        "Profile_Cdr_Handset_Type",
        "The category of the device being used (e.g., Smartphone, Feature Phone).",
    )

    assert unexplained_phrase_terms("smartphones", [handset]) == []
    assert unexplained_phrase_terms("smartphone customers", [handset]) == []


def test_unaddressed_terms_catch_a_phrase_that_never_became_a_role():
    # The Z1 regression: "based on segment name" was dropped at extraction, so
    # no role carried it, retrieval never searched for LC_SEGMENT_NAME, and the
    # agent then cited that silence as proof no segment column existed.
    # Per-role unexplained_terms cannot see this; only a whole-request sweep can.
    from vp_agent.tools.retrieve import unaddressed_request_terms

    slots = {
        "kpi_phrase": "promotion count",
        "aggregate": "COUNT_ALL",
        "filters": [
            {"phrase": "promotion", "operator": "IN LIST", "value": ["Promotion", "PROMOTION", "promotion"]}
        ],
        "negations": ["did not get any promotion"],
    }
    request = "Find customers who did not get any promotion in the last 4 days, based on segment name."

    assert unaddressed_request_terms(request, slots) == ["segment", "name"]


def test_unaddressed_terms_stay_silent_once_the_selector_is_a_role():
    from vp_agent.tools.retrieve import unaddressed_request_terms

    slots = {
        "kpi_phrase": "promotion count",
        "filters": [
            {"phrase": "promotion", "operator": "IN LIST", "value": ["Promotion"]},
            {"phrase": "segment name", "operator": "runtime", "value": None},
        ],
        "negations": ["did not get any promotion"],
    }
    request = "Find customers who did not get any promotion in the last 4 days, based on segment name."

    assert unaddressed_request_terms(request, slots) == []


def test_unaddressed_terms_ignore_window_words_already_in_the_time_token():
    # "last"/"days" are represented by time_token, so reporting them on every
    # request would bury the real signal.
    from vp_agent.tools.retrieve import unaddressed_request_terms

    slots = {"kpi_phrase": "data revenue", "time_token": "30D", "filters": []}

    assert unaddressed_request_terms("Find customers with data revenue in the last 30 days", slots) == []


def test_retrieval_page_reports_unexplained_terms_per_role():
    slots = {
        "raw_request": "Indian iPhone customers whose data revenue in the last 2 weeks is high",
        "domain": "usage",
        "kpi_phrase": "data revenue",
        "aggregate": "SUM",
        "time_token": "2W",
        "filters": [{"phrase": "Indian iPhone customers", "operator": "=", "value": "Indian"}],
    }

    page = compact_retrieval_page(build_retrieval_audit(slots, "omantel"), audit_id="compound")

    assert "metric_unexplained_terms" in page
    assert page["filter_candidates"]
    assert "unexplained_terms" in page["filter_candidates"][0]


def test_period_mismatched_snapshots_stay_visible_with_a_reason():
    # A wrong period often means the extracted time token is wrong, not that the
    # column is wrong. The candidate must survive so the agent can notice.
    slots = {
        "raw_request": "recharge amount last month",
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "M1",
        "filters": [],
    }
    ranking = build_retrieval_audit(slots, "omantel")["roles"]["metric"]["ranking"]
    mismatched = [item for item in ranking if any("snapshot_period_mismatch" in note for note in item["adaptations"])]

    assert mismatched
    assert all(item["eligible"] for item in mismatched)
    assert all(not item["gate_failures"] for item in mismatched)


def test_clean_candidates_still_outrank_adapted_ones():
    slots = {
        "raw_request": "recharge amount last month",
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "M1",
        "filters": [],
    }
    ranking = [item for item in build_retrieval_audit(slots, "omantel")["roles"]["metric"]["ranking"] if item["eligible"]]
    first_adapted = next((index for index, item in enumerate(ranking) if item["adaptations"]), len(ranking))

    assert all(not item["adaptations"] for item in ranking[:first_adapted])
    assert all(item["adaptations"] for item in ranking[first_adapted:])


def test_non_numeric_metric_for_a_sum_is_still_blocked():
    slots = {
        "raw_request": "total data revenue in the last 2 weeks",
        "domain": "usage",
        "kpi_phrase": "data revenue",
        "aggregate": "SUM",
        "time_token": "2W",
        "filters": [],
    }
    page = compact_retrieval_page(build_retrieval_audit(slots, "omantel"), audit_id="sum-block")

    assert page["metric_candidates"]
    assert all(item["data_type"] not in {"string", "categorical", "date"} for item in page["metric_candidates"])


def test_exact_snapshot_is_detected_below_the_top_candidate():
    from vp_agent.tools.retrieve import compact_retrieval_page as page_fn

    audit = build_retrieval_audit(
        {
            "raw_request": "recharge amount in the last 30 days",
            "domain": "recharge",
            "kpi_phrase": "recharge amount",
            "time_token": "30D",
            "filters": [],
        },
        "omantel",
    )
    page = page_fn(audit, audit_id="snapshot-scan")
    exact = [item for item in page["metric_candidates"] if item["time_window_support"].startswith("exact ")]

    assert exact
    assert page["time_assessment"]["exact_snapshot_found"] is True
    assert page["time_assessment"]["requires_event_date"] is False


def test_filter_role_inherits_its_own_period():
    from vp_agent.tools.retrieve import _filter_role_slots

    inherited = _filter_role_slots({}, {"phrase": "purchased a product", "time_token": "45D", "domain": "subscription"})
    default = _filter_role_slots({}, {"phrase": "smartphones", "value": "smartphone"})

    assert inherited["time_token"] == "45D"
    assert inherited["domain"] == "subscription"
    assert default["time_token"] == "none"
    assert default["domain"] == "profile"


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


def test_select_seed_uses_percentage_formula_for_event_recharge_months():
    slots = {
        "raw_request": "Calculate 20% of recharge amount of prepaid subscribers in last 2 months",
        "domain": "recharge",
        "kpi_phrase": "recharge denomination",
        # The formula object remains authoritative even when an agent reports
        # the outer aggregation as SUM.
        "aggregate": "SUM",
        "time_token": "2M",
        "operator": "unknown",
        "value": "",
        "filters": [{"phrase": "line type prepaid", "operator": "=", "value": "Prepaid"}],
        "formula": {"type": "percentage_of_kpi", "percentage": 20, "factor": 0.2},
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
    assert selected["seed_id"] == "S160_percentage_of_kpi_formula_months_lower_only"
    assert selected["suggested_variables"] == {
        "N": 2,
        "kpi_col": "RECHARGE_Denomination",
        "date_col": "RECHARGE_Event_Date",
        "factor": 0.2,
        "vp_name": "RECHARGE_Denomination_MUL_0_2",
    }


def test_role_retrieval_prefers_numeric_recharge_denomination_for_percentage_formula():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge denomination",
        "time_token": "2M",
        "formula": {"type": "percentage_of_kpi", "percentage": 20, "factor": 0.2},
        "filters": [{"phrase": "line type prepaid", "operator": "=", "value": "Prepaid"}],
    }

    page = compact_retrieval_page(build_retrieval_audit(slots, "omantel"), audit_id="percentage-recharge")

    assert page["metric_candidates"][0]["feature_name"] == "RECHARGE_Denomination"
    assert all(item["data_type"] == "numeric" for item in page["metric_candidates"])


COUNT_THRESHOLD_SLOTS = {
    "raw_request": "To select customers who purchased any product at most 4 times in the last week",
    "domain": "subscription",
    "kpi_phrase": "product purchase count",
    "time_token": "W1",
    "operator": "<=",
    "value": "4",
    "aggregate": "COUNT",
    "filters": [],
}
COUNT_THRESHOLD_COLUMNS = [
    {"feature_name": "SUBSCRIPTIONS_Product_Id", "group_name": "Subscriptions", "data_type": "string"}
]
COUNT_THRESHOLD_RULE = (
    "SUBSCRIPTIONS_DT >= CurrentWeek-1WEEKS AND SUBSCRIPTIONS_DT < CurrentWeek "
    "AND COUNT_ALL(SUBSCRIPTIONS_Product_Id) ${operator} ${value}"
)


def test_count_threshold_seed_infers_key_col_and_threshold():
    audit = build_seed_audit(
        COUNT_THRESHOLD_SLOTS,
        client="omantel",
        columns=COUNT_THRESHOLD_COLUMNS,
        table="Subscriptions",
    )
    seed = next(item for item in audit["candidates"] if item["seed_id"] == "S30_count_threshold_30d")

    assert seed["suggested_variables"]["key_col"] == "SUBSCRIPTIONS_Product_Id"
    # The seed declares a fixed `COUNT_ALL(...) <= {threshold}` comparison and the
    # request states `<= 4`, so the threshold resolves deterministically.
    assert seed["suggested_variables"]["threshold"] == 4


def test_count_threshold_seed_is_eligible_despite_window_unit_difference():
    audit = build_seed_audit(
        COUNT_THRESHOLD_SLOTS,
        client="omantel",
        columns=COUNT_THRESHOLD_COLUMNS,
        table="Subscriptions",
    )
    seed = next(item for item in audit["candidates"] if item["seed_id"] == "S30_count_threshold_30d")

    assert seed["eligible"], seed["gate_failures"]
    assert not any("time_unit_mismatch" in failure for failure in seed["gate_failures"])
    assert not any("missing_required_variables" in failure for failure in seed["gate_failures"])
    # The seed's 30-day window differs from the requested week; that is a
    # re-parameterisation the agent performs, not a disqualification.
    assert any("time_unit_adaptation" in note for note in seed["adaptations"])
    assert seed["matched_phrases"]


def test_every_seed_variable_is_one_the_system_knows_about():
    """Guards the bug class that hid `key_col` and `threshold` for so long.

    A seed whose template needs a variable nobody can fill or defer is
    unusable, and nothing announces it. This fails the moment a new seed
    introduces an unknown role name.
    """
    from vp_agent.data import load_seed_catalog
    from vp_agent.tools.seed import KNOWN_TEMPLATE_ROLES, _required_variables

    unknown: dict[str, list[str]] = {}
    for seed in load_seed_catalog().get("seeds", []):
        for variable in _required_variables(seed):
            if variable not in KNOWN_TEMPLATE_ROLES:
                unknown.setdefault(variable, []).append(str(seed.get("seed_id")))

    assert not unknown, f"seed variables with no resolver role or declared deferral: {unknown}"


def test_identifier_roles_do_not_resolve_to_the_metric_column():
    from vp_agent.tools.seed import _infer_column

    columns = [
        {"feature_name": "SUBSCRIPTIONS_Revenue", "group_name": "Subscriptions", "data_type": "numeric"},
        {"feature_name": "SUBSCRIPTIONS_Product_Id", "group_name": "Subscriptions", "data_type": "string"},
    ]

    assert _infer_column(columns, "kpi_col", "Subscriptions") == "SUBSCRIPTIONS_Revenue"
    for role in ("grp_col", "key_col", "id_col"):
        assert _infer_column(columns, role, "Subscriptions") == "SUBSCRIPTIONS_Product_Id"


def test_360_single_seed_is_reported_as_by_design_not_a_shortage():
    slots = {
        "domain": "recharge",
        "kpi_phrase": "recharge amount",
        "time_token": "30D",
        "operator": ">",
        "value": "5",
    }
    columns = [
        {"feature_name": "CUST_360_RECHARGE_AMOUNT_30D", "group_name": "360_PROFILE", "data_type": "numeric"}
    ]

    supply = select_seed(slots, client="omantel", columns=columns, table="360_PROFILE")["supply"]

    # Exactly one seed is permitted on the snapshot path, so the "weak evidence"
    # advisory must not fire and mislead the agent into ignoring it.
    assert supply["eligible"] == 1
    assert "advisory" not in supply
    assert "360 snapshot path" in supply["note"]


def test_seed_selection_reports_candidate_supply():
    result = select_seed(
        COUNT_THRESHOLD_SLOTS,
        client="omantel",
        columns=COUNT_THRESHOLD_COLUMNS,
        table="Subscriptions",
    )

    supply = result["supply"]
    assert supply["seeds_considered"] > 1
    assert supply["eligible"] > 1
    assert supply["eligible_needing_adaptation"] >= 1
    # More than one option survived, so the thin-supply advisory stays silent.
    assert "advisory" not in supply
    assert result["alternatives"]
    assert all("adaptations" in item for item in result["alternatives"])


def test_validate_warns_when_a_stated_number_is_not_rendered():
    result = validate_rule(
        COUNT_THRESHOLD_RULE,
        request="To select customers who purchased any product at most 4 times in the last week",
    )

    assert result["ok"], result["errors"]
    coverage = [item for item in result["warnings"] if item.get("class") == "coverage"]
    assert coverage
    assert coverage[0]["numbers"] == ["4"]


def _intent_cues(rule: str, request: str) -> set[str]:
    from vp_agent.tools.validate import intent_cue_warnings

    return {item["cue"] for item in intent_cue_warnings(rule, request)}


def test_intent_sweep_flags_missing_negation():
    rule = "L_PROMO_SENT_DATE >= CurrentTime-7DAYS AND L_ACTION_KEY ${operator} ${value} AND COUNT_ALL(L_AGG_MSISDN) > 0"

    assert "negation" in _intent_cues(rule, "customers who did not receive the promo last week")
    # The same rule shape with `= 0` satisfies the cue.
    assert "negation" not in _intent_cues(
        rule.replace("> 0", "= 0"),
        "customers who did not receive the promo last week",
    )


def test_intent_sweep_flags_missing_groupby_but_ignores_per_month():
    rule = "SUBSCRIPTIONS_DT >= CurrentMonth-1MONTHS AND SUM(SUBSCRIPTIONS_Revenue) ${operator} ${value}"
    request = "total revenue per product in the last month"

    assert "per_entity" in _intent_cues(rule, request)
    grouped = rule.replace(
        "SUM(SUBSCRIPTIONS_Revenue)",
        "SUM(SUBSCRIPTIONS_Revenue)__groupby_SUBSCRIPTIONS_Product_Id",
    )
    assert "per_entity" not in _intent_cues(grouped, request)
    # "per month" is a period and "per customer" is the default grain.
    assert "per_entity" not in _intent_cues(rule, "total revenue per month per customer")


def test_intent_sweep_flags_missing_average_and_alternation():
    summed = "COMMON_Event_Date >= CurrentWeek-4WEEKS AND SUM(COMMON_OG_Call_Revenue) ${operator} ${value}"
    assert "average" in _intent_cues(summed, "average weekly revenue from outgoing calls over 4 weeks")

    averaged = summed.replace("SUM(COMMON_OG_Call_Revenue)", "SUM(V{A}=f{COMMON_OG_Call_Revenue/4})")
    assert "average" not in _intent_cues(averaged, "average weekly revenue from outgoing calls over 4 weeks")

    anded = 'Profile_Cdr_Handset_Type = "smartphone" AND CUST_360_REVENUE_30D ${operator} ${value}'
    assert "alternation" in _intent_cues(anded, "customers using a smartphone or iPhone")
    # "or more" is a comparison phrase, not a list of alternatives.
    assert "alternation" not in _intent_cues(anded, "customers who recharged 5 or more times")


def test_intent_sweep_flags_dropped_service_scope():
    generic = "COMMON_Event_Date >= CurrentTime-2DAYS AND SUM(Total_Voice_Revenue) ${operator} ${value}"
    scoped = generic.replace("Total_Voice_Revenue", "COMMON_OG_IDD_Call_Revenue")
    request = "revenue from international outgoing calls in the last 2 days"

    assert "scope" in _intent_cues(generic, request)
    assert "scope" not in _intent_cues(scoped, request)


def test_intent_sweep_flags_an_event_type_folded_into_the_kpi_phrase():
    request = "Find customers who got a any promotion within the last 4 days."
    dropped = "L_SENT_DATE >= CurrentTime-4DAYS AND COUNT_ALL(L_AGG_CNT) ${operator} ${value}"
    kept = (
        "L_SENT_DATE >= CurrentTime-4DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND COUNT_ALL(L_AGG_CNT) ${operator} ${value}"
    )

    assert "category_value" in _intent_cues(dropped, request)
    assert "category_value" not in _intent_cues(kept, request)


def test_category_value_check_accepts_a_different_column_for_the_same_concept():
    from vp_agent.tools.validate import dropped_category_values

    # Production mines "smartphone" onto Profile_Cdr_Handset_Type, but a 360
    # rule expresses the same constraint on a differently named column.
    rule = 'CUST_360_HANDSET_TYPE = "SP" AND CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}'

    assert dropped_category_values(rule, "smartphone customers who recharged in the last 30 days") == []


def test_intent_sweep_is_quiet_on_a_plain_request():
    rule = 'CUST_360_HANDSET_TYPE = "SP" AND CUST_360_RECHARGE_AMOUNT_30D ${operator} ${value}'

    assert _intent_cues(rule, "smartphone customers who recharged in the last 30 days") == set()


def test_validate_surfaces_intent_warnings_alongside_errors():
    result = validate_rule(
        "SUBSCRIPTIONS_DT >= CurrentMonth-1MONTHS AND SUM(SUBSCRIPTIONS_Revenue) ${operator} ${value}",
        request="total product subscription revenue per product in the last month",
    )

    assert result["ok"], result["errors"]
    assert any(item.get("cue") == "per_entity" for item in result["warnings"] if item.get("class") == "intent")


def test_validate_warns_when_production_uses_a_different_shape():
    result = validate_rule(
        COUNT_THRESHOLD_RULE,
        request="To select customers who purchased any product at most 4 times in the last week",
        table="Subscriptions",
        client="omantel",
    )

    convention = [item for item in result["warnings"] if item.get("class") == "convention"]
    assert convention
    assert all("SUBSCRIPTIONS_Product_Id" in item["shared_columns"] for item in convention)
    assert any(
        "production keeps a literal aggregate threshold" in difference
        for item in convention
        for difference in item["differences"]
    )


def test_validate_production_comparison_is_skipped_without_client():
    result = validate_rule(COUNT_THRESHOLD_RULE, request="purchased any product")

    assert not [item for item in result["warnings"] if item.get("class") == "convention"]


def test_validate_accepts_groupby_aggregate_and_skips_unrelated_production_vps():
    rule = (
        "SUBSCRIPTIONS_DT >= CurrentMonth-1MONTHS AND SUBSCRIPTIONS_DT < CurrentMonth "
        "AND SUM(SUBSCRIPTIONS_Revenue)__groupby_SUBSCRIPTIONS_Product_Id ${operator} ${value}"
    )

    result = validate_rule(
        rule,
        request="product subscriptions whose total revenue per product in the last month is greater than a specified value",
        table="Subscriptions",
        client="omantel",
    )

    assert result["ok"], result["errors"]
    # The comparison keys on the aggregated column, so count-threshold VPs that
    # merely share a filter column must not raise a convention warning.
    assert not [item for item in result["warnings"] if item.get("class") == "convention"]


def test_normalize_prefers_measure_word_over_product_noun():
    # Golden case: "total data bundle revenue ... last 1 months" is a revenue
    # KPI, not a subscription request. The bare `bundle` keyword used to route
    # it to the subscription domain, which reweights retrieval groups.
    slots = normalize_slots("total data bundle revenue of a customer for the last 1 months", client="omantel")

    assert slots["domain"] == "usage"
    assert slots["time_token"] == "M1"


def test_normalize_never_raises_clarification_from_the_regex_pass():
    slots = normalize_slots("customers with an unparseable widget metric", client="omantel")

    assert slots["needs_clarification"] is False
    assert "domain" in slots["missing"]


def test_model_facing_slots_withhold_semantic_fields():
    from vp_agent.tools.normalize import model_facing_slots

    parsed = normalize_slots(
        "Prepaid smartphone customers whose total data revenue in the last 2 weeks is more than a given value",
        client="omantel",
    )
    view = model_facing_slots(parsed)

    assert view["operator"] == ">"
    assert view["value"] == ""
    assert view["time_token"] == "2W"
    assert len(view["filters"]) == 2
    for withheld in ("domain", "kpi_phrase", "aggregate", "needs_clarification"):
        assert withheld not in view
    assert "domain" in view["not_parsed"]
    assert "kpi_phrase" in view["not_parsed"]


def test_syntax_coverage_audit_detects_constructs_and_documentation():
    import sys

    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from audit_syntax_coverage import audit

    report = audit("omantel")
    by_name = {item["construct"]: item for item in report["constructs"]}

    assert report["vps_scanned"] > 100
    # Detection works against real production syntax.
    assert by_name["not_null_guard"]["vp_count"] > 0
    assert by_name["max_date_guard"]["vp_count"] > 0
    # Documentation lookup works: groupby was documented, so it must read as such.
    assert by_name["groupby_single"]["documented"] is True
    # Production contains rules validate_rule would reject outright.
    assert report["placeholder_rule_outliers"]["count"] > 0


def test_normalize_distinguishes_rolling_week_from_calendar_week():
    calendar = normalize_slots("customers who purchased any product in the last week", client="omantel")
    rolling = normalize_slots(
        "customers who purchased any product in the last week from today",
        client="omantel",
    )

    assert calendar["time_token"] == "W1"
    assert rolling["time_token"] == "7D"


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


def test_count_all_over_a_categorical_column_is_an_error():
    # B1: kpi_meta holds 109 categorical columns and production counts none of
    # them, yet the rule counted the promotion filter itself.
    from vp_agent.tools.validate import aggregate_shape_errors

    rule = (
        "L_SENT_DATE >= CurrentTime-4DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND L_ACTION_KEY ${operator} ${value} AND COUNT_ALL(LC_ACTION_TYPE) > 0"
    )

    assert [e["class"] for e in aggregate_shape_errors(rule)] == ["aggregate"]
    assert "categorical" in aggregate_shape_errors(rule)[0]["message"]


def test_grouping_by_the_counted_column_is_an_error():
    # B5. Zero of 44 production groupby usages name the aggregated column.
    from vp_agent.tools.validate import aggregate_shape_errors

    rule = (
        "L_SENT_DATE >= CurrentTime-2DAYS AND "
        "COUNT_ALL(L_AGG_MSISDN)__groupby_L_AGG_MSISDN ${operator} ${value}"
    )

    assert any("groups by the column it aggregates" in e["message"] for e in aggregate_shape_errors(rule))


def test_the_expected_promo_rule_passes_the_aggregate_checks():
    from vp_agent.tools.validate import aggregate_shape_errors

    rule = (
        "L_PROMO_SENT_DATE >= CurrentTime-2DAYS AND L_ACTION_KEY ${operator} ${value} "
        "AND COUNT_ALL(L_AGG_MSISDN)__groupby_L_ACTION_KEY > 0 AND Max(L_PROMO_SENT_DATE) <> NULL"
    )

    assert aggregate_shape_errors(rule) == []


def test_seed_slot_coverage_notices_a_column_doing_two_jobs():
    # S39 needs four distinct columns; the run supplied three.
    from vp_agent.tools.validate import seed_slot_coverage

    template = (
        "{date_col} >= CurrentTime-{N}DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND {key_col} ${operator} ${value} AND COUNT_ALL({count_col}) > 0"
    )
    short = (
        "L_SENT_DATE >= CurrentTime-4DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND L_ACTION_KEY ${operator} ${value} AND COUNT_ALL(LC_ACTION_TYPE) > 0"
    )
    complete = short.replace("COUNT_ALL(LC_ACTION_TYPE)", "COUNT_ALL(L_AGG_MSISDN)")

    assert [item["clause"] for item in seed_slot_coverage(short, template)] == ["slot_coverage"]
    assert seed_slot_coverage(complete, template) == []


def test_new_aggregate_checks_do_not_fire_on_real_production_rules():
    # The guard against another over-eager check: 713 client VPs, zero errors.
    import csv
    import glob

    from vp_agent.config import load_settings
    from vp_agent.tools.validate import aggregate_shape_errors

    conditions = [
        row.get("PARENT_CONDITION") or ""
        for path in glob.glob(str(load_settings().data_dir / "vpdesc-all-*.csv"))
        for row in csv.DictReader(open(path))
    ]

    assert conditions
    assert [c for c in conditions if aggregate_shape_errors(c)] == []


def test_convention_check_ignores_rules_that_merely_share_the_counted_column():
    # B2: this rule was correct on the first render. It was compared against the
    # positive per-action-key presence family purely because both count
    # L_AGG_MSISDN, told it was missing a Max(...) <> NULL guard, and edited
    # until the warnings hit zero.
    from vp_agent.tools.validate import production_shape_differences

    correct = (
        "L_SENT_DATE >= CurrentTime-4DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND LC_SEGMENT_NAME ${operator} ${value} AND COUNT_ALL(L_AGG_MSISDN) = 0"
    )

    assert production_shape_differences(correct, "omantel") == []


def test_convention_check_still_reports_a_genuinely_comparable_rule():
    from vp_agent.tools.validate import production_shape_differences

    misplaced_pair = (
        "L_SENT_DATE >= CurrentTime-2DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND COUNT_ALL(L_AGG_MSISDN)__groupby_L_AGG_MSISDN ${operator} ${value} "
        "AND Max(L_SENT_DATE) <> NULL"
    )

    findings = production_shape_differences(misplaced_pair, "omantel")
    assert findings
    assert all(item["column_overlap"] >= 0.5 for item in findings)
    assert any("puts ${operator} ${value} on the column" in d for item in findings for d in item["differences"])


def test_production_role_usage_separates_selector_from_counted_column():
    # Both are strings, so data type cannot separate them; the role they play in
    # production can.
    from vp_agent.tools.retrieval_index import client_role_usage

    usage = client_role_usage("omantel")

    assert usage["aggregated"]["L_AGG_MSISDN"] > usage["aggregated"].get("L_ACTION_KEY", 0)
    assert usage["pair_owner"]["L_ACTION_KEY"] > usage["pair_owner"].get("L_AGG_MSISDN", 0)


def test_seed_resolver_gives_distinct_roles_distinct_columns():
    # B5: key_col and count_col both resolved to L_AGG_MSISDN, which rendered as
    # COUNT_ALL(L_AGG_MSISDN)__groupby_L_AGG_MSISDN.
    from vp_agent.tools.seed import select_seed

    result = select_seed(
        slots={
            "domain": "lifecycle",
            "kpi_phrase": "promotional send count",
            "time_token": "2D",
            "operator": ">",
            "value": "",
            "aggregate": "COUNT_ALL",
        },
        client="omantel",
        table="LIFECYCLE_CDR",
        columns=[
            {"feature_name": "L_AGG_MSISDN"},
            {"feature_name": "L_ACTION_KEY"},
            {"feature_name": "LC_ACTION_TYPE"},
        ],
    )
    variables = (result.get("proposed_selected_seed") or {}).get("suggested_variables") or {}

    assert variables.get("key_col") == "L_ACTION_KEY"
    assert variables.get("count_col") == "L_AGG_MSISDN"
    columns = [value for name, value in variables.items() if name.endswith("_col")]
    assert len(columns) == len(set(columns)), variables


def test_seed_resolver_leaves_a_role_unfilled_rather_than_guessing_a_bad_column():
    # Only a categorical is left for count_col; production counts none, and
    # validate_rule now rejects it, so an unfilled variable is the honest answer.
    from vp_agent.tools.seed import select_seed

    result = select_seed(
        slots={"domain": "lifecycle", "kpi_phrase": "promotion received", "time_token": "4D", "aggregate": "COUNT"},
        client="omantel",
        table="LIFECYCLE_CDR",
        columns=[{"feature_name": "LC_ACTION_TYPE"}, {"feature_name": "L_ACTION_KEY"}],
    )
    variables = (result.get("proposed_selected_seed") or {}).get("suggested_variables") or {}

    assert variables.get("key_col") == "L_ACTION_KEY"
    assert "count_col" not in variables


def test_count_threshold_does_not_make_the_metric_column_numeric():
    # B5 retest: the agent sent operator ">" value 0 for COUNT_ALL(...) > 0.
    # Retrieval read that as "the metric column should be numeric", boosted every
    # *_count column, and L_AGG_MSISDN never reached the page.
    from vp_agent.tools.retrieve import build_retrieval_audit, compact_retrieval_page

    slots = {
        "kpi_phrase": "count of customers",
        "time_token": "2D",
        "domain": "lifecycle",
        "operator": ">",
        "value": 0,
        "aggregate": "COUNT_ALL",
        "filters": [{"phrase": "promotion action type", "operator": "IN LIST", "value": ["Promotion"]}],
    }
    page = compact_retrieval_page(build_retrieval_audit(slots, "omantel"), audit_id="count")
    names = [item["feature_name"] for item in page["metric_candidates"]]

    assert "L_AGG_MSISDN" in names, names
    assert all(item["data_type"] != "numeric" for item in page["metric_candidates"])


def test_count_all_over_numeric_or_date_is_an_error():
    # 170 production COUNT_ALL usages, every one a string identifier.
    from vp_agent.tools.validate import aggregate_shape_errors

    assert aggregate_shape_errors("COUNT_ALL(Recharge_count) > 0")
    assert aggregate_shape_errors("COUNT_ALL(L_PROMO_SENT_DATE) > 0")
    assert aggregate_shape_errors("COUNT_ALL(L_AGG_MSISDN) > 0") == []


def test_spaced_date_anchor_is_rejected():
    # 660 production date anchors, none spaced.
    from vp_agent.tools.validate import validate_rule

    spaced = "L_PROMO_SENT_DATE >= CurrentTime - 2DAYS AND L_ACTION_KEY ${operator} ${value}"
    tight = "L_PROMO_SENT_DATE >= CurrentTime-2DAYS AND L_ACTION_KEY ${operator} ${value}"

    assert not validate_rule(spaced, request="x", client="omantel")["ok"]
    assert validate_rule(tight, request="x", client="omantel")["ok"]


def test_count_col_is_never_resolved_to_a_date_column():
    # B5 retest: count_col resolved to L_PROMO_SENT_DATE.
    from vp_agent.tools.seed import select_seed

    result = select_seed(
        slots={"domain": "lifecycle", "kpi_phrase": "promo sent count", "time_token": "2D", "aggregate": "COUNT_ALL"},
        client="omantel",
        table="LIFECYCLE_CDR",
        columns=[
            {"feature_name": "L_AGG_CNT"},
            {"feature_name": "LC_ACTION_TYPE"},
            {"feature_name": "L_PROMO_SENT_DATE"},
        ],
    )
    variables = (result.get("proposed_selected_seed") or {}).get("suggested_variables") or {}

    assert variables.get("count_col") != "L_PROMO_SENT_DATE"


def test_selector_slot_refuses_a_column_production_only_ever_counts():
    # C1/C2/C5: key_col resolved to L_AGG_MSISDN, which production counts 50
    # times and never uses as a selector, leaving count_col empty.
    from vp_agent.tools.seed import select_seed

    result = select_seed(
        slots={
            "domain": "lifecycle",
            "kpi_phrase": "customers who received bonuses",
            "aggregate": "COUNT_ALL",
            "time_token": "30D",
            "operator": "<",
            "value": 3,
        },
        client="omantel",
        table="LIFECYCLE_CDR",
        columns=[{"feature_name": "L_AGG_MSISDN"}, {"feature_name": "LC_ACTION_TYPE"}],
    )
    selected = result.get("proposed_selected_seed") or {}
    variables = selected.get("suggested_variables") or {}

    assert variables.get("count_col") == "L_AGG_MSISDN"
    assert "key_col" not in variables

    hint = selected.get("selector_hint") or {}
    assert hint.get("unfilled_roles") == ["key_col"]
    assert [item["column"] for item in hint["production_selectors"]][0] == "L_ACTION_KEY"


def test_selector_hint_is_absent_when_the_slot_is_filled():
    from vp_agent.tools.seed import select_seed

    result = select_seed(
        slots={
            "domain": "lifecycle",
            "kpi_phrase": "customers who received bonuses",
            "aggregate": "COUNT_ALL",
            "time_token": "30D",
            "operator": "<",
            "value": 3,
        },
        client="omantel",
        table="LIFECYCLE_CDR",
        columns=[
            {"feature_name": "L_AGG_MSISDN"},
            {"feature_name": "LC_ACTION_TYPE"},
            {"feature_name": "L_BONUS_ACTION_KEY"},
        ],
    )
    selected = result.get("proposed_selected_seed") or {}

    assert (selected.get("suggested_variables") or {}).get("key_col") == "L_BONUS_ACTION_KEY"
    assert "selector_hint" not in selected


def test_a_stated_count_limit_is_not_reported_as_deferrable():
    from vp_agent.tools.validate import validate_rule

    rule = (
        "L_SENT_DATE >= CurrentTime-30DAYS AND LC_ACTION_TYPE IN LIST (BONUS;Bonus;bonus) "
        "AND COUNT_ALL(L_AGG_MSISDN) ${operator} ${value}"
    )
    stated = validate_rule(
        rule,
        request="Find customers who have received fewer than 3 bonuses in the last 30 days.",
        client="omantel",
    )
    coverage = [w for w in stated["warnings"] if w["class"] == "coverage"]

    assert coverage
    assert "cannot be deferred" in coverage[0]["message"]
    assert "fewer than 3" in coverage[0]["message"]


def test_an_unstated_threshold_still_reads_as_deferrable():
    from vp_agent.tools.validate import validate_rule

    result = validate_rule(
        "COMMON_Event_Date >= CurrentMonth-1MONTHS AND SUM(COMMON_Total_Revenue) ${operator} ${value}",
        request="Find customers whose revenue in the last month is greater than a specified value",
        client="omantel",
    )

    assert [w for w in result["warnings"] if w["class"] == "coverage"] == []


def test_a_fully_specified_rule_is_legal_with_a_warning():
    # C5 rendered this first and it was correct; "exactly one pair" rejected it,
    # so the agent deleted the 3 to make room for the placeholders.
    from vp_agent.tools.validate import validate_rule

    rule = (
        "L_SENT_DATE >= CurrentTime-1DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND COUNT_ALL(L_AGG_MSISDN) = 3"
    )
    result = validate_rule(rule, request="Find customers who received a promotion exactly 3 times today.", client="omantel")

    assert result["ok"], result["errors"]
    assert any(w["class"] == "render" and "no runtime" in w["message"] for w in result["warnings"])


def test_placeholder_pair_errors_that_must_survive():
    from vp_agent.tools.validate import validate_rule

    for rule in (
        "A ${operator} ${value} AND B ${operator} ${value}",   # two pairs
        "COUNT_ALL(X) < ${value}",                              # half a pair
        "A ${operator} AND B ${value}",                         # split pair
    ):
        assert not validate_rule(rule, request="x", client="omantel")["ok"], rule


def test_every_pair_less_production_rule_now_validates():
    import csv
    import glob

    from vp_agent.config import load_settings
    from vp_agent.tools.validate import validate_rule

    pair_less = [
        row.get("PARENT_CONDITION") or ""
        for path in glob.glob(str(load_settings().data_dir / "vpdesc-all-*.csv"))
        for row in csv.DictReader(open(path))
        if (row.get("PARENT_CONDITION") or "").strip() and "${operator}" not in (row.get("PARENT_CONDITION") or "")
    ]

    assert len(pair_less) >= 20, len(pair_less)
    rejected = [c for c in pair_less if validate_rule(c, request="x", client="omantel")["errors"]]
    assert rejected == [], rejected[:3]


def test_control_group_disjunction_counts_as_one_runtime_pair():
    # C3/C4: the expected answers repeat the pair with a _CG suffix, and the
    # placeholder count rejected them as "two pairs".
    from vp_agent.tools.validate import validate_rule

    for rule in (
        "(L_ACTION_KEY ${operator} ${value} OR L_ACTION_KEY ${operator} ${value}_CG) "
        "AND COUNT_ALL(L_AGG_CNT)__groupby_L_ACTION_KEY > 0",
        "(L_BONUS_ACTION_KEY ${operator} ${value} OR L_BONUS_ACTION_KEY ${operator} ${value}_CG) "
        "AND COUNT_ALL(L_AGG_CNT)__groupby_L_BONUS_ACTION_KEY = 0",
    ):
        result = validate_rule(rule, request="x", client="omantel")
        assert result["ok"], result["errors"]


def test_two_unrelated_pairs_are_still_rejected():
    from vp_agent.tools.validate import validate_rule

    # Two different columns is not the control-group shape.
    assert not validate_rule("A ${operator} ${value} AND B ${operator} ${value}", request="x", client="omantel")["ok"]
    assert not validate_rule(
        "(A ${operator} ${value} OR B ${operator} ${value}_CG) AND COUNT_ALL(X) > 0",
        request="x",
        client="omantel",
    )["ok"]


def test_control_group_wording_finds_the_production_family():
    # The four rules that define the pattern have no "control" or "group" in
    # their names; the only token in the data is "cg".
    from vp_agent.tools.retrieve_vps import retrieve_existing_vps

    names = [c["name"] for c in retrieve_existing_vps("promotion or its control-group variant", "omantel", top_k=6)]

    assert "L_AK_PROMO_COUNT" in names, names
    assert any(n.startswith("L_AK_") for n in names[:3]), names


def test_runtime_variables_are_not_reported_as_unknown_columns():
    # D1/D2: `$L_PROMO_SENT_DATE` and `$OM_MSISDN` are runtime variables the
    # engine substitutes per subscriber, not kpi_meta columns.
    from vp_agent.tools.validate import validate_rule

    def column_warnings(rule):
        return [w["tokens"] for w in validate_rule(rule, request="x", client="omantel")["warnings"] if w["class"] == "column"]

    assert column_warnings("L_MSISDN = $OM_MSISDN AND COUNT_ALL(L_AGG_CNT) > 0") == []
    assert (
        column_warnings(
            "created_date >= $L_PROMO_SENT_DATE AND created_date <= $L_PROMO_SENT_DATE+7DAYS "
            "AND SUM(I_RECHARGE_AMOUNT) ${operator} ${value}"
        )
        == []
    )
    # A genuine unknown column must still be reported.
    assert column_warnings("L_MSISDN = $OM_MSISDN AND COUNT_ALL(L_AGG_TYPOO) > 0") == [["L_AGG_TYPOO"]]


def test_post_promo_recharge_rules_validate():
    from vp_agent.tools.validate import validate_rule

    for rule in (
        "created_date >= $L_PROMO_SENT_DATE AND SUM(I_RECHARGE_AMOUNT) ${operator} ${value}",
        "created_date >= $L_PROMO_SENT_DATE AND created_date <= $L_PROMO_SENT_DATE+7DAYS "
        "AND SUM(I_RECHARGE_AMOUNT) ${operator} ${value}",
    ):
        assert validate_rule(rule, request="recharged after the promo was sent", client="omantel")["ok"]


def test_zero_pair_warning_names_the_selector_production_uses():
    # D3 shipped a pair-less rule with only "confirm the fixed form" to go on,
    # because select_seed — which carries the same suggestion — was never called.
    from vp_agent.tools.validate import validate_rule

    rule = (
        "L_PROMO_SENT_DATE >= CurrentTime-${X}DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND COUNT_ALL(L_AGG_MSISDN) > 0"
    )
    warning = next(
        w for w in validate_rule(rule, request="x", client="omantel")["warnings"] if w["class"] == "render"
    )

    assert "L_ACTION_KEY" in warning["message"]
    assert "29 rules" in warning["message"]


def test_zero_pair_warning_degrades_without_a_client():
    from vp_agent.tools.validate import validate_rule

    warning = next(
        w for w in validate_rule("COUNT_ALL(L_AGG_MSISDN) > 0", request="x")["warnings"] if w["class"] == "render"
    )

    assert "Confirm the fixed form" in warning["message"]


def test_rendering_an_aggregate_without_select_seed_draws_an_advisory():
    # D3 rendered a counted lifecycle rule having never called select_seed, so
    # the seed catalog's selector suggestion was never seen.
    import json as _json

    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    condition = "COMMON_Event_Date >= CurrentTime-7DAYS AND SUM(COMMON_Total_Revenue) ${operator} ${value}"

    def run(state):
        hook = make_hooks(state)["PostToolUse"][0].hooks[0]
        return asyncio.run(
            hook(
                {
                    "tool_name": "mcp__vp__render_condition",
                    "tool_input": {"client": "omantel"},
                    "tool_response": [{"type": "text", "text": _json.dumps({"parent_condition": condition})}],
                },
                "tool-1",
                {"signal": None},
            )
        )

    without = run(ToolState())
    assert "select_seed was never called" in _json.dumps(without)

    with_seed = run(ToolState(tools_called={"mcp__vp__select_seed"}))
    assert "select_seed was never called" not in _json.dumps(with_seed)


def test_any_scope_is_not_told_to_add_a_selector():
    # "any promotion" is scoped by the event-type filter alone; adding a selector
    # would narrow it to one promotion the marketer picks.
    from vp_agent.tools.validate import validate_rule

    rule = (
        "L_PROMO_SENT_DATE >= CurrentTime-6DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion) "
        "AND COUNT_ALL(L_AGG_MSISDN) > 0"
    )

    def render_warning(request):
        return next(w for w in validate_rule(rule, request=request, client="omantel")["warnings"] if w["class"] == "render")

    any_scope = render_warning("Find customers who got any promotion delivered in the last 6 days.")
    assert "do not add a selector" in any_scope["message"]
    assert "L_ACTION_KEY" not in any_scope["message"]

    particular = render_warning("Find customers who got a particular promotion delivered in the last 6 days.")
    assert "L_ACTION_KEY" in particular["message"]

    # "based on segment name" names a selector even though "any" appears nearby.
    scoped = render_warning("Find customers who did not get any promotion in the last 4 days, based on segment name.")
    assert "L_ACTION_KEY" in scoped["message"] or "LC_SEGMENT_NAME" in scoped["message"]


def test_retrieval_without_an_aggregate_is_flagged():
    # D4 sent slots with no `aggregate`, so the metric gate never ran and a
    # categorical column ranked first for "what should I count?".
    import json as _json

    from vp_agent.hooks import make_hooks
    from vp_agent.schemas import ToolState

    def run(slots):
        state = ToolState(request="Find customers who were not delivered a promotion in the last 180 days.")
        hook = make_hooks(state)["PostToolUse"][0].hooks[0]
        return _json.dumps(
            asyncio.run(
                hook(
                    {
                        "tool_name": "mcp__vp__retrieve_columns",
                        "tool_input": {"slots": slots},
                        "tool_response": [{"type": "text", "text": _json.dumps({"audit_id": "a"})}],
                    },
                    "tool-1",
                    {"signal": None},
                )
            )
        )

    base = {"kpi_phrase": "customers promotion delivered", "time_token": "180D", "domain": "lifecycle"}
    assert "declare no `aggregate`" in run(base)
    assert "declare no `aggregate`" not in run(dict(base, aggregate="COUNT_ALL"))
    assert "declare no `aggregate`" not in run(dict(base, formula={"type": "percentage_of_kpi"}))


def test_a_date_column_never_fills_the_selector_slot():
    # D4 resolved key_col to L_SENT_DATE because one production rule groups by it.
    from vp_agent.tools.seed import select_seed

    variables = (
        select_seed(
            slots={"domain": "lifecycle", "kpi_phrase": "promotion delivered", "time_token": "180D", "aggregate": "COUNT_ALL"},
            client="omantel",
            table="LIFECYCLE_CDR",
            columns=[{"feature_name": "LC_ACTION_TYPE"}, {"feature_name": "L_SENT_DATE"}],
        ).get("proposed_selected_seed")
        or {}
    ).get("suggested_variables") or {}

    assert variables.get("key_col") != "L_SENT_DATE"
    assert "key_col" not in variables


def test_a_parameterised_window_is_not_reported_as_no_window():
    # D5: "a specified number of days" parsed as time_token "none", which made a
    # seed gate delete the one template written for the request.
    from vp_agent.tools.normalize import normalize_slots

    def token(sentence):
        return normalize_slots(sentence, client="omantel")["time_token"]

    assert token("Find customers who received fewer than a specified number of bonuses in a specified number of days.") == "PARAM"
    assert token("Find customers who got a promotion delivered in the last X days.") == "PARAM"
    assert token("bonus less than N times in last X days") == "PARAM"
    # Concrete and absent windows are unchanged.
    assert token("Find customers who recharged in the last 30 days.") == "30D"
    assert token("Find customers with high revenue") == "none"


def test_a_seed_window_the_request_did_not_ask_for_is_an_adaptation_not_a_gate():
    # S86_parameterized_bonus_sent matched the request's own phrase and was
    # dropped because the window had been parsed as absent.
    from vp_agent.tools.seed import build_seed_audit

    audit = build_seed_audit(
        {
            "domain": "lifecycle",
            "kpi_phrase": "customers who received bonuses",
            "time_token": "none",
            "operator": "<",
            "aggregate": "COUNT",
            "raw_request": "Find customers who received fewer than a specified number of bonuses.",
        },
        client="omantel",
        table="LIFECYCLE_CDR",
        columns=[{"feature_name": "L_AGG_MSISDN"}, {"feature_name": "L_BONUS_ACTION_KEY"}],
    )
    seed = next(c for c in audit["candidates"] if c["seed_id"] == "S86_parameterized_bonus_sent")

    assert "time_window_not_requested" not in seed["gate_failures"]
    assert seed["eligible"]
    assert any("time_window_not_requested" in note for note in seed["adaptations"])


def test_named_placeholders_do_not_count_toward_the_pair_rule():
    from vp_agent.tools.validate import validate_rule

    rule = (
        "L_BONUS_SENT_DATE >= CurrentTime-${NoOfDays}DAYS AND L_BONUS_ACTION_KEY ${operator} ${value} "
        "AND COUNT_ALL(L_AGG_MSISDN) < ${NoOfBonus}"
    )
    result = validate_rule(rule, request="fewer than a specified number of bonuses", client="omantel")

    assert result["ok"], result["errors"]
    assert not [w for w in result["warnings"] if w["class"] == "column"]
