"""Tweet-style hook cards: personas, handles, card layout, and real renders (no network)."""

import itertools
import json
import random
import re
import shutil

import pytest
from PIL import Image

from content_clipper.render import tweet
from content_clipper.render.core import render_approved, set_approval
from tests.test_render import CFG, ws  # noqa: F401  (fixture)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def personas(tmp_path):
    d = tmp_path / "personas"
    d.mkdir()
    Image.new("RGB", (175, 131), (120, 80, 40)).save(d / "hagrids_beard.webp")
    Image.new("RGB", (64, 64), (20, 20, 200)).save(d / "space_cadet.png")
    (d / "notes.txt").write_text("ignored")
    return d


def test_personas_come_from_images_with_names(personas):
    (personas / "names.yaml").write_text("hagrids_beard: Hagrid's Beard\n")
    ps = tweet.load_personas(personas)
    assert [(p.base, p.display_name) for p in ps] == [
        ("hagrids_beard", "Hagrid's Beard"), ("space_cadet", "Space Cadet")]


def test_no_persona_images_is_a_clear_error(tmp_path):
    with pytest.raises(tweet.TweetError, match="no persona images"):
        tweet.load_personas(tmp_path)


def test_each_pick_gets_a_new_handle_and_rotates_personas(personas, tmp_path):
    ps = tweet.load_personas(personas)
    hist = tmp_path / "used.json"
    rng = random.Random(1)
    picks = [tweet.pick_persona(ps, hist, recent=1, rng=rng) for _ in range(4)]
    bases = [p.base for p, _ in picks]
    assert all(a != b for a, b in itertools.pairwise(bases))  # never the same persona twice
    handles = [h for _, h in picks]
    assert len(set(handles)) == 4
    assert all(re.fullmatch(r"(hagrids_beard|space_cadet)_\d{5}", h) for h in handles)
    assert json.loads(hist.read_text()) == bases


def test_card_size_shrinks_text_to_fit_and_refuses_essays(personas):
    p = tweet.load_personas(personas)[0]
    card = tweet.make_card("calling in sick today for the last day of free dmz", p, "x_12345",
                           width=950, max_height=360)
    assert card.size[0] == 950 and card.size[1] <= 360 and card.mode == "RGBA"
    assert card.getpixel((0, 0))[3] == 0  # rounded corner is transparent
    long = " ".join(["word"] * 40)
    small = tweet.make_card(long, p, "x_1", width=950, max_height=420)
    assert small.size[1] <= 420
    with pytest.raises(tweet.TweetError, match="too long"):
        tweet.make_card(" ".join(["word"] * 200), p, "x_1", width=950, max_height=300)


@needs_ffmpeg
def test_tweet_render_in_both_layouts_adds_files_only(ws, personas):  # noqa: F811
    from content_clipper.models import ShotAnalysis
    from tests.test_render import shot

    adir = ws / "data/analysis/v"
    adir.mkdir(parents=True)
    a = ShotAnalysis(video="data/sources/v.mp4", video_id="v", duration_s=4.0, analysis_fps=30,
                     shots=[shot(0, 0, 4, None)])
    (adir / "shots.json").write_text(a.model_dump_json())
    set_approval(ws / "data/clips/v.clips.json", ["c01"], True)

    plain = render_approved("acme-launch-trailer", "v", cfg=CFG)
    stamp = plain[0].path.stat().st_mtime_ns
    band = render_approved("acme-launch-trailer", "v", cfg=CFG, hook_style="tweet",
                           personas_dir=personas)
    crop = render_approved("acme-launch-trailer", "v", cfg=CFG, hook_style="tweet",
                           layout="crop", personas_dir=personas)
    assert [r.path.name for r in plain + band + crop] == [
        "c01.mp4", "c01.tweet.mp4", "c01.crop.tweet.mp4"]
    assert plain[0].path.stat().st_mtime_ns == stamp
    for r in band + crop:
        t = r.render_json["tweet"]
        assert t["text"] == "Ghost briefs the squad on X01" and re.fullmatch(r"\w+_\d{5}", t["handle"])
        assert r.render_json["hook_style"] == "tweet"
        assert abs(r.duration_s - 2.2) <= 0.1
    out = ws / "data/out/acme-launch-trailer/v"
    assert (out / "c01.tweet.card.png").exists()
    ass = (out / "c01.tweet.ass").read_text()
    assert "Ghost briefs" not in ass  # the hook is in the card, not drawn twice as text
