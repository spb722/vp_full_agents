from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from vp_agent.config import load_settings
from vp_agent.schemas import KpiMeta


def data_dir() -> Path:
    return load_settings().data_dir


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


@lru_cache(maxsize=4)
def load_kpi_meta(path: str | None = None) -> tuple[KpiMeta, ...]:
    source = Path(path) if path else data_dir() / "kpi_meta.csv"
    with source.open(newline="", encoding="utf-8-sig") as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append(
                KpiMeta(
                    id=_clean(row.get("id")),
                    feature_name=_clean(row.get("feature_name")),
                    group_id=_clean(row.get("group_id")),
                    group_name=_clean(row.get("group_name")),
                    kpi_type=_clean(row.get("kpi_type")),
                    description=_clean(row.get("description")),
                    kpi_type_name=_clean(row.get("kpi_type_name")),
                    time_window_value=_clean(row.get("time_window_value")),
                    activity=_clean(row.get("activity")),
                    network_scope=_clean(row.get("network_scope")),
                    direction_value=_clean(row.get("direction_value")),
                    data_type=_clean(row.get("data_type")),
                    value_references=_clean(row.get("value_references")),
                    value_type=_clean(row.get("value_type")),
                )
            )
    return tuple(rows)


@lru_cache(maxsize=4)
def load_seed_catalog(path: str | None = None) -> dict[str, Any]:
    source = Path(path) if path else data_dir() / "vp_seed_catalog_with_selection_metadata.json"
    with source.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=8)
def load_vp_descriptions(client: str, path: str | None = None) -> tuple[dict[str, str], ...]:
    if path:
        source = Path(path)
    else:
        normalized = client.lower().strip()
        source = data_dir() / f"vpdesc-all-{normalized}.csv"
    with source.open(newline="", encoding="utf-8-sig") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def find_seed(seed_id: str) -> dict[str, Any] | None:
    for seed in load_seed_catalog().get("seeds", []):
        if seed.get("seed_id") == seed_id:
            return seed
    return None

