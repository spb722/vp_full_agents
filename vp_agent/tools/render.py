from __future__ import annotations

import re
from typing import Any

from vp_agent.data import find_seed


TEMPLATE_VAR_RE = re.compile(r"(?<!\$)\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
DOUBLE_FORMULA_BRACE_RE = re.compile(r"([Vf])\{\{([^{}]+)\}\}")


def _literal(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return '""'
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    if text.startswith('"') and text.endswith('"'):
        return text
    return f'"{text}"'


def _filter_condition(item: dict[str, Any]) -> str:
    col = item.get("col") or item.get("feature_name")
    if not col:
        raise ValueError(f"filter missing column: {item}")
    op = item.get("operator") or "="
    value = item.get("value")
    return f"{col} {op} {_literal(value)}"


def render_from_template(template: str, variables: dict[str, Any], filters: list[dict[str, Any]] | None = None) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        # A complete agent-composed rule contains literal engine formula syntax
        # such as V{ALIAS}=f{COLUMN}. Those braces are not template variables.
        prefix = template[match.start() - 1] if match.start() else ""
        if prefix in {"V", "f"}:
            return match.group(0)
        raise ValueError(f"missing template variable: {key}")

    rendered = TEMPLATE_VAR_RE.sub(replace, template)
    # Seed templates use triple braces around variables so the substituted
    # value remains inside one literal V{...}/f{...} pair. Collapse only that
    # known formula wrapper after variable substitution.
    rendered = DOUBLE_FORMULA_BRACE_RE.sub(r"\1{\2}", rendered)
    filter_parts = [_filter_condition(item) for item in (filters or [])]
    parts = filter_parts + [rendered]
    return " AND ".join(part for part in parts if part)


def render_condition(
    seed_id: str | None,
    template: str | None,
    variables: dict[str, Any],
    filters: list[dict[str, Any]] | None,
    client: str,
) -> dict[str, Any]:
    selected_seed = find_seed(seed_id) if seed_id else None
    selected_template = template or (selected_seed or {}).get("output_template")
    if not selected_template:
        raise ValueError("render_condition requires seed_id with output_template or explicit template")

    rule = render_from_template(selected_template, variables, filters)
    return {
        "client": client,
        "seed_id": seed_id,
        "parent_condition": rule,
        "runtime_placeholders": {
            "operator": "${operator}" in rule,
            "value": "${value}" in rule,
        },
    }
