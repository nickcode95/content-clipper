"""Hook suggestions: several options per clip, steered by the trend brief, checked in code.

Checks (deterministic): not empty, at most MAX_HOOK_CHARS, every token with a digit (scores,
prices, dates, "MW4", "X01") must appear in the clip's narration, the campaign brief or
allowed_facts, no forbidden phrase, and no 5-word run copied from the research notes.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import create_model

from content_clipper import llm
from content_clipper.campaign.draft import has_literal, norm
from content_clipper.campaign.store import Campaign, require_reviewed
from content_clipper.models import Clip, ClipsFile, ShotAnalysis, Transcript, TrendBrief
from content_clipper.research.manual import RESEARCH_DIR, load_trend_brief

MAX_HOOK_CHARS = 80
MAX_OPTIONS = 4
SHINGLE = 5

HOOK_ANGLES = (
    "Hooks that perform play on fans' anticipation: speculation, excitement, intrigue, nostalgia "
    "or frustration, often as a casual first-person fan reaction or question (for example "
    "\"did i just see <old-school gun>?\" or \"is <new zone> the new <old zone>?\"). Whatever a "
    "hook names or asks about must be clearly visible in the keyframes or said in the narration: "
    "never guess a gun, place or character name. Speculation is a question or reaction, never a "
    "stated fact. Use only the angles the campaign rules allow. Emoji are welcome where they add "
    "to the reaction, following the trend brief's emoji_patterns when it has them (usually 0-2, "
    "at the end of the line)."
)

HOOK_GUIDE = {
    "default": f"{HOOK_ANGLES} Hooks may be punchy, but must be true to the clip.",
    "descriptive": f"{HOOK_ANGLES} No exaggeration and no false claims: a hook may react to, ask "
                   "about or describe only what is seen or said in the clip.",
    "none": "This campaign uses no hook text: return no hook options.",
}


def _shingles(text_n: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text_n)
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def hook_problems(hook: str, *, grounded_n: str, forbidden: list[str], research_n: str) -> list[str]:
    h = hook.strip()
    out = []
    if not h:
        return ["empty"]
    if len(h) > MAX_HOOK_CHARS:
        out.append(f"longer than {MAX_HOOK_CHARS} characters")
    for tok in re.findall(r"[\w$€£]*\d[\w.,%]*", h):
        if norm(tok).rstrip(".,") not in grounded_n:
            out.append(f"{tok!r} is not in the clip, the brief or allowed_facts")
    out += [f"uses forbidden phrase {p!r}" for p in forbidden if has_literal(norm(h), p)]
    if research_n and _shingles(norm(h)) & _shingles(research_n):
        out.append("copies wording from the research notes")
    return out


def narration_between(transcript: Transcript, start: float, end: float) -> str:
    return " ".join(w.w for w in transcript.words
                    if w.start >= start - 0.05 and w.end <= end + 0.05)


def clip_narration(transcript: Transcript, clip: Clip) -> str:
    return narration_between(transcript, clip.start_s, clip.end_s)


def grounding(c: Campaign, narration: str, names: list[str] = ()) -> str:
    """Text whose numbers a hook may use: narration, brief, allowed facts, fan reference names."""
    brief = c.brief_path.read_text(encoding="utf-8") if c.brief_path.exists() else ""
    return norm(" ".join([narration, brief, *c.rules.allowed_facts, *names]))


def reference_names(brief: TrendBrief | None) -> list[str]:
    return [r.name for r in brief.fan_references] if brief else []


def research_text(brief: TrendBrief | None) -> str:
    if brief is None:
        return ""
    return norm(" ".join(Path(s).read_text(encoding="utf-8") for s in brief.sources
                         if Path(s).exists()))


def valid_options(options: list[str], *, c: Campaign, narration: str, research_n: str,
                  names: list[str] = ()) -> tuple[list[str], list[str]]:
    """(kept, rejected-with-reason). Duplicates dropped, order kept."""
    kept, rejected = [], []
    g = grounding(c, narration, names)
    for o in options:
        o = " ".join(o.split())
        probs = hook_problems(o, grounded_n=g, forbidden=c.rules.caption.forbidden_phrases,
                              research_n=research_n)
        if probs:
            rejected.append(f"{o!r}: {'; '.join(probs)}")
        elif o.lower() not in {k.lower() for k in kept}:
            kept.append(o)
    return kept[:MAX_OPTIONS], rejected


def trend_section(brief: TrendBrief | None) -> str:
    if brief is None:
        return ""
    data = brief.model_dump(include={"keywords", "fan_references", "emoji_patterns", "patterns",
                                     "hook_formulas", "avoid"})
    return (
        "Audience research (trend brief). Use fan_references and patterns to choose which visible "
        "moment a hook reacts to and which fan emotion it plays on, and phrase it the way this "
        "audience talks. Name a fan reference only if it is clearly in the clip, or as an explicit "
        "comparison question about what is on screen. Never put a scored, priced, counted or dated "
        "claim from the research in a hook, never use an angle under 'avoid', and never copy "
        "wording from it:\n"
        f"{json.dumps(data, ensure_ascii=False, indent=1)}\n\n"
    )


# ---------------------------------------------------------------- regenerate for existing clips


class HookError(RuntimeError):
    pass


SYSTEM = """\
You write hook lines: the short on-screen text shown under a vertical clip for TikTok and \
Instagram Reels, read like a fan's post reacting to the clip. For each clip you get its \
keyframes and narration. Write {n} different options per clip, best first, each playing on a \
different fan emotion or visible detail. Keep them short (under {chars} characters). The \
campaign rules are the campaign's non-negotiable terms: obey them exactly. Return only JSON \
matching the schema.\
"""


def regenerate_hooks(
    campaign: str,
    video_id: str,
    *,
    model: str,
    only: list[str] | None = None,
    data: Path = Path("data"),
    campaigns_root: Path = Path("campaigns"),
    research_dir: Path = RESEARCH_DIR,
) -> tuple[ClipsFile, dict[str, list[str]]]:
    """Refresh hook_options for clips; cuts, hook_line and approvals stay as they are."""
    c = require_reviewed(campaign, campaigns_root)
    if c.rules.hook_style.mode == "none" or c.rules.on_screen_text.policy == "forbidden":
        raise HookError(f"campaign {campaign!r} uses no hook text")
    clips_path = data / "clips" / f"{video_id}.clips.json"
    clips = ClipsFile.model_validate_json(clips_path.read_text(encoding="utf-8"))
    transcript = Transcript.model_validate_json(
        (data / "transcripts" / f"{video_id}.json").read_text(encoding="utf-8"))
    a_dir = data / "analysis" / video_id
    analysis = ShotAnalysis.model_validate_json((a_dir / "shots.json").read_text(encoding="utf-8"))
    trend = load_trend_brief(campaign, research_dir)
    targets = [x for x in clips.clips if not only or x.id in only]
    if not targets:
        raise HookError("no matching clips")

    content: list[dict[str, Any]] = []
    for clip in targets:
        content.append({"type": "text", "text": (
            f"Clip {clip.id} ({clip.end_s - clip.start_s:.0f} s). Narration: "
            f"\"{clip_narration(transcript, clip)}\". Keyframes:")})
        for s in analysis.shots[clip.start_shot:clip.end_shot + 1]:
            data_b64 = base64.standard_b64encode((a_dir / s.keyframe).read_bytes()).decode()
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": data_b64}})
    rules_part = {"hook_style": c.rules.hook_style.model_dump(mode="json"),
                  "on_screen_text": c.rules.on_screen_text.model_dump(mode="json"),
                  "allowed_facts": c.rules.allowed_facts, "prohibited": c.rules.prohibited,
                  "other_rules": c.rules.other_rules}
    content.append({"type": "text", "text": (
        f"{trend_section(trend)}Campaign rules:\n{json.dumps(rules_part, ensure_ascii=False)}\n\n"
        f"{HOOK_GUIDE[c.rules.hook_style.mode]}\n"
        f"Return hook options for clips: {', '.join(x.id for x in targets)}.")})

    item = create_model("ClipHooks", clip_id=(str, ...), hook_options=(list[str], ...))
    resp_model = create_model("HooksResponse", clips=(list[item], ...))
    resp = llm.parse_structured(
        model=model, system=SYSTEM.format(n=MAX_OPTIONS, chars=MAX_HOOK_CHARS), user=content,
        output_type=resp_model)

    research_n = research_text(trend)
    names = reference_names(trend)
    by_id = {x.id: x for x in targets}
    rejected: dict[str, list[str]] = {}
    for got in resp.clips:
        clip = by_id.get(got.clip_id)
        if clip is None:
            continue
        kept, bad = valid_options(got.hook_options, c=c,
                                  narration=clip_narration(transcript, clip), research_n=research_n,
                                  names=names)
        clip.hook_options = kept
        if not clip.hook_line and kept:
            clip.hook_line = kept[0]
        if bad:
            rejected[clip.id] = bad
    tmp = clips_path.with_name(clips_path.name + ".tmp")
    tmp.write_text(clips.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, clips_path)
    return clips, rejected


def choose_hooks(
    clips_path: Path,
    clip_id: str,
    choices: list[str],
    *,
    data: Path = Path("data"),
    campaigns_root: Path = Path("campaigns"),
    research_dir: Path = RESEARCH_DIR,
) -> list[Clip]:
    """Human choice of hook(s) for a clip. Each choice is an option number (1-based) or own text.

    The first choice becomes the clip's hook_line. Every further choice becomes a variant clip
    `<clip_id>-2`, `-3`...: same cut and approval, different hook, variant_of = clip_id. Earlier
    variants of the clip are replaced. Own text gets the same code checks as generated options.
    """
    if not choices:
        raise HookError("give at least one option number or hook text")
    clips = ClipsFile.model_validate_json(clips_path.read_text(encoding="utf-8"))
    base = next((x for x in clips.clips if x.id == clip_id and x.variant_of is None), None)
    if base is None:
        raise HookError(f"no clip {clip_id!r} in {clips_path} (pick hooks on the original clip)")
    c = require_reviewed(clips.campaign, campaigns_root)
    transcript = Transcript.model_validate_json(
        (data / "transcripts" / f"{clips.video_id}.json").read_text(encoding="utf-8"))
    trend = load_trend_brief(clips.campaign, research_dir)

    hooks: list[str] = []
    for ch in choices:
        if ch.strip().isdigit():
            n = int(ch)
            if not 1 <= n <= len(base.hook_options):
                raise HookError(f"{clip_id} has {len(base.hook_options)} hook options; pick 1-"
                                f"{len(base.hook_options)} or give your own text")
            hooks.append(base.hook_options[n - 1])
            continue
        kept, bad = valid_options([ch], c=c, narration=clip_narration(transcript, base),
                                  research_n=research_text(trend), names=reference_names(trend))
        if bad:
            raise HookError(f"hook rejected: {bad[0]}")
        hooks.append(kept[0])

    base.hook_line = hooks[0]
    variants = [base.model_copy(update={"id": f"{base.id}-{k}", "hook_line": h,
                                        "variant_of": base.id, "title": f"{base.title} (hook {k})"})
                for k, h in enumerate(hooks[1:], start=2)]
    rest = [x for x in clips.clips if x.variant_of != base.id]
    i = rest.index(base) + 1
    clips.clips = rest[:i] + variants + rest[i:]
    tmp = clips_path.with_name(clips_path.name + ".tmp")
    tmp.write_text(clips.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, clips_path)
    return [base, *variants]
