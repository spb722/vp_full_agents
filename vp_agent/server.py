from __future__ import annotations

from typing import Any
import uuid

from vp_agent.observability import observation, update_observation
from vp_agent.tools.memory import episodic_lookup as episodic_lookup_core
from vp_agent.tools.memory import queue_correction as queue_correction_core
from vp_agent.tools.normalize import normalize_slots as normalize_slots_core
from vp_agent.tools.plan import build_condition_plan as build_condition_plan_core
from vp_agent.tools.render import render_condition as render_condition_core
from vp_agent.tools.retrieve import (
    build_retrieval_audit,
    compact_retrieval_page,
    serialize_retrieval_audit,
)
from vp_agent.tools.retrieve_vps import retrieve_existing_vps as retrieve_existing_vps_core
from vp_agent.tools.router import route_table as route_table_core
from vp_agent.tools.seed import build_seed_audit, compact_seed_selection, serialize_seed_audit
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

    # The MCP server is created per VP request. These stores let targeted
    # expansion reuse the original ranking without putting it in model context.
    retrieval_audits: dict[str, dict[str, Any]] = {}
    seed_audits: dict[str, dict[str, Any]] = {}

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
        "Retrieve metric, filter, and time-compatible KPI/profile candidates in one batch. Returns up to five compact candidates per role; use audit_id plus page 2 or 3 only for triggered role expansion.",
        {
            "type": "object",
            "properties": {
                "slots": {"type": "object"},
                "client": {"type": "string"},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "audit_id": {"type": "string"},
                "expand_roles": {"type": "array", "items": {"type": "string"}},
                "page": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["client"],
        },
    )
    async def retrieve_columns(args: dict[str, Any]) -> dict[str, Any]:
        audit_id = str(args.get("audit_id") or "")
        if audit_id:
            audit = retrieval_audits.get(audit_id)
            if audit is None:
                return _text_result({"audit_id": audit_id, "error": "retrieval audit is unavailable for expansion"})
            return _text_result(
                compact_retrieval_page(
                    audit,
                    audit_id=audit_id,
                    role_ids=args.get("expand_roles") or None,
                    page=args.get("page") or 2,
                )
            )

        if "slots" not in args:
            return _text_result({"error": "slots are required for initial column retrieval"})
        audit_id = uuid.uuid4().hex[:12]
        audit = build_retrieval_audit(
            slots=args["slots"],
            client=args["client"],
            exclude=args.get("exclude") or [],
        )
        retrieval_audits[audit_id] = audit
        full_audit = serialize_retrieval_audit(audit)
        with observation(
            "vp.retrieve_columns.full_audit",
            **{
                "vp.audit_id": audit_id,
                "vp.audit.kind": "column_retrieval",
                "input.value": args,
            },
        ) as span:
            update_observation(span, **{"output.value": full_audit})
        return _text_result(compact_retrieval_page(audit, audit_id=audit_id))

    @tool(
        "retrieve_existing_vps",
        "Retrieve ranked existing client VP names and definitions as evidence for reuse or composition. The agent decides whether a candidate semantically matches.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "client": {"type": "string"},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query", "client"],
        },
    )
    async def retrieve_existing_vps(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(
            {
                "candidates": retrieve_existing_vps_core(
                    query=args["query"],
                    client=args["client"],
                    exclude=args.get("exclude") or [],
                    top_k=args.get("top_k") or 12,
                )
            }
        )

    @tool(
        "record_resolution",
        "Record the agent's semantic VP resolution and evidence choices before rendering. This tool stores no business logic and does not alter the decision.",
        {
            "type": "object",
            "properties": {
                "client": {"type": "string"},
                "slots": {"type": "object"},
                "selected_columns": {"type": "array", "items": {"type": "string"}},
                "selected_vps": {"type": "array", "items": {"type": "string"}},
                "seed_id": {"type": "string"},
                "proposed_seed_id": {"type": "string"},
                "seed_overrode_proposal": {"type": "boolean"},
                "path": {"type": "string"},
                "snapshot": {"type": "boolean"},
                "dependencies": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["client", "slots"],
        },
    )
    async def record_resolution(args: dict[str, Any]) -> dict[str, Any]:
        return _text_result(
            {
                "client": args["client"],
                "slots": args["slots"],
                "selected_columns": args.get("selected_columns") or [],
                "selected_vps": args.get("selected_vps") or [],
                "seed_id": args.get("seed_id"),
                "proposed_seed_id": args.get("proposed_seed_id"),
                "seed_overrode_proposal": args.get("seed_overrode_proposal", False),
                "path": args.get("path"),
                "snapshot": args.get("snapshot"),
                "dependencies": args.get("dependencies") or [],
            }
        )

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
        "Propose one complete structurally compatible seed and up to three compact diverse alternatives. Full ranking is audited externally; fetch one alternative by audit_id and seed_id only when promoting it.",
        {
            "type": "object",
            "properties": {
                "slots": {"type": "object"},
                "client": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "object"}},
                "table": {"type": "string"},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "audit_id": {"type": "string"},
                "seed_id": {"type": "string"},
            },
            "required": ["client"],
        },
    )
    async def select_seed(args: dict[str, Any]) -> dict[str, Any]:
        audit_id = str(args.get("audit_id") or "")
        seed_id = str(args.get("seed_id") or "")
        if audit_id and seed_id:
            audit = seed_audits.get(audit_id)
            if audit is None:
                return _text_result({"audit_id": audit_id, "error": "seed audit is unavailable"})
            candidate = next(
                (item for item in audit["candidates"] if item["seed_id"] == seed_id and item["eligible"]),
                None,
            )
            if candidate is None:
                return _text_result({"audit_id": audit_id, "seed_id": seed_id, "error": "seed is not an eligible audited alternative"})
            promoted = {
                key: value
                for key, value in candidate.items()
                if key not in {"eligible", "gate_failures", "structural_fingerprint"}
            }
            return _text_result({"audit_id": audit_id, "promoted_seed": promoted})
        if "slots" not in args:
            return _text_result({"error": "slots are required for initial seed selection"})

        audit_id = uuid.uuid4().hex[:12]
        audit = build_seed_audit(
            slots=args["slots"],
            client=args["client"],
            columns=args.get("columns") or [],
            table=args.get("table"),
            exclude=args.get("exclude") or [],
        )
        seed_audits[audit_id] = audit
        with observation(
            "vp.select_seed.full_audit",
            **{
                "vp.audit_id": audit_id,
                "vp.audit.kind": "seed_selection",
                "input.value": args,
            },
        ) as span:
            update_observation(span, **{"output.value": serialize_seed_audit(audit)})
        return _text_result(compact_seed_selection(audit, audit_id=audit_id))

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
            retrieve_existing_vps,
            record_resolution,
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
