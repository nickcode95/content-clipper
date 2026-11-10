"""Research from notes Nick pastes in (allowed by the research-brief skill): notes -> trend brief.

The notes are stored verbatim; the LLM turns them into angles, keywords and hook formulas in new
wording, and lists the angles hooks must avoid because the campaign rules forbid them.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

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
