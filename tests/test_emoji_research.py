"""Emoji in on-screen text, and screenshots of top posts as research (fake LLM only)."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from content_clipper import llm
from content_clipper.models import ClipsFile, TrendBriefBody
from content_clipper.render import richtext as rt
from content_clipper.render.core import render_approved, set_approval
from content_clipper.research.manual import (
    ScreenshotReadout,
    add_research,
    load_trend_brief,
)
from tests.test_render import CFG, ws  # noqa: F401  (fixture)

has_emoji_font = pytest.mark.skipif(
    "emoji" not in rt.font_file("Noto Color Emoji").lower(), reason="Noto Color Emoji missing")


@pytest.mark.parametrize("text, runs", [
    ("last day 🤒🎮", [("last day ", False), ("🤒", True), ("🎮", True)]),
    ("dev 👨‍💻 ok", [("dev ", False), ("👨‍💻", True), (" ok", False)]),
    ("👍🏽🇦🇺", [("👍🏽", True), ("🇦🇺", True)]),
    ("step 1️⃣", [("step ", False), ("1️⃣", True)]),
    ("no emoji here", [("no emoji here", False)]),
])
def test_split_runs_keeps_emoji_clusters_whole(text, runs):
    assert rt.split_runs(text) == runs
    assert rt.has_emoji(text) is any(e for _, e in runs)


def _colourful(img: Image.Image) -> int:
    px = img.convert("RGBA").get_flattened_data()
    return sum(1 for r, g, b, a in px if a > 200 and max(r, g, b) - min(r, g, b) > 80)


@has_emoji_font
def test_text_block_draws_colour_emoji_and_wraps():
    rf = rt.RichFont(rt.font_file("DejaVu Sans:bold"), 64)
    plain = rt.text_block("did i just see the PPSh", rf, max_width=940, fill=(255, 255, 255, 255))
    emoji = rt.text_block("did i just see the PPSh 😭🔥", rf, max_width=940,
                          fill=(255, 255, 255, 255))
    assert _colourful(plain) == 0 and _colourful(emoji) > 200
    assert emoji.width <= 940 + 20


@has_emoji_font
def test_band_render_draws_an_emoji_hook_as_an_image(ws):  # noqa: F811
    path = ws / "data/clips/v.clips.json"
    cf = ClipsFile.model_validate_json(path.read_text())
    cf.clips[0].hook_line = "did i just see that 😭🔥"
    path.write_text(cf.model_dump_json())
    set_approval(path, ["c01"], True)
    r = render_approved("acme-launch-trailer", "v", cfg=CFG)[0]
    assert r.path.name == "c01.mp4" and r.render_json["hook_text"] == "did i just see that 😭🔥"
    ass = (ws / "data/out/acme-launch-trailer/v/c01.ass").read_text()
    assert "did i just see" not in ass  # drawn as an image, not by libass (no emoji boxes)


def test_screenshots_become_notes_and_feed_the_trend_brief(tmp_path, monkeypatch):
    camp = tmp_path / "campaigns" / "acme-launch-trailer"
    camp.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    shutil.copy(repo / "docs/examples/brief.example.md", camp / "brief.md")
    shutil.copy(repo / "docs/examples/rules.example.yaml", camp / "rules.yaml")
    shots = tmp_path / "in"
    shots.mkdir()
    Image.new("RGB", (3000, 2000), (200, 30, 30)).save(shots / "big.png")
    Image.new("RGB", (400, 700), (30, 30, 200)).save(shots / "post.webp")

    calls = []

    def fake(**kw):
        calls.append(kw)
        if kw["output_type"] is ScreenshotReadout:
            return ScreenshotReadout.model_validate({"posts": [
                {"file": "a", "format": "tweet-style card under the video",
                 "on_screen_text": ["no way they brought this back 😭"], "caption_text": "",
                 "emojis": ["😭"], "visible_stats": "2.1M views", "why_it_works": "nostalgia"}]})
        return TrendBriefBody(keywords=["x"], patterns=[], hook_formulas=[], avoid=[],
                              emoji_patterns=["one crying-laughing emoji at the end"])

    monkeypatch.setattr(llm, "parse_structured", fake)
    tb = add_research("acme-launch-trailer", model="m", notes=b"Some notes.",
                      images=[shots / "big.png", shots / "post.webp"],
                      research_dir=tmp_path / "research", campaigns_root=tmp_path / "campaigns")

    images = [b for b in calls[0]["user"] if b["type"] == "image"]
    assert len(images) == 2 and all(b["source"]["media_type"] == "image/jpeg" for b in images)
    import base64
    import io
    big = Image.open(io.BytesIO(base64.b64decode(images[0]["source"]["data"])))
    assert max(big.size) == 1568
    copied = sorted(p.name for p in (tmp_path / "research/acme-launch-trailer/screenshots").iterdir())
    assert len(copied) == 2 and copied[0].endswith("_01_big.png")
    notes = (tmp_path / "research/acme-launch-trailer.notes.md").read_text()
    assert "Some notes." in notes and "no way they brought this back 😭" in notes
    assert "stats: 2.1M views" in notes
    assert tb.emoji_patterns == ["one crying-laughing emoji at the end"]
    assert load_trend_brief("acme-launch-trailer", tmp_path / "research") == tb
    assert "no way they brought this back" in calls[1]["user"]  # brief is built from the readout
    assert json.loads(tb.model_dump_json())["emoji_patterns"]
