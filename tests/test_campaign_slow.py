"""Paid test: real LLM drafts rules for the fictional example brief.

Gated by RUN_SLOW=1 because it calls the Anthropic API (a few cents).

    RUN_SLOW=1 uv run pytest tests/test_campaign_slow.py -s
"""

import os
from datetime import date
from pathlib import Path

import pytest

from content_clipper.campaign.draft import request_draft, verify_draft
from content_clipper.config import load_models_config

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SLOW") != "1", reason="paid: set RUN_SLOW=1 (calls the Anthropic API)"
)

REPO = Path(__file__).resolve().parents[1]


def test_real_llm_drafts_example_brief():
    brief = (REPO / "docs" / "examples" / "brief.example.md").read_text(encoding="utf-8")
    template = (REPO / "campaigns" / "_template" / "rules.yaml").read_text(encoding="utf-8")
    model = load_models_config(REPO / "config" / "models.yaml").selection

    draft = request_draft(brief, model=model, template_text=template, today=date(2026, 11, 10))
    v = verify_draft(draft, brief)
    print(f"verified {v.verified}, rejected {len(v.rejections)}")
    for r in v.rejections:
        print("  rejected", r.path, r.reason)

    b = v.body
    assert b.audio.music_allowed is False
    assert b.audio.ai_voice_allowed is False
    assert b.source.policy == "official_only"
    assert [m.text for m in b.caption.required_mentions] == ["@acmestudio"]
    assert b.caption.disclosure.accepted_tags == ["#Ad"]
    assert b.video.min_length_s == 8
    assert set(b.platforms) == {"tiktok", "instagram"}
