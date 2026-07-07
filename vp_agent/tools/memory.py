from __future__ import annotations

from typing import Any


def episodic_lookup(slots: dict[str, Any], client: str) -> dict[str, Any]:
    return {
        "client": client,
        "matches": [],
        "reason": "episodic memory store is not configured in this implementation slice",
    }


def queue_correction(trace_id: str, corrected: dict[str, Any]) -> dict[str, Any]:
    return {
        "queued": True,
        "trace_id": trace_id,
        "corrected": corrected,
        "note": "review queue persistence will be added with the reinforced memory slice",
    }

