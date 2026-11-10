"""Pydantic models for the file contracts in docs/ARCHITECTURE.md."""

from __future__ import annotations

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
