"""campaign add: brief.md -> LLM draft -> evidence check in code -> rules.yaml (status: draft).

The LLM must back every value it sets with a verbatim quote from the brief. Code checks each
quote (and, for numbers and pasted strings, the value itself) against the brief. Anything that
fails is reset to the template default and turned into a question in `unknowns`. Same idea as
clip snapping: never trust the model's claim without a check.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from content_clipper import llm
from content_clipper.campaign.store import (
    CAMPAIGNS_DIR,
    CampaignError,
    campaign_dir,
    write_rules,
)
from content_clipper.models import CampaignRules, CampaignRulesBody

TEMPLATE_RULES = "_template/rules.yaml"

SYSTEM = """\
You turn a campaign brief for short-form video clipping into structured rules. The rules drive an \
automated editing and caption pipeline, and QA blocks posts that break them, so accuracy matters \
more than completeness.

- The brief is the campaign's non-negotiable terms. Apply it exactly as written. Do not question, \
second-guess, soften or flag any of its terms, and do not raise questions about it. Where wording \
could be read two ways, follow its literal wording.
- Record only what the brief states. Where the brief is silent, keep the template default (null, \
empty list, false, unknown, allowed). Never guess and never fill in from general knowledge.
- Leave unknowns empty.
- Evidence: for every value you set differently from the template default, and for every list \
item, add an evidence entry {path, quote}. path uses dotted field names and [i] for list items, \
for example audio.music_allowed, caption.required_mentions[0], prohibited[2]. quote is a span \
copied character for character from the brief (you may shorten it, never reword it). Code checks \
every quote against the brief and throws away values whose quote is not found.
- Text that ends up in posts (mentions, disclosure tags, required line options, example captions, \
forbidden phrases) must be copied exactly as written in the brief.
- Numbers must appear in their quote as written.
- A rule that fits no field goes in other_rules as a short sentence. Never drop a rule.
- Things code cannot check (engagement rate, audience countries, likes visible, how long a post \
stays live, story boosting, paid promotion) go in checklist.
"""


class EvidenceQuote(BaseModel):
    path: str
    quote: str


class RulesDraft(BaseModel):
    """What the LLM returns."""

    rules: CampaignRulesBody
    evidence: list[EvidenceQuote]


@dataclass(frozen=True)
class Rejection:
    path: str
    value: Any
    reason: str


@dataclass(frozen=True)
class VerifiedDraft:
    body: CampaignRulesBody
    evidence: dict[str, str]
    rejections: list[Rejection]
    verified: int


_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_NUM_WORDS = dict(enumerate([
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve",
]))


def norm(text: str) -> str:
    """Case-insensitive, whitespace-collapsed, typographic quotes and dashes made plain."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_TRANS).lower().split())


def fmt_num(value: float) -> str:
    v = float(value)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def number_in(quote_n: str, value: float) -> bool:
    v = float(value)
    s = fmt_num(v)
    if re.search(rf"(?<![\d.]){re.escape(s)}(?!\.?\d)", quote_n):
        return True
    word = _NUM_WORDS.get(int(v)) if v.is_integer() else None
    return bool(word and re.search(rf"\b{word}\b", quote_n))


def has_literal(text_n: str, literal: str) -> bool:
    lit = norm(literal)
    return bool(lit) and re.search(rf"(?<![\w@#]){re.escape(lit)}(?!\w)", text_n) is not None


def _literals(list_path: str, item: Any) -> list[str]:
    """Strings inside a list item that get pasted into posts, so must exist in the brief."""
    if list_path == "caption.required_mentions":
        return [item["text"]]
    if list_path == "caption.required_lines":
        return list(item["options"]) if item.get("verbatim", True) else []
    if list_path in ("caption.disclosure.accepted_tags", "caption.example_captions",
                     "caption.forbidden_phrases"):
        return [item]
    return []


def _norm_path(path: str) -> str:
    return re.sub(r"\.(\d+)(?=\.|$)", r"[\1]", path.strip().replace(" ", ""))


def verify_draft(draft: RulesDraft, brief_text: str) -> VerifiedDraft:
    brief_n = norm(brief_text)
    quotes: dict[str, list[str]] = {}
    for e in draft.evidence:
        quotes.setdefault(_norm_path(e.path), []).append(e.quote)

    evidence: dict[str, str] = {}
    rejections: list[Rejection] = []
    verified = 0

    def check(path: str, value: Any, list_path: str | None) -> tuple[str | None, str]:
        given = quotes.get(path, [])
        if not given:
            return "no evidence quote given", ""
        found = [q for q in given if len(norm(q)) >= 3 and norm(q) in brief_n]
        if not found:
            return f"quote not found in the brief: {given[0]!r}", ""
        quote = found[0]
        is_number = isinstance(value, int | float) and not isinstance(value, bool)
        if is_number and not number_in(norm(quote), value):
            return f"{fmt_num(value)} does not appear in the quote {quote!r}", ""
        for lit in _literals(list_path or "", value):
            if not has_literal(brief_n, lit):
                return f"{lit!r} is not written verbatim in the brief", ""
        return None, quote

    def walk(val: Any, default: Any, path: str) -> Any:
        nonlocal verified
        if isinstance(val, dict):
            return {
                k: walk(v, (default or {}).get(k), f"{path}.{k}" if path else k)
                for k, v in val.items()
            }
        if isinstance(val, list):
            kept = []
            for i, item in enumerate(val):
                reason, quote = check(f"{path}[{i}]", item, path)
                if reason:
                    rejections.append(Rejection(f"{path}[{i}]", item, reason))
                    continue
                evidence[f"{path}[{len(kept)}]"] = quote
                kept.append(item)
                verified += 1
            return kept
        if val == default:
            return val
        reason, quote = check(path, val, None)
        if reason:
            rejections.append(Rejection(path, val, reason))
            return default
        evidence[path] = quote
        verified += 1
        return val

    body = draft.rules.model_dump(mode="json")
    default = CampaignRulesBody().model_dump(mode="json")
    # The brief is not up for question: whatever the model put in unknowns is discarded. unknowns
    # only lists values that failed verification, for the human to fix before approval.
    body.pop("unknowns")
    default.pop("unknowns")
    unknowns: list[str] = []
    checked = walk(body, default, "")
    for r in rejections:
        value = json.dumps(r.value, ensure_ascii=False)
        unknowns.append(
            f"Could not verify {r.path} = {value} ({r.reason}). Set it from brief.md, "
            "then delete this entry."
        )
    checked["unknowns"] = unknowns
    return VerifiedDraft(
        body=CampaignRulesBody.model_validate(checked),
        evidence=evidence,
        rejections=rejections,
        verified=verified,
    )


def request_draft(brief_text: str, *, model: str, template_text: str, today: date) -> RulesDraft:
    user = (
        f"Today's date: {today.isoformat()}\n\n"
        "Field guide: the rules template with comments. Field names, allowed values and "
        f"defaults are here.\n<template>\n{template_text}\n</template>\n\n"
        f"Campaign brief, verbatim:\n<brief>\n{brief_text}\n</brief>\n\n"
        "Return the rules for this brief and the evidence list."
    )
    # The rules schema is too large for grammar-constrained output (API limit), so the
    # reply is validated by pydantic instead, then checked against the brief by verify_draft.
    return llm.parse_structured(
        model=model, system=SYSTEM, user=user, output_type=RulesDraft, constrained=False
    )


HEADER = """\
# Drafted by `content-clipper campaign add` from brief.md. Status stays draft until a human
# checks every value against brief.md, fixes anything listed in unknowns, and runs:
#   uv run content-clipper campaign approve {name}
# null means "the brief does not say". For music, AI voice and source, null means NOT allowed.
"""


def add_campaign(
    name: str,
    brief_bytes: bytes,
    *,
    model: str,
    root: Path = CAMPAIGNS_DIR,
    force: bool = False,
    today: date | None = None,
) -> tuple[CampaignRules, VerifiedDraft]:
    d = campaign_dir(name, root)
    rules_path = d / "rules.yaml"
    if rules_path.exists() and not force:
        raise CampaignError(f"{rules_path} exists; pass --force to redraft (discards your edits)")
    try:
        brief_text = brief_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CampaignError("the brief must be UTF-8 text") from e
    if not brief_text.strip():
        raise CampaignError("the brief is empty")
    template_text = (root / TEMPLATE_RULES).read_text(encoding="utf-8")

    draft = request_draft(
        brief_text, model=model, template_text=template_text, today=today or date.today()
    )
    v = verify_draft(draft, brief_text)
    rules = CampaignRules.model_validate(
        {**v.body.model_dump(), "name": name, "status": "draft", "brief_file": "brief.md",
         "brief_sha256": "", "evidence": v.evidence}
    )
    d.mkdir(parents=True, exist_ok=True)
    (d / "brief.md").write_bytes(brief_bytes)  # verbatim, byte for byte
    write_rules(rules_path, rules, HEADER.format(name=name))
    return rules, v
