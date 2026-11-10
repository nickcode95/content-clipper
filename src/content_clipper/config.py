"""Typed loaders for config/*.yaml. Paths are relative to the working directory (repo root)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: str  # brief -> rules drafting, clip selection, captions
    light: str  # cheap tasks: research keywords, advisory QA review


def load_models_config(path: Path = CONFIG_DIR / "models.yaml") -> ModelsConfig:
    return ModelsConfig.model_validate(_load_yaml(path))


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float = Field(ge=0)
    max: float = Field(gt=0)


class RubricConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: dict[str, float]
    descriptions: dict[str, str] = {}
    window_seconds: Window
    target_seconds: Window | None = None


def load_rubric_config(path: Path = CONFIG_DIR / "rubric.yaml") -> RubricConfig:
    cfg = RubricConfig.model_validate(_load_yaml(path))
    if not cfg.weights or any(w < 0 for w in cfg.weights.values()):
        raise ValueError(f"{path}: weights must be non-empty and non-negative")
    return cfg


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoEncode(_M):
    codec: str
    preset: str
    crf: int
    pix_fmt: str


class AudioEncode(_M):
    codec: str
    bitrate: str
    loudnorm: str


class CaptionStyle(_M):
    font: str
    bold: bool = True
    size: int = Field(gt=0)
    highlight_color: str
    max_words_per_line: int = Field(ge=1)
    safe_top_pct: float = Field(ge=0, lt=50)
    safe_bottom_pct: float = Field(ge=0, lt=50)


class HookStyleCfg(_M):
    seconds: float = Field(default=0, ge=0)  # 0 = whole clip
    size: int = Field(gt=0)


class LayoutCfg(_M):
    mode: Literal["band"] = "band"
    background: Literal["blur", "color"] = "blur"
    background_color: str = "#111111"
    video_center_y: float = Field(default=0.45, gt=0, lt=1)
    hook_position: Literal["below", "above"] = "below"


class MusicCfg(_M):
    gain_db: float
    duck: bool
    fade_s: float


class RenderConfig(_M):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)
    video: VideoEncode
    audio: AudioEncode
    captions: CaptionStyle
    hook: HookStyleCfg
    layout: LayoutCfg = Field(default_factory=LayoutCfg)
    music: MusicCfg


def load_render_config(path: Path = CONFIG_DIR / "render.yaml") -> RenderConfig:
    return RenderConfig.model_validate(_load_yaml(path))
