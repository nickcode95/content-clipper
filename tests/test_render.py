"""Render stage: geometry, captions, hook rules, and a real ffmpeg render of synthetic media."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from content_clipper.campaign import store
from content_clipper.config import load_render_config
from content_clipper.media import probe
from content_clipper.models import (
    CampaignRules,
    Clip,
    ClipsFile,
    Segment,
    Transcript,
    Word,
)
from content_clipper.render.core import (
    RelWord,
    RenderError,
    ass_colour,
    band_geometry,
    build_ass,
    caption_groups,
    clip_words,
    fit_size,
    hook_for,
    render_approved,
    set_approval,
    text_boxes,
    wrap,
)

REPO = Path(__file__).resolve().parents[1]
CFG = load_render_config(REPO / "config/render.yaml")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def cfg_with(**layout):
    return CFG.model_copy(update={"layout": CFG.layout.model_copy(update=layout)})


def test_band_geometry_16x9_hook_below():
    g = band_geometry(CFG, 3840, 2160)
    assert (g.video_w, g.video_h, g.video_x) == (1080, 608, 0)
    assert (g.safe.top, g.safe.bottom) == (192, 1536)
    assert g.captions.bottom == g.video_y and g.hook.top == g.video_y + g.video_h
    assert g.hook.bottom == g.safe.bottom and g.captions.top == g.safe.top


def test_band_geometry_hook_above_and_tall_source():
    g = band_geometry(cfg_with(hook_position="above"), 1920, 1080)
    assert g.hook.bottom == g.video_y
    tall = band_geometry(CFG, 1080, 1920)  # vertical source: fit height, keep text room
    assert tall.video_h == 1344 - 300 and tall.video_w < 1080
    assert tall.hook.height >= 150 and tall.captions.height >= 150


def test_wrap_and_fit():
    assert wrap(["aaaa", "bbbb", "cccc"], 10, 60) == [[0, 1], [2]]
    long = "Ghost briefs the squad on the new DMZ location and everything that waits inside it"
    from content_clipper.render.core import Box
    assert fit_size(long, 84, Box(0, 150), 940) < 84


def test_caption_groups_break_on_size_gap_and_sentence():
    ws = [RelWord(w, s, s + 0.3, seg) for w, s, seg in [
        ("a", 0.0, 0), ("b", 0.4, 0), ("c", 0.8, 0), ("d", 1.2, 0), ("e", 1.6, 0),  # 5 > 4
        ("f", 3.0, 0),  # gap > 0.6 s
        ("g", 3.4, 1),  # new sentence
    ]]
    assert [[w.w for w in g] for g in caption_groups(ws, 4)] == [
        ["a", "b", "c", "d"], ["e"], ["f"], ["g"]]


def transcript():
    words = [Word(seg=0, w=w, start=s, end=e) for w, s, e in [
        ("before", 0.2, 0.6), ("North", 1.1, 1.4), ("Korea", 1.5, 1.9), ("owns", 2.0, 2.3),
        ("it", 2.35, 2.5), ("now.", 2.55, 2.9), ("after", 3.6, 3.9)]]
    return Transcript(video="v.mp4", video_id="v", duration_s=4.0, language="en", model="small",
                      segments=[Segment(id=0, start=0.2, end=3.9, text="...")], words=words)


def clip(**kw):
    base = {"id": "c01", "start_s": 1.0, "end_s": 3.2, "start_shot": 0, "end_shot": 0,
            "hook_line": "Ghost briefs the squad on X01", "title": "t", "score": 7.0,
            "scores": {}, "reason": "r", "approved": True}
    return Clip.model_validate({**base, **kw})


def test_clip_words_are_relative_and_inside_the_clip():
    ws = clip_words(transcript(), clip())
    assert [w.w for w in ws] == ["North", "Korea", "owns", "it", "now."]
    assert ws[0].start == pytest.approx(0.1)


def test_build_ass_highlights_one_word_per_event_inside_safe_area():
    g = band_geometry(CFG, 1920, 1080)
    subs = build_ass(clip_words(transcript(), clip()), "Ghost briefs the squad on X01", g, CFG,
                     2.2)
    caps = [e for e in subs.events if e.style == "Caption"]
    hook = [e for e in subs.events if e.style == "Hook"]
    assert len(caps) == 5 and len(hook) == 1
    hl = ass_colour(CFG.captions.highlight_color)
    assert hl == "&H00D4FF&"
    assert caps[0].text.count(hl) == 1 and f"{{\\c{hl}}}North" in caps[0].text
    assert f"{{\\c{hl}}}Korea" in caps[1].text
    assert hook[0].start == 0 and hook[0].end == 2200  # whole clip when hook.seconds is 0
    assert all(g.safe.top <= top and bottom <= g.safe.bottom for top, bottom in text_boxes(subs))


def test_hook_respects_campaign_rules():
    rules = CampaignRules.model_validate(
        {"name": "x", "on_screen_text": {"policy": "forbidden"}})
    assert hook_for(clip(), rules) is None
    rules = CampaignRules.model_validate({"name": "x", "hook_style": {"mode": "none"}})
    assert hook_for(clip(), rules) is None
    assert hook_for(clip(), CampaignRules(name="x")) == "Ghost briefs the squad on X01"
    assert hook_for(clip(hook_line="  "), CampaignRules(name="x")) is None


# ---------------------------------------------------------------- real render, synthetic media


@pytest.fixture
def ws(tmp_path, monkeypatch):
    camp = tmp_path / "campaigns" / "acme-launch-trailer"
    camp.mkdir(parents=True)
    shutil.copy(REPO / "docs/examples/brief.example.md", camp / "brief.md")
    shutil.copy(REPO / "docs/examples/rules.example.yaml", camp / "rules.yaml")
    store.approve("acme-launch-trailer", tmp_path / "campaigns")
    src = tmp_path / "data/sources/v.mp4"
    src.parent.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=s=1280x720:r=30:d=4", "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)],
        check=True)
    (tmp_path / "data/sources/registry.csv").write_text(
        "video_id,origin,campaign,downloaded_at,notes\nv,folder,acme-launch-trailer,,\n")
    t = transcript().model_copy(update={"video": "data/sources/v.mp4"})
    (tmp_path / "data/transcripts").mkdir(parents=True)
    (tmp_path / "data/transcripts/v.json").write_text(t.model_dump_json())
    clips = ClipsFile(video_id="v", campaign="acme-launch-trailer", generated_at="now",
                      model="m", clips=[clip(approved=None), clip(id="c02", approved=None)])
    (tmp_path / "data/clips").mkdir(parents=True)
    (tmp_path / "data/clips/v.clips.json").write_text(clips.model_dump_json())
    monkeypatch.chdir(tmp_path)
    return tmp_path


@needs_ffmpeg
def test_render_needs_approval_then_renders_band_layout(ws):
    with pytest.raises(RenderError, match="no approved clips"):
        render_approved("acme-launch-trailer", "v", cfg=CFG)
    set_approval(ws / "data/clips/v.clips.json", ["c01"], True)
    done = render_approved("acme-launch-trailer", "v", cfg=CFG)
    assert len(done) == 1
    r = done[0]
    assert abs(r.duration_s - 2.2) <= 0.1
    info = probe(r.path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"], v["pix_fmt"]) == (1080, 1920, "yuv420p")
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    out = ws / "data/out/acme-launch-trailer/v"
    meta = json.loads((out / "c01.render.json").read_text())
    assert meta["music"] is None and meta["ai_voice"] is False
    assert meta["hook_text"] == "Ghost briefs the squad on X01"
    assert (out / "c01.ass").exists() and (out / "c01.log").exists()


def test_set_approval_rejects_unknown_ids(ws):
    with pytest.raises(RenderError, match="unknown clip"):
        set_approval(ws / "data/clips/v.clips.json", ["c09"], True)


def test_render_refuses_unregistered_source(ws):
    (ws / "data/sources/registry.csv").write_text("video_id,origin,campaign,downloaded_at,notes\n")
    set_approval(ws / "data/clips/v.clips.json", ["c01"], True)
    with pytest.raises(RenderError, match="not registered"):
        render_approved("acme-launch-trailer", "v", cfg=CFG)
