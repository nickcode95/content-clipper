"""Research from notes Nick pastes in (allowed by the research-brief skill): notes -> trend brief.

The notes are stored verbatim; the LLM turns them into angles, keywords and hook formulas in new
wording, and lists the angles hooks must avoid because the campaign rules forbid them.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps
from pydantic import BaseModel

from content_clipper import llm
from content_clipper.campaign.store import CAMPAIGNS_DIR, CampaignError, load_campaign
from content_clipper.models import TrendBrief, TrendBriefBody

RESEARCH_DIR = Path("data/research")

SYSTEM = """\
You turn audience research notes into a trend brief that helps write hooks (short on-screen \
lines) for clips in a sponsored short-form video campaign. Hooks that perform play on what fans \
are anticipating and reacting to: speculation, excitement, intrigue, nostalgia or frustration, \
often as a casual first-person fan reaction (for example "did i just see <old-school gun>?").

- fan_references: concrete things fans recognise and react to (returning or new guns, old zones \
and maps, characters, modes), each with why fans care and the emotion it triggers.
- patterns: angles this audience responds to that a hook could lean into, each with why it \
works. Summarise in your own words; never copy distinctive phrasing from the notes.
- keywords: words and names this audience uses for the subject.
- emoji_patterns: how the top posts in the notes use emoji (which ones, where in the line, how \
many), as reusable patterns. Empty if the notes show no emoji use.
- Notes may include readouts of screenshots of top-performing posts, with their format, text \
and visible stats. Treat those as evidence of what performs; copy patterns, never their wording.
- hook_formulas: reusable hook templates in new wording and a fan's voice, with placeholders \
like <gun>, <old zone> or <moment>, e.g. a reaction, a question or a comparison to the past.
- avoid: angles that hooks must not use because they would break the campaign rules below \
(for example if the rules ban rage bait, hate bait or controversy, the complaints that would \
start arguments go here), plus review scores, prices, player counts and dates, which a clip \
cannot show. Frustration that the rules allow is fine as a relatable fan wish.

The campaign rules are the campaign's non-negotiable terms. Apply them; never question them.\
"""


def trend_path(campaign: str, research_dir: Path = RESEARCH_DIR) -> Path:
    return research_dir / f"{campaign}.trend_brief.json"


def load_trend_brief(campaign: str, research_dir: Path = RESEARCH_DIR) -> TrendBrief | None:
    p = trend_path(campaign, research_dir)
    return TrendBrief.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


SEPARATOR = "\n\n---\n\n"


def add_notes(
    campaign: str,
    notes: bytes,
    *,
    model: str,
    replace: bool = False,
    research_dir: Path = RESEARCH_DIR,
    campaigns_root: Path = CAMPAIGNS_DIR,
) -> TrendBrief:
    """Add notes (kept verbatim) and rebuild the trend brief from ALL notes for the campaign."""
    rules = load_campaign(campaign, campaigns_root).rules
    new = notes.decode("utf-8").strip()
    if not new:
        raise CampaignError("the research notes are empty")
    notes_path = research_dir / f"{campaign}.notes.md"
    old = "" if replace or not notes_path.exists() else notes_path.read_text(encoding="utf-8")
    text = f"{old.rstrip()}{SEPARATOR}{new}" if old.strip() else new
    rules_part = {
        "prohibited": rules.prohibited, "other_rules": rules.other_rules,
        "hook_style": rules.hook_style.model_dump(mode="json"),
        "on_screen_text": rules.on_screen_text.model_dump(mode="json"),
        "caption_style_notes": rules.caption.style_notes, "allowed_facts": rules.allowed_facts,
    }
    user = (f"Campaign rules:\n{json.dumps(rules_part, ensure_ascii=False, indent=1)}\n\n"
            f"Research notes:\n<notes>\n{text}\n</notes>\n\nReturn the trend brief.")
    body = llm.parse_structured(model=model, system=SYSTEM, user=user, output_type=TrendBriefBody)

    research_dir.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(text + "\n", encoding="utf-8")
    brief = TrendBrief(campaign=campaign, generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
                       sources=[notes_path.as_posix()], **body.model_dump())
    out = trend_path(campaign, research_dir)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(brief.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return brief


# ---------------------------------------------------------------- screenshots of top posts

MAX_EDGE = 1568  # long edge the API handles best


class ScreenshotPost(BaseModel):
    file: str
    format: str  # e.g. "tweet-style card under the video", "plain hook text over the video"
    on_screen_text: list[str]  # exactly as shown, emoji included
    caption_text: str  # post caption exactly as shown, or ""
    emojis: list[str]
    visible_stats: str  # views, likes etc. exactly as shown, or ""
    why_it_works: str


class ScreenshotReadout(BaseModel):
    posts: list[ScreenshotPost]


SCREENSHOT_SYSTEM = """\
You read screenshots of top-performing short-form video posts (TikTok, Instagram Reels, \
YouTube Shorts). For each screenshot, describe the post format (for example a tweet-style card \
under the video, plain hook text, a caption only), transcribe the on-screen text and the post \
caption exactly as shown, keeping emoji, list the emoji used, copy any visible views, likes or \
comments exactly as shown (never estimate), and say briefly why the post likely works. \
Use the file names you are given. Return only JSON matching the schema.\
"""


def prepare_image(path: Path) -> bytes:
    """JPEG bytes, long edge at most MAX_EDGE, EXIF orientation applied."""
    img = ImageOps.exif_transpose(Image.open(path))
    img.seek(0)
    img = img.convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def readout_text(readout: ScreenshotReadout, day: str) -> str:
    lines = [f"Screenshots of top-performing posts (added {day}):"]
    for p in readout.posts:
        lines.append(f"- {p.file}: format: {p.format}")
        for t in p.on_screen_text:
            lines.append(f"  on-screen text: {t}")
        if p.caption_text:
            lines.append(f"  caption: {p.caption_text}")
        if p.emojis:
            lines.append(f"  emoji: {' '.join(p.emojis)}")
        if p.visible_stats:
            lines.append(f"  stats: {p.visible_stats}")
        lines.append(f"  why it works: {p.why_it_works}")
    return "\n".join(lines)


def add_research(
    campaign: str,
    *,
    model: str,
    notes: bytes | None = None,
    images: list[Path] = (),
    replace: bool = False,
    research_dir: Path = RESEARCH_DIR,
    campaigns_root: Path = CAMPAIGNS_DIR,
) -> TrendBrief:
    """Notes and/or screenshots -> notes file (accumulated) -> rebuilt trend brief."""
    load_campaign(campaign, campaigns_root)  # the campaign must exist
    parts = [notes.decode("utf-8").strip()] if notes and notes.strip() else []
    if images:
        missing = [str(p) for p in images if not p.is_file()]
        if missing:
            raise CampaignError(f"screenshot(s) not found: {missing}")
        day = datetime.now(UTC).date().isoformat()
        shots_dir = research_dir / campaign / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        content: list[dict] = []
        names = []
        for i, path in enumerate(images, start=1):
            name = f"{day}_{i:02d}_{path.stem}{path.suffix.lower()}"
            shutil.copy(path, shots_dir / name)
            names.append(name)
            data = base64.standard_b64encode(prepare_image(path)).decode()
            content += [{"type": "text", "text": f"Screenshot file: {name}"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": data}}]
        content.append({"type": "text", "text": "Read every screenshot above."})
        readout = llm.parse_structured(model=model, system=SCREENSHOT_SYSTEM, user=content,
                                       output_type=ScreenshotReadout)
        parts.append(readout_text(readout, day))
    if not parts:
        raise CampaignError("give research notes and/or screenshots")
    return add_notes(campaign, "\n\n".join(parts).encode(), model=model, replace=replace,
                     research_dir=research_dir, campaigns_root=campaigns_root)
