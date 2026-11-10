"""Hook options: code checks, research ingest, regeneration and picking (fake LLM only)."""

import shutil
from pathlib import Path

import pytest

from content_clipper import llm
from content_clipper.campaign import store
from content_clipper.campaign.draft import norm
from content_clipper.models import ClipsFile, TrendBrief, TrendBriefBody
from content_clipper.research.manual import add_notes, load_trend_brief
from content_clipper.select.hooks import (
    HookError,
    choose_hooks,
    hook_problems,
    regenerate_hooks,
    trend_section,
)

REPO = Path(__file__).resolve().parents[1]
CAMPAIGN = "acme-launch-trailer"
GROUNDED = norm("Ghost briefs the squad on X01 in MW4 Season 1. Free to play until November 11th")
NOTES = "Critics call the mode unpredictable and chaotic. It scored 84 on Metacritic."


@pytest.mark.parametrize("hook, ok", [
    ("Ghost briefs the squad on X01", True),
    ("New in MW4 Season 1: X01", True),
    ("DMZ is free until November 11th", True),
    ("Rated 84 on Metacritic", False),  # number not in clip, brief or allowed facts
    ("Only $70 for the best mode", False),
    ("", False),
    ("x" * 81, False),
    ("Critics call the mode unpredictable and chaotic", False),  # copies the notes
])
def test_hook_problems(hook, ok):
    probs = hook_problems(hook, grounded_n=GROUNDED, forbidden=["gameplay"],
                          research_n=norm(NOTES))
    assert (probs == []) is ok, probs


def test_forbidden_phrase():
    assert hook_problems("Real gameplay from X01", grounded_n=GROUNDED, forbidden=["gameplay"],
                         research_n="") == ["uses forbidden phrase 'gameplay'"]


def test_trend_section_lists_avoid_and_forbids_research_claims():
    tb = TrendBrief(campaign="c", generated_at="t", sources=[], keywords=["DMZ"], patterns=[],
                    hook_formulas=["<place> is where it gets chaotic"], avoid=["PvE complaints"])
    text = trend_section(tb)
    assert "PvE complaints" in text and "Never put a scored" in text
    assert trend_section(None) == ""


@pytest.fixture
def ws(tmp_path, monkeypatch):
    camp = tmp_path / "campaigns" / CAMPAIGN
    camp.mkdir(parents=True)
    shutil.copy(REPO / "docs/examples/brief.example.md", camp / "brief.md")
    shutil.copy(REPO / "docs/examples/rules.example.yaml", camp / "rules.yaml")
    store.approve(CAMPAIGN, tmp_path / "campaigns")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_research_add_appends_notes_and_rebuilds_from_all(ws, monkeypatch):
    body = TrendBriefBody(keywords=["launch"], patterns=[], hook_formulas=["<moment> lands"],
                          avoid=["review scores"],
                          fan_references=[{"name": "PPSh-41", "context": "returning favourite",
                                           "emotion": "nostalgia"}])
    calls = []
    monkeypatch.setattr(llm, "parse_structured", lambda **kw: calls.append(kw) or body)
    tb = add_notes(CAMPAIGN, NOTES.encode(), model="m", campaigns_root=ws / "campaigns")
    notes = ws / "data/research" / f"{CAMPAIGN}.notes.md"
    assert notes.read_text() == NOTES + "\n"
    assert load_trend_brief(CAMPAIGN) == tb and tb.fan_references[0].emotion == "nostalgia"
    assert NOTES in calls[0]["user"] and "exaggerated hooks" in calls[0]["user"]

    add_notes(CAMPAIGN, b"More notes.", model="m", campaigns_root=ws / "campaigns")
    assert notes.read_text() == NOTES + "\n\n---\n\nMore notes.\n"
    assert NOTES in calls[1]["user"] and "More notes." in calls[1]["user"]

    add_notes(CAMPAIGN, b"Fresh start.", model="m", replace=True, campaigns_root=ws / "campaigns")
    assert notes.read_text() == "Fresh start.\n"


def test_fan_reference_names_count_as_grounded_but_stats_do_not():
    g = grounding_for_test(["PPSh-41", "Kastov 762"])
    ok = hook_problems("did i just see the PPSh-41??", grounded_n=g, forbidden=[], research_n="")
    assert ok == []
    bad = hook_problems("882 RPM on the Osprey is wild", grounded_n=g, forbidden=[], research_n="")
    assert bad and "882" in bad[0]


def grounding_for_test(names):
    return norm(" ".join([GROUNDED, *names]))


def _clips_ws(ws):
    from tests.test_select import SHOTS, transcript

    t = transcript([(4.5, 6.0, [("North", 4.5, 4.9), ("Korea", 5.0, 5.5)])])
    (ws / "data/transcripts").mkdir(parents=True)
    (ws / "data/transcripts/trailer.json").write_text(t.model_dump_json())
    adir = ws / "data/analysis/trailer"
    adir.mkdir(parents=True)
    (adir / "shots.json").write_text(SHOTS.model_dump_json())
    for s in SHOTS.shots:
        (adir / s.keyframe).write_bytes(b"\xff\xd8")
    clip = {"id": "c01", "start_s": 4.0, "end_s": 11.9, "start_shot": 1, "end_shot": 2,
            "hook_line": "old hook", "title": "t", "score": 7.0, "scores": {}, "reason": "r",
            "approved": True}
    cf = ClipsFile.model_validate({"video_id": "trailer", "campaign": CAMPAIGN,
                                   "generated_at": "t", "model": "m", "clips": [clip]})
    (ws / "data/clips").mkdir(parents=True)
    path = ws / "data/clips/trailer.clips.json"
    path.write_text(cf.model_dump_json())
    return path


def test_regenerate_hooks_keeps_cuts_and_approval(ws, monkeypatch):
    path = _clips_ws(ws)

    def fake(**kw):
        images = [b for b in kw["user"] if b["type"] == "image"]
        assert len(images) == 2  # only the clip's own shots
        return kw["output_type"].model_validate({"clips": [
            {"clip_id": "c01", "hook_options": ["North Korea owns it now", "Scored 9/10 by fans",
                                                 "The squad moves in"]},
            {"clip_id": "c99", "hook_options": ["ignored"]}]})

    monkeypatch.setattr(llm, "parse_structured", fake)
    clips, rejected = regenerate_hooks(CAMPAIGN, "trailer", model="m")
    c = clips.clips[0]
    assert c.hook_options == ["North Korea owns it now", "The squad moves in"]
    assert c.hook_line == "old hook" and c.approved is True and c.start_s == 4.0
    assert "9/10" in rejected["c01"][0]

    done = choose_hooks(path, "c01", ["2", "1"])
    assert [(c.id, c.hook_line, c.variant_of) for c in done] == [
        ("c01", "The squad moves in", None), ("c01-2", "North Korea owns it now", "c01")]
    saved = ClipsFile.model_validate_json(path.read_text())
    assert [c.id for c in saved.clips] == ["c01", "c01-2"]
    assert saved.clips[1].approved is True and saved.clips[1].start_s == 4.0

    done = choose_hooks(path, "c01", ["anyone else thinking about the squad?"])  # own text
    assert done[0].hook_line == "anyone else thinking about the squad?"
    assert [c.id for c in ClipsFile.model_validate_json(path.read_text()).clips] == ["c01"]

    with pytest.raises(HookError, match="pick 1-2"):
        choose_hooks(path, "c01", ["5"])
    with pytest.raises(HookError, match="'84'"):
        choose_hooks(path, "c01", ["rated 84 by critics"])
    with pytest.raises(HookError, match="no clip"):
        choose_hooks(path, "c01-2", ["1"])
