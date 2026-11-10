"""Select stage: pick clips by shot range from keyframes + narration + rules, then snap in code.

The LLM sees shot IDs (H000...), one keyframe per shot, and a timeline with each shot's length,
audio level, motion, brightness and the words spoken in it. It never sees or returns timestamps.
It returns candidates as shot ranges. Code turns them into cut points:

1. Cuts sit on shot boundaries (the end is pulled back one analysis frame so no frame of the next
   shot leaks in).
2. If a cut would split a sentence, extend the clip to the nearest shot boundary that keeps the
   sentence whole, if that costs at most `max_extend_s`.
3. Never cut inside a word: if a cut still lands inside a word, move it to the word's edge
   (dropping the partial word) and flag `speech_cut`.
4. Enforce the length limits, drop candidates that overlap a better one, score with the rubric.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, create_model

from content_clipper import llm
from content_clipper.campaign.store import Campaign, require_reviewed
from content_clipper.config import RubricConfig
from content_clipper.models import (
    CampaignRules,
    Clip,
    ClipsFile,
    RejectedCandidate,
    ShotAnalysis,
    Transcript,
    TrendBrief,
)
from content_clipper.research.manual import load_trend_brief
from content_clipper.select.hooks import (
    HOOK_GUIDE,
    MAX_OPTIONS,
    narration_between,
    reference_names,
    research_text,
    trend_section,
    valid_options,
)
from content_clipper.sources import is_registered

EPS = 0.05
MAX_IMAGES = 100  # API limit per request
WORD_GAP_PAD = 0.02


class SelectError(RuntimeError):
    pass


class SnapError(ValueError):
    pass


# ---------------------------------------------------------------- snapping (pure)


@dataclass(frozen=True)
class Snapped:
    start: float
    end: float
    start_shot: int
    end_shot: int
    start_seg: int | None
    end_seg: int | None
    speech_cut: bool


def _inside(t: float, start: float, end: float) -> bool:
    return start + EPS < t < end - EPS


def snap(
    a: int,
    b: int,
    analysis: ShotAnalysis,
    transcript: Transcript,
    *,
    min_len: float,
    max_len: float,
    max_extend_s: float = 3.0,
) -> Snapped:
    shots = analysis.shots
    n = len(shots)
    if not (0 <= a <= b < n):
        raise SnapError(f"shot range H{a:03d}-H{b:03d} does not exist (video has {n} shots)")
    margin = 1.0 / analysis.analysis_fps

    def end_of(k: int) -> float:
        return shots[k].end if k == n - 1 else shots[k].end - margin

    def seg_at(t: float):
        return next((s for s in transcript.segments if _inside(t, s.start, s.end)), None)

    def word_at(t: float):
        return next((w for w in transcript.words if w.start < t < w.end), None)

    start, end = shots[a].start, end_of(b)

    seg = seg_at(start)
    if seg is not None:
        k = a
        while k > 0 and shots[k].start > seg.start + EPS:
            k -= 1
        if shots[k].start <= seg.start + EPS and start - shots[k].start <= max_extend_s:
            a, start = k, shots[k].start
    seg = seg_at(end)
    if seg is not None:
        k = b
        while k < n - 1 and end_of(k) < seg.end - EPS:
            k += 1
        if end_of(k) >= seg.end - EPS and end_of(k) - end <= max_extend_s:
            b, end = k, end_of(k)

    speech_cut = seg_at(start) is not None or seg_at(end) is not None
    w = word_at(start)
    if w is not None:
        start, speech_cut = w.end + WORD_GAP_PAD, True
    w = word_at(end)
    if w is not None:
        end, speech_cut = w.start - WORD_GAP_PAD, True

    length = end - start
    if length < min_len - EPS:
        raise SnapError(f"too short: {length:.1f} s < {min_len:g} s")
    if length > max_len + EPS:
        raise SnapError(f"too long: {length:.1f} s > {max_len:g} s")

    spoken = [x for x in transcript.words if x.start >= start - EPS and x.end <= end + EPS]
    return Snapped(
        start=round(start, 3), end=round(end, 3), start_shot=a, end_shot=b,
        start_seg=spoken[0].seg if spoken else None, end_seg=spoken[-1].seg if spoken else None,
        speech_cut=speech_cut,
    )


def weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights.values())
    return round(sum(weights[k] * scores[k] for k in weights) / total, 2) if total else 0.0


def overlaps(x: tuple[float, float], y: tuple[float, float]) -> bool:
    return min(x[1], y[1]) - max(x[0], y[0]) > EPS


# ---------------------------------------------------------------- prompt


def response_model(rubric_keys: list[str]) -> type[BaseModel]:
    scores = create_model(
        "Scores", **{k: (int, Field(ge=0, le=10)) for k in rubric_keys}
    )
    candidate = create_model(
        "Candidate",
        start_shot=(int, ...), end_shot=(int, ...), hook_options=(list[str], ...),
        title=(str, ...),
        scores=(scores, ...), reason=(str, ...),
    )
    return create_model("SelectResponse", candidates=(list[candidate], ...))


def _levels(values: list[float], names: tuple[str, str, str]) -> list[str]:
    if not values:
        return []
    ranked = sorted(values)
    lo, hi = ranked[len(ranked) // 3], ranked[(2 * len(ranked)) // 3]
    return [names[0] if v < lo else names[2] if v >= hi else names[1] for v in values]


def timeline(analysis: ShotAnalysis, transcript: Transcript) -> str:
    motion = _levels([s.motion for s in analysis.shots], ("low", "medium", "high"))
    lines = []
    for s, m in zip(analysis.shots, motion, strict=True):
        words = [w.w for w in transcript.words if s.start <= (w.start + w.end) / 2 < s.end]
        light = "dark" if s.brightness < 0.15 else "dim" if s.brightness < 0.35 else "bright"
        said = f' | "{" ".join(words)}"' if words else ""
        lines.append(
            f"[H{s.id:03d}] {s.end - s.start:4.1f}s  audio {s.loudness_db:5.1f} dB  "
            f"motion {m:6s}  {light:6s}{said}"
        )
    return "\n".join(lines)


SYSTEM = """\
You are a short-form video editor choosing clips from a longer video for TikTok and Instagram \
Reels. You get one keyframe per shot, labelled with its shot ID, and a timeline listing each \
shot's length, audio level, motion, brightness and the words spoken during it.

Pick moments that work as standalone vertical videos and hold attention from the first second: \
open on something striking (seen or heard), keep the pace up, end on a payoff. Shots that only \
show logos, title cards or end slates rarely hold attention. Score every candidate 0-10 on each \
rubric key. The campaign rules are the campaign's non-negotiable terms: obey them exactly. \
Return only JSON matching the schema. Do not invent anything that is not in the video. \
hook_options are short original lines of on-screen text for the clip, read like a fan's post \
reacting to it (best first, each playing on a different fan emotion or visible detail); title \
is a short working title for the human reviewer.\
"""

def rules_for_prompt(rules: CampaignRules) -> dict[str, Any]:
    return {
        "platforms": rules.platforms,
        "video": rules.video.model_dump(mode="json"),
        "audio": rules.audio.model_dump(mode="json"),
        "allowed_facts": rules.allowed_facts,
        "on_screen_text": rules.on_screen_text.model_dump(mode="json"),
        "hook_style": rules.hook_style.model_dump(mode="json"),
        "prohibited": rules.prohibited,
        "other_rules": rules.other_rules,
    }


def build_content(
    analysis: ShotAnalysis,
    transcript: Transcript,
    rules: CampaignRules,
    rubric: RubricConfig,
    *,
    keyframe_dir: Path,
    min_len: float,
    max_len: float,
    max_clips: int,
    trend: TrendBrief | None = None,
) -> list[dict[str, Any]]:
    shots = analysis.shots
    step = max(1, -(-len(shots) // MAX_IMAGES))
    content: list[dict[str, Any]] = []
    for s in shots[::step]:
        data = base64.standard_b64encode((keyframe_dir / s.keyframe).read_bytes()).decode()
        content.append({"type": "text", "text": f"[H{s.id:03d}]"})
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    rubric_lines = "\n".join(
        f"- {k}: {rubric.descriptions.get(k, k)}" for k in rubric.weights
    )
    target = rubric.target_seconds
    target_line = (f"Aim for {target.min:g}-{target.max:g} s where the material allows. "
                   if target else "")
    sampled = "" if step == 1 else f"(Only every {step}th shot has a keyframe above.)\n"
    content.append({"type": "text", "text": (
        f"{sampled}Timeline ({len(shots)} shots, {analysis.duration_s:.1f} s):\n"
        f"{timeline(analysis, transcript)}\n\n"
        f"{trend_section(trend)}"
        f"Campaign rules:\n{json.dumps(rules_for_prompt(rules), ensure_ascii=False, indent=1)}\n\n"
        f"Rubric keys:\n{rubric_lines}\n\n"
        f"Task: return up to {max_clips} candidate clips, best first, with no shot in two "
        f"candidates. A clip is the shots start_shot..end_shot inclusive; its length is the sum "
        f"of their lengths and must be between {min_len:g} and {max_len:g} s. {target_line}"
        f"Give up to {MAX_OPTIONS} hook_options per candidate. {HOOK_GUIDE[rules.hook_style.mode]}"
    )})
    return content


# ---------------------------------------------------------------- stage


def length_limits(rules: CampaignRules, rubric: RubricConfig, duration: float) -> tuple[float, float]:
    lo = rules.video.min_length_s if rules.video.min_length_s is not None else rubric.window_seconds.min
    hi = rules.video.max_length_s if rules.video.max_length_s is not None else rubric.window_seconds.max
    return lo, min(hi, duration)


def select_clips(
    campaign: str,
    video_id: str,
    *,
    model: str,
    rubric: RubricConfig,
    transcripts_dir: Path = Path("data/transcripts"),
    analysis_dir: Path = Path("data/analysis"),
    clips_dir: Path = Path("data/clips"),
    registry: Path = Path("data/sources/registry.csv"),
    campaigns_root: Path = Path("campaigns"),
    research_dir: Path = Path("data/research"),
    max_clips: int = 5,
    force: bool = False,
) -> ClipsFile:
    c: Campaign = require_reviewed(campaign, campaigns_root)
    rules = c.rules
    if rules.source.require_registry and not is_registered(video_id, campaign, registry):
        raise SelectError(
            f"{video_id!r} is not registered for {campaign!r} in {registry}. Add a row "
            f"(video_id,origin,campaign,downloaded_at,notes) for authorised footage first."
        )
    out = clips_dir / f"{video_id}.clips.json"
    if out.exists() and not force:
        raise SelectError(f"{out} exists (it may hold your approvals); pass --force to redo it")
    t_path = transcripts_dir / f"{video_id}.json"
    a_path = analysis_dir / video_id / "shots.json"
    for p, cmd in ((t_path, "transcribe"), (a_path, "analyze")):
        if not p.exists():
            raise SelectError(f"{p} not found; run `content-clipper {cmd} <video>` first")
    transcript = Transcript.model_validate_json(t_path.read_text(encoding="utf-8"))
    analysis = ShotAnalysis.model_validate_json(a_path.read_text(encoding="utf-8"))

    keys = list(rubric.weights)
    min_len, max_len = length_limits(rules, rubric, analysis.duration_s)
    trend = load_trend_brief(campaign, research_dir)
    content = build_content(analysis, transcript, rules, rubric, keyframe_dir=a_path.parent,
                            min_len=min_len, max_len=max_len, max_clips=max_clips, trend=trend)
    resp = llm.parse_structured(model=model, system=SYSTEM, user=content,
                                output_type=response_model(keys))

    kept: list[tuple[float, Snapped, Any]] = []
    rejected: list[RejectedCandidate] = []
    scored = []
    for cand in resp.candidates:
        sc = cand.scores.model_dump()
        try:
            snapped = snap(cand.start_shot, cand.end_shot, analysis, transcript,
                           min_len=min_len, max_len=max_len)
        except SnapError as e:
            rejected.append(RejectedCandidate(start_shot=cand.start_shot, end_shot=cand.end_shot,
                                              title=cand.title, reason=str(e)))
            continue
        scored.append((weighted_score(sc, rubric.weights), snapped, cand))
    for score, snapped, cand in sorted(scored, key=lambda x: -x[0]):
        hit = next((k for k in kept if overlaps((k[1].start, k[1].end),
                                                (snapped.start, snapped.end))), None)
        if hit is not None:
            rejected.append(RejectedCandidate(
                start_shot=cand.start_shot, end_shot=cand.end_shot, title=cand.title,
                reason=f"overlaps a higher-scoring clip ({hit[2].title})"))
            continue
        kept.append((score, snapped, cand))

    research_n = research_text(trend)
    no_hooks = rules.hook_style.mode == "none" or rules.on_screen_text.policy == "forbidden"
    hooks: list[list[str]] = []
    for _, s, cand in kept:
        opts = [] if no_hooks else valid_options(
            cand.hook_options, c=c, narration=narration_between(transcript, s.start, s.end),
            research_n=research_n, names=reference_names(trend))[0]
        hooks.append(opts)
    clips = [
        Clip(
            id=f"c{i:02d}", start_s=s.start, end_s=s.end, start_shot=s.start_shot,
            end_shot=s.end_shot, start_seg=s.start_seg, end_seg=s.end_seg,
            hook_line=opts[0] if opts else "", hook_options=opts,
            title=cand.title.strip(), score=score,
            scores={k: float(v) for k, v in cand.scores.model_dump().items()},
            reason=cand.reason.strip(), speech_cut=s.speech_cut, approved=None,
        )
        for i, ((score, s, cand), opts) in enumerate(zip(kept, hooks, strict=True), start=1)
    ]
    result = ClipsFile(
        video_id=video_id, campaign=campaign, model=model,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        clips=clips, rejected=rejected,
    )
    clips_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return result
