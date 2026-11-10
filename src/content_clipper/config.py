"""Typed loaders for config/*.yaml. Paths are relative to the working directory (repo root)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

CONFIG_DIR = Path("config")


class TranscribeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_size: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = Field(default=5, ge=1)
    vad_filter: bool = True
    language: str | None = "en"  # None = auto-detect
    cpu_threads: int = Field(default=0, ge=0)  # 0 = library default


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path}: expected a mapping at the top level")
    return data


def load_transcribe_config(path: Path = CONFIG_DIR / "transcribe.yaml") -> TranscribeConfig:
    return TranscribeConfig.model_validate(_load_yaml(path))
