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


# ---------------------------------------------------------------- crop layout


def shot(i, start, end, focus):
    from content_clipper.models import Shot

    return Shot(id=i, start=start, end=end, keyframe=f"shot_{i:03d}.jpg", loudness_db=-20,
                motion=0.02, brightness=0.3, focus_x=focus)


def test_crop_plan_follows_each_shot_and_clamps_to_the_frame():
    from content_clipper.render.core import crop_plan, crop_x_expr

    shots = [shot(0, 0, 2, 0.9), shot(1, 2, 5, None), shot(2, 5, 8, 0.0), shot(3, 8, 9, 0.9)]
    plan = crop_plan(3840, 2160, shots, clip(start_s=1.0, end_s=7.0))
    assert (plan.w, plan.h, plan.y) == (1216, 2160, 0)
    # shot 0: right side, clamped so the crop stays inside the frame; shot 1: centre; shot 2: left
    assert plan.xs == [(0.0, 2624), (1.0, 1312), (4.0, 0)]
    assert crop_x_expr(plan) == "2624+(-1312)*gte(t,1.000)+(-1312)*gte(t,4.000)"


def test_crop_plan_for_a_vertical_source_crops_the_height():
    from content_clipper.render.core import crop_plan

    plan = crop_plan(1080, 2400, [], clip())
    assert (plan.w, plan.h) == (1080, 1920) and plan.xs == [(0.0, 0)] and plan.y == 240


def test_crop_captions_sit_inside_the_safe_area():
    from content_clipper.render.core import crop_geometry

    g = crop_geometry(CFG)
    subs = build_ass(clip_words(transcript(), clip()), None, g, CFG, 2.2)
    assert all(e.style == "Caption" for e in subs.events)
    assert all(g.safe.top <= top and bottom <= g.safe.bottom for top, bottom in text_boxes(subs))


@needs_ffmpeg
def test_crop_render_adds_files_without_touching_band_renders(ws):
    from content_clipper.models import ShotAnalysis

    adir = ws / "data/analysis/v"
    adir.mkdir(parents=True)
    a = ShotAnalysis(video="data/sources/v.mp4", video_id="v", duration_s=4.0, analysis_fps=30,
                     shots=[shot(0, 0, 2, 0.8), shot(1, 2, 4, None)])
    (adir / "shots.json").write_text(a.model_dump_json())
    clips_path = ws / "data/clips/v.clips.json"
    cf = ClipsFile.model_validate_json(clips_path.read_text())
    cf.clips[1] = cf.clips[1].model_copy(update={"variant_of": "c01", "id": "c01-2"})
    clips_path.write_text(cf.model_dump_json())
    set_approval(clips_path, ["c01", "c01-2"], True)

    band = render_approved("acme-launch-trailer", "v", cfg=CFG)
    assert [r.path.name for r in band] == ["c01.mp4", "c01-2.mp4"]
    before = band[0].path.stat().st_mtime_ns

    crop = render_approved("acme-launch-trailer", "v", cfg=CFG, layout="crop")
    assert [r.path.name for r in crop] == ["c01.crop.mp4"]  # hook variant skipped: no hook here
    assert band[0].path.stat().st_mtime_ns == before
    info = probe(crop[0].path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    meta = json.loads((ws / "data/out/acme-launch-trailer/v/c01.crop.render.json").read_text())
    assert meta["layout"] == "crop" and meta["hook_text"] is None
    assert [x for _, x in meta["crop"]["xs"]] == [822, 438]  # 404 px crop: focus 0.8, then centre
    assert abs(crop[0].duration_s - 2.2) <= 0.1


# ---------------------------------------------------------------- outro


def test_outro_audio_stops_before_speech_inside_it():
    from content_clipper.models import Outro
    from content_clipper.render.core import (
        SPEECH_MARGIN_S,
        outro_audio_until,
        outro_speech,
    )

    t = transcript()  # words: before 0.2-0.6 ... now. 2.55-2.9, after 3.6-3.9
    quiet = Outro(video_id="v", start_s=3.0, end_s=3.5)
    assert outro_speech(quiet, t) == [] and outro_audio_until(quiet, t) == 0.5
    talky = Outro(video_id="v", start_s=3.0, end_s=4.0)
    assert [w.w for w in outro_speech(talky, t)] == ["after"]
    assert outro_audio_until(talky, t) == pytest.approx(0.6 - SPEECH_MARGIN_S)
    assert outro_audio_until(Outro(video_id="v", start_s=0.0, end_s=1.0), t) == pytest.approx(0.05)
    with pytest.raises(ValueError, match="shorter than"):
        Outro(video_id="v", start_s=2.0, end_s=2.1)


def test_outro_graph_mutes_when_speech_starts_at_once():
    from content_clipper.render.core import outro_graph

    g = band_geometry(CFG, 1920, 1080)
    graph = outro_graph(g, CFG, 1, clip_dur=2.2, outro_dur=1.0, audio_until=0.45, clip_tail=0.06)
    # L-cut: video hard-cuts, the clip's sound runs 60 ms under the outro, the outro's is untouched
    assert "[mvs][ov]concat=n=2:v=1:a=0[v]" in graph
    assert "[0:a:0]apad,atrim=end=2.2600," in graph
    assert "acrossfade=d=0.0600:c1=tri:c2=nofade[cat]" in graph
    assert "afade=t=out:st=0.150:d=0.300" in graph and "[1:v]" in graph and "[1:a:0]" in graph
    assert graph.endswith("[a]") and "ass=" not in graph  # no text on the outro
    # a cut between frames: video and audio both end on the same whole frame (no gap at the join)
    odd = outro_graph(g, CFG, 1, clip_dur=22.067, outro_dur=2.033, audio_until=1.21)
    assert "[mv]trim=end_frame=662," in odd and "atrim=end=22.1267," in odd  # 662/30 + 0.06
    assert "trim=end_frame=61," in odd and "atrim=end=2.0333," in odd
    assert "volume=0" in outro_graph(g, CFG, 2, clip_dur=2.2, outro_dur=1.0, audio_until=0.0)


def test_clip_tail_stays_clear_of_the_next_word():
    from content_clipper.render.core import MIN_FADE_S, TAIL_MARGIN_S, clip_tail

    t = transcript()  # ... it 2.35-2.5, now. 2.55-2.9, after 3.6-3.9
    assert clip_tail(t, clip(end_s=3.2), 0.06) == 0.06  # "after" is far away
    # "it" spans the cut and is ignored (the human chose that cut); "now." at 2.55 s limits it
    assert clip_tail(t, clip(end_s=2.45), 0.06) == pytest.approx(0.05)
    assert clip_tail(t, clip(end_s=2.5), 0.06) == MIN_FADE_S  # "now." starts 50 ms later
    assert clip_tail(t, clip(end_s=2.4), 0.2) == pytest.approx(2.55 - 2.4 - TAIL_MARGIN_S)
    assert clip_tail(t, clip(end_s=3.95), 0.06) == 0.06  # nothing spoken after the cut


def test_save_outro_checks_the_video(tmp_path):
    from content_clipper.models import Outro
    from content_clipper.render.core import load_outro, save_outro

    t = transcript()
    with pytest.raises(RenderError, match="only 4.000 s long"):
        save_outro(Outro(video_id="v", start_s=3.0, end_s=5.0), t, tmp_path)
    with pytest.raises(RenderError, match="transcript for"):
        save_outro(Outro(video_id="w", start_s=0.0, end_s=1.0), t, tmp_path)
    assert load_outro("v", tmp_path) is None
    save_outro(Outro(video_id="v", start_s=3.0, end_s=4.0), t, tmp_path)
    assert load_outro("v", tmp_path).end_s == 4.0


def _rms_db(path, start, dur):
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", str(path),
         "-af", "astats=measure_perchannel=none", "-f", "null", "-"],
        capture_output=True, text=True, check=True).stderr
    level = [ln.split(":")[-1].strip() for ln in out.splitlines() if "RMS level dB" in ln][-1]
    return float("-inf") if level == "-inf" else float(level)


@needs_ffmpeg
def test_render_appends_the_outro_in_both_layouts(ws):
    from content_clipper.models import Outro
    from content_clipper.render.core import save_outro

    set_approval(ws / "data/clips/v.clips.json", ["c01"], True)
    # source 3.0-4.0 s: "after" is spoken at 3.6 s, so the outro audio must be silent by then
    save_outro(Outro(video_id="v", start_s=3.0, end_s=4.0), transcript(), ws / "data")
    [r] = render_approved("acme-launch-trailer", "v", cfg=CFG)
    assert abs(r.duration_s - 3.2) <= 0.1  # 2.2 s clip + 1.0 s outro
    assert r.render_json["outro"] == {"start_s": 3.0, "end_s": 4.0, "duration_s": 1.0,
                                      "audio_until_s": 0.45,
                                      "clip_tail_s": CFG.outro.clip_tail_s}
    assert _rms_db(r.path, 2.17, 0.08) > -30  # no dropout at the join (2.2 s)
    assert _rms_db(r.path, 2.25, 0.15) > -40  # outro sound plays ...
    assert _rms_db(r.path, 2.75, 0.4) < -60  # ... and is silent before the spoken word

    adir = ws / "data/analysis/v"
    adir.mkdir(parents=True)
    from content_clipper.models import ShotAnalysis

    a = ShotAnalysis(video="data/sources/v.mp4", video_id="v", duration_s=4.0, analysis_fps=30,
                     shots=[shot(0, 0, 4, 0.5)])
    (adir / "shots.json").write_text(a.model_dump_json())
    [c] = render_approved("acme-launch-trailer", "v", cfg=CFG, layout="crop")
    assert abs(c.duration_s - 3.2) <= 0.1 and c.render_json["outro"]["end_s"] == 4.0

    [plain] = render_approved("acme-launch-trailer", "v", cfg=CFG, use_outro=False)
    assert abs(plain.duration_s - 2.2) <= 0.1 and plain.render_json["outro"] is None
