from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATA_DIR = Path("/Users/sachinpb/PycharmProjects/Virtual_profile_agent/data")
PROJECT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DEFAULT_DATA_DIR
    orchestrator_model: str = "claude-sonnet-5"
    subagent_model: str = "claude-haiku-4-5-20251001"
    max_turns: int = 25


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("VP_DATA_DIR", DEFAULT_DATA_DIR)),
        orchestrator_model=os.environ.get("VP_ORCHESTRATOR_MODEL", Settings.orchestrator_model),
        subagent_model=os.environ.get("VP_SUBAGENT_MODEL", Settings.subagent_model),
        max_turns=int(os.environ.get("VP_MAX_TURNS", "25")),
    )
