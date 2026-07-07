from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KpiMeta:
    id: str
    feature_name: str
    group_id: str
    group_name: str
    kpi_type: str
    description: str
    kpi_type_name: str
    time_window_value: str
    activity: str
    network_scope: str
    direction_value: str
    data_type: str
    value_references: str
    value_type: str


@dataclass(frozen=True)
class Candidate:
    id: str
    feature_name: str
    group_name: str
    description: str
    data_type: str
    time_window_value: str
    score: float
    reason: str
    bm25_score: float = 0.0
    semantic_score: float = 0.0
    bm25_norm: float = 0.0
    embedding_norm: float = 0.0
    hybrid_score: float = 0.0
    metadata_boost: float = 0.0
    client_prior: float = 0.0


@dataclass(frozen=True)
class RouteDecision:
    table: str
    reason: str
    variant_hint: str


@dataclass
class ToolState:
    request_id: str | None = None
    console_trace: bool = False
    slots_seen: bool = False
    plan_seen: bool = False
    render_seen: bool = False
    stop_blocks: int = 0
    subagent_counts: dict[str, int] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SeedCandidate:
    seed_id: str
    description: str
    client: str
    output_template: str
    score: float
    confidence: float
    reason: str
    required_variables: tuple[str, ...]
    suggested_variables: dict[str, Any]
    selection_signature: dict[str, Any]
