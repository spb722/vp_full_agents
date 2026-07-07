from __future__ import annotations

from typing import Any

from vp_agent.tools.memory import episodic_lookup as episodic_lookup_core
from vp_agent.tools.memory import queue_correction as queue_correction_core
from vp_agent.tools.normalize import normalize_slots as normalize_slots_core
from vp_agent.tools.plan import build_condition_plan as build_condition_plan_core
from vp_agent.tools.render import render_condition as render_condition_core
from vp_agent.tools.retrieve import retrieve_columns as retrieve_columns_core
from vp_agent.tools.router import route_table as route_table_core
from vp_agent.tools.seed import select_seed as select_seed_core
from vp_agent.tools.shelf import shelf_lookup as shelf_lookup_core
from vp_agent.tools.validate import validate_rule as validate_rule_core


def _text_result(data: Any) -> dict[str, Any]:
    import json

    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, sort_keys=True)}],
        "structuredContent": data,
    }


def create_vp_server():
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool(
        "normalize_slots",
        "Normalize a raw marketer sentence into deterministic VP slots for common telecom audience requests.",
        {
            "type": "object",
            "properties": {
                "request": {"type": "string"},
                "client": {"type": "string"},
            },
            "required": ["request"],
        },
    )
    async def normalize_slots(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(normalize_slots_core(args["request"], args.get("client")))

    @tool(
        "retrieve_columns",
        "Retrieve candidate KPI/profile columns from kpi_meta for extracted VP slots.",
        {
            "type": "object",
            "properties": {
                "slots": {"type": "object"},
                "client": {"type": "string"},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["slots", "client"],
        },
    )
    async def retrieve_columns(args: dict[str, Any]) -> dict[str, Any]:
        candidates = retrieve_columns_core(
            slots=args["slots"],
            client=args["client"],
            exclude=args.get("exclude") or [],
            top_k=args.get("top_k") or 12,
        )
        return _text_result({"candidates": [candidate.__dict__ for candidate in candidates]})

    @tool(
        "shelf_lookup",
        "Check whether a single KPI/time token has a matching precomputed 360 Profile KPI.",
        {"token": str, "client": str},
    )
    async def shelf_lookup(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(shelf_lookup_core(args["token"], args["client"]))

    @tool(
        "route_table",
        "Route extracted domain/token and optional KPI group to the deterministic source table.",
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "token": {"type": "string"},
                "kpi_group": {"type": "string"},
                "shelf_on_360": {"type": "boolean"},
            },
            "required": ["domain", "token"],
        },
    )
    async def route_table(args: dict[str, Any]) -> dict[str, Any]:
        decision = route_table_core(
            domain=args["domain"],
            token=args["token"],
            kpi_group=args.get("kpi_group"),
            shelf_on_360=args.get("shelf_on_360"),
        )
        return _text_result(decision.__dict__)

    @tool(
        "select_seed",
        "Select the best VP seed/template from the seed catalog for slots, client, route table, and resolved columns.",
        {
            "type": "object",
            "properties": {
                "slots": {"type": "object"},
                "client": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "object"}},
                "table": {"type": "string"},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["slots", "client"],
        },
    )
    async def select_seed(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(
            select_seed_core(
                slots=args["slots"],
                client=args["client"],
                columns=args.get("columns") or [],
                table=args.get("table"),
                exclude=args.get("exclude") or [],
                top_k=args.get("top_k") or 5,
            )
        )

    @tool(
        "build_condition_plan",
        "Build deterministic render_condition input from extracted slots, selected columns, route, and seed. Does not emit condition syntax.",
        {
            "type": "object",
            "properties": {
                "slots": {"type": "object"},
                "client": {"type": "string"},
                "prefer_360": {"type": "boolean"},
                "force_event": {"type": "boolean"},
            },
            "required": ["slots", "client"],
        },
    )
    async def build_condition_plan(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(
            build_condition_plan_core(
                slots=args["slots"],
                client=args["client"],
                prefer_360=args.get("prefer_360", True),
                force_event=args.get("force_event", False),
            )
        )

    @tool(
        "render_condition",
        "Render the only legal PARENT_CONDITION string from a seed/template, variables, and resolved filters.",
        {
            "type": "object",
            "properties": {
                "seed_id": {"type": "string"},
                "template": {"type": "string"},
                "variables": {"type": "object"},
                "filters": {"type": "array", "items": {"type": "object"}},
                "client": {"type": "string"},
            },
            "required": ["variables", "client"],
        },
    )
    async def render_condition(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(
            render_condition_core(
                seed_id=args.get("seed_id"),
                template=args.get("template"),
                variables=args["variables"],
                filters=args.get("filters") or [],
                client=args["client"],
            )
        )

    @tool(
        "validate_rule",
        "Validate VP parent condition syntax, placeholder discipline, known columns, and coarse request coverage.",
        {"rule": str, "request": str, "table": str},
    )
    async def validate_rule(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(validate_rule_core(args["rule"], args["request"], args.get("table")))

    @tool(
        "episodic_lookup",
        "Look up reviewed prior corrections for similar slots and client.",
        {"slots": dict, "client": str},
    )
    async def episodic_lookup(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(episodic_lookup_core(args["slots"], args["client"]))

    @tool(
        "queue_correction",
        "Queue a marketer or verifier correction for human review before reinforcement.",
        {"trace_id": str, "corrected": dict},
    )
    async def queue_correction(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(queue_correction_core(args["trace_id"], args["corrected"]))

    return create_sdk_mcp_server(
        name="vp",
        version="0.1.0",
        tools=[
            normalize_slots,
            retrieve_columns,
            shelf_lookup,
            route_table,
            select_seed,
            build_condition_plan,
            render_condition,
            validate_rule,
            episodic_lookup,
            queue_correction,
        ],
    )
