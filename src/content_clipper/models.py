"""Pydantic models for the file contracts in docs/ARCHITECTURE.md."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class Word(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seg: int = Field(ge=0)
    w: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Transcript(BaseModel):
    """data/transcripts/<video_id>.json

    Invariants (checked on every load and save):
    - segment ids are 0..n-1 in order, each with start <= end, starts non-decreasing;
    - words point at an existing segment, in non-decreasing segment order;
    - word timestamps are monotonic and non-overlapping:
      start <= end, and each word starts at or after the previous word's end.
    """

    model_config = ConfigDict(extra="forbid")

    video: str
    video_id: str
    duration_s: float = Field(ge=0)
    language: str
    model: str
    segments: list[Segment]
    words: list[Word]

    @model_validator(mode="after")
    def _check_invariants(self) -> Transcript:
        prev_start = 0.0
        for i, s in enumerate(self.segments):
            if s.id != i:
                raise ValueError(f"segment {i} has id {s.id}; ids must be 0..n-1 in order")
            if s.end < s.start:
                raise ValueError(f"segment {i}: end {s.end} < start {s.start}")
            if s.start < prev_start:
                raise ValueError(f"segment {i}: start {s.start} < previous start {prev_start}")
            prev_start = s.start

        n_segs = len(self.segments)
        prev_seg = 0
        prev_end = 0.0
        for i, w in enumerate(self.words):
            if w.seg >= n_segs:
                raise ValueError(f"word {i} ({w.w!r}) points at missing segment {w.seg}")
            if w.seg < prev_seg:
                raise ValueError(f"word {i} ({w.w!r}): segment {w.seg} after {prev_seg}")
            if w.end < w.start:
                raise ValueError(f"word {i} ({w.w!r}): end {w.end} < start {w.start}")
            if w.start < prev_end:
                raise ValueError(
                    f"word {i} ({w.w!r}): start {w.start} < previous word end {prev_end}"
                )
            prev_seg = w.seg
            prev_end = w.end
        return self


# --------------------------------------------------------------------------------------------
# Campaign rules: campaigns/<name>/rules.yaml. Schema mirrors campaigns/_template/rules.yaml and
# docs/CAMPAIGN_RULES.md. Defaults here equal the template defaults ("the brief does not say").
# Models stay permissive about cross-field consistency (a draft may be incomplete); those checks
# live in content_clipper.campaign.store.problems() and block approval instead.

Platform = Literal["tiktok", "instagram", "youtube"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRules(_Strict):
    policy: Literal["official_only", "own_footage_only", "any", "unknown"] = "unknown"
    location_notes: str = ""
    require_registry: bool = True


class VideoRules(_Strict):
    min_length_s: float | None = Field(default=None, ge=0)
    max_length_s: float | None = Field(default=None, ge=0)
    language: str | None = None
    quality_notes: list[str] = []


class AudioRules(_Strict):
    music_allowed: bool | None = None  # null = treated as not allowed
    ai_voice_allowed: bool | None = None  # null = treated as not allowed
    original_audio_only: bool = False


class Mention(_Strict):
    text: str
    platforms: list[Platform] = []  # empty = every platform in rules.platforms


class Disclosure(_Strict):
    required: bool = False
    accepted_tags: list[str] = []
    must_be_first_hashtag: bool = False
    own_line: bool = False
    after_caption_text: bool = False


class RequiredLine(_Strict):
    id: str
    where: Literal["caption", "on_screen", "either"] = "either"
    verbatim: bool = True
    options: list[str] = []
    expires: date | None = None


class CaptionRules(_Strict):
    required_mentions: list[Mention] = []
    disclosure: Disclosure = Field(default_factory=Disclosure)
    max_additional_hashtags: int | None = Field(default=None, ge=0)
    hashtags_must_relate: bool = False
    max_length_chars: int | None = Field(default=None, ge=1)
    required_lines: list[RequiredLine] = []
    forbidden_phrases: list[str] = []
    example_captions: list[str] = []
    style_notes: list[str] = []


class OnScreenTextRules(_Strict):
    policy: Literal["optional", "required", "forbidden"] = "optional"
    must_match_screen: bool = False
    notes: list[str] = []


class HookStyle(_Strict):
    mode: Literal["default", "descriptive", "none"] = "default"
    notes: list[str] = []


class PostingRules(_Strict):
    max_posts_per_clip: int | None = Field(default=None, ge=1)
    same_clip_on_multiple_accounts: Literal["allowed", "forbidden"] | None = None
    # Standing rule: the same clip may go to several platforms in every campaign.
    same_clip_across_platforms: Literal["allowed", "forbidden"] | None = "allowed"
    min_days_live: int | None = Field(default=None, ge=0)
    dedicated_page_required: bool | None = None
    account_notes: list[str] = []


class CampaignRulesBody(_Strict):
    """Everything in rules.yaml that comes from the brief (what the LLM drafts)."""

    valid_until: date | None = None
    platforms: list[Platform] = []
    rate_per_1k_views: float | None = Field(default=None, ge=0)
    source: SourceRules = Field(default_factory=SourceRules)
    video: VideoRules = Field(default_factory=VideoRules)
    audio: AudioRules = Field(default_factory=AudioRules)
    caption: CaptionRules = Field(default_factory=CaptionRules)
    allowed_facts: list[str] = []
    on_screen_text: OnScreenTextRules = Field(default_factory=OnScreenTextRules)
    hook_style: HookStyle = Field(default_factory=HookStyle)
    prohibited: list[str] = []
    posting: PostingRules = Field(default_factory=PostingRules)
    checklist: list[str] = []
    other_rules: list[str] = []
    unknowns: list[str] = []


class CampaignRules(CampaignRulesBody):
    """campaigns/<name>/rules.yaml: the brief-derived body plus bookkeeping."""

    schema_version: Literal[1] = 1
    name: str
    status: Literal["draft", "reviewed"] = "draft"
    brief_file: str = "brief.md"
    brief_sha256: str = ""
    evidence: dict[str, str] = {}  # dotted.path or list.path[i] -> verbatim quote from the brief

    def body(self) -> CampaignRulesBody:
        return CampaignRulesBody.model_validate(
            self.model_dump(include=set(CampaignRulesBody.model_fields))
        )


# --------------------------------------------------------------------------------------------
# Shot analysis: data/analysis/<video_id>/shots.json (+ shot_###.jpg keyframes next to it)


class Shot(_Strict):
    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    keyframe: str  # file name next to shots.json
    loudness_db: float  # RMS level of the shot's audio in dBFS (-90 = silence)
    motion: float = Field(ge=0)  # mean absolute frame-to-frame difference, 0..1
    brightness: float = Field(ge=0, le=1)  # mean luma, 0..1


class ShotAnalysis(_Strict):
    video: str
    video_id: str
    duration_s: float = Field(ge=0)
    analysis_fps: float = Field(gt=0)
    shots: list[Shot]

    @model_validator(mode="after")
    def _contiguous(self) -> ShotAnalysis:
        prev_end = 0.0
        for i, s in enumerate(self.shots):
            if s.id != i:
                raise ValueError(f"shot {i} has id {s.id}")
            if abs(s.start - prev_end) > 1e-6:
                raise ValueError(f"shot {i} starts at {s.start}, previous ended at {prev_end}")
            if s.end <= s.start:
                raise ValueError(f"shot {i}: end {s.end} <= start {s.start}")
            prev_end = s.end
        return self


# --------------------------------------------------------------------------------------------
# Clips: data/clips/<video_id>.clips.json


class Clip(_Strict):
    id: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    start_shot: int = Field(ge=0)
    end_shot: int = Field(ge=0)
    start_seg: int | None = None  # first/last transcript segment spoken in the clip (None = no speech)
    end_seg: int | None = None
    hook_line: str
    hook_options: list[str] = []  # validated suggestions; hook_line is the chosen one
    title: str
    score: float
    scores: dict[str, float]
    reason: str
    speech_cut: bool = False  # True when a sentence had to be cut at the start or end
    variant_of: str | None = None  # same cut as this clip id, different hook (same video for
    #                                posting limits)
    approved: bool | None = None  # set by a human; render only uses approved == true


class RejectedCandidate(_Strict):
    start_shot: int
    end_shot: int
    title: str
    reason: str


class ClipsFile(_Strict):
    video_id: str
    campaign: str
    generated_at: str
    model: str
    clips: list[Clip]
    rejected: list[RejectedCandidate] = []


# --------------------------------------------------------------------------------------------
# Trend brief: data/research/<campaign>.trend_brief.json


class TrendPattern(_Strict):
    pattern: str  # an angle or pattern the audience responds to, in our own words
    why_it_works: str
    example_titles: list[str] = []


Emotion = Literal["anticipation", "speculation", "excitement", "intrigue", "nostalgia",
                  "frustration"]


class FanReference(_Strict):
    name: str  # a thing fans recognise: a gun, map, mode, character, old zone
    context: str  # why fans care, in our own words
    emotion: Emotion


class TrendBriefBody(_Strict):
    """What the LLM returns from research notes."""

    keywords: list[str]
    fan_references: list[FanReference] = []
    patterns: list[TrendPattern]
    hook_formulas: list[str]  # reusable templates in new wording
    avoid: list[str]  # angles hooks must not use (they would break the campaign rules)


class TrendBrief(TrendBriefBody):
    campaign: str
    generated_at: str
    sources: list[str]
