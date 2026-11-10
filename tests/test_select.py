"""Select stage: snapping, scoring and the full stage with a fake LLM (no paid calls)."""

import json
import shutil
from pathlib import Path

import pytest

from content_clipper import llm
from content_clipper.campaign import store
from content_clipper.config import RubricConfig, load_rubric_config
from content_clipper.models import (
    ClipsFile,
    Segment,
    Shot,
    ShotAnalysis,
    Transcript,
    Word,
)
from content_clipper.select.core import (
    SelectError,
    SnapError,
    response_model,
    select_clips,
    snap,
    timeline,
    weighted_score,
)

REPO = Path(__file__).resolve().parents[1]
CAMPAIGN = "acme-launch-trailer"
VIDEO = "trailer"


def analysis(bounds, fps=10.0):
    shots = [Shot(id=i, start=a, end=b, keyframe=f"shot_{i:03d}.jpg", loudness_db=-20.0,
                  motion=0.01 * (i + 1), brightness=0.3) for i, (a, b) in enumerate(bounds)]
    return ShotAnalysis(video="v.mp4", video_id=VIDEO, duration_s=bounds[-1][1],
                        analysis_fps=fps, shots=shots)


def transcript(segs):
    """segs: [(start, end, [(word, start, end), ...]), ...]"""
    segments, words = [], []
    for i, (a, b, ws) in enumerate(segs):
        segments.append(Segment(id=i, start=a, end=b, text=" ".join(w for w, _, _ in ws)))
        words += [Word(seg=i, w=w, start=s, end=e) for w, s, e in ws]
    return Transcript(video="v.mp4", video_id=VIDEO, duration_s=20.0, language="en",
                      model="small", segments=segments, words=words)


SHOTS = analysis([(0, 4), (4, 8), (8, 12), (12, 16), (16, 20)])
SILENT = transcript([])


def test_clean_shot_range_cuts_on_shot_boundaries():
    s = snap(1, 2, SHOTS, SILENT, min_len=1, max_len=60)
    assert (s.start, s.end) == (4.0, 11.9)  # end pulled back one analysis frame (1/10 s)
    assert (s.start_shot, s.end_shot, s.speech_cut) == (1, 2, False)
    assert (s.start_seg, s.end_seg) == (None, None)


def test_last_shot_ends_at_the_end_of_the_video():
    assert snap(3, 4, SHOTS, SILENT, min_len=1, max_len=60).end == 20.0


def test_sentence_across_the_start_extends_to_an_earlier_shot():
    t = transcript([(3.0, 5.0, [("aa", 3.0, 3.8), ("bb", 4.2, 5.0)])])
    s = snap(1, 2, SHOTS, t, min_len=1, max_len=60, max_extend_s=5)
    assert (s.start, s.start_shot, s.speech_cut) == (0.0, 0, False)
    assert (s.start_seg, s.end_seg) == (0, 0)


def test_sentence_across_the_end_extends_when_cheap():
    shots = analysis([(0, 4), (4, 8), (8, 12), (12, 14), (14, 20)])
    t = transcript([(11.0, 13.0, [("cc", 11.0, 11.8), ("dd", 11.85, 12.4)])])
    s = snap(1, 2, shots, t, min_len=1, max_len=60)
    assert (s.end, s.end_shot, s.speech_cut) == (13.9, 3, False)


def test_never_cuts_inside_a_word_when_extension_is_too_long():
    t = transcript([(11.0, 13.0, [("cc", 11.0, 11.8), ("dd", 11.85, 12.4)])])
    s = snap(1, 2, SHOTS, t, min_len=1, max_len=60)  # next boundary is 4 s away: too far
    assert s.end == pytest.approx(11.83)  # just before "dd" starts
    assert s.speech_cut is True
    assert s.end_seg == 0


def test_start_inside_a_word_moves_after_it():
    t = transcript([(3.5, 4.6, [("ee", 3.5, 4.3), ("ff", 4.4, 4.6)])])
    s = snap(1, 2, SHOTS, t, min_len=1, max_len=60, max_extend_s=0.5)
    assert s.start == pytest.approx(4.32) and s.speech_cut is True


@pytest.mark.parametrize("a, b, kw, msg", [
    (1, 1, {"min_len": 5, "max_len": 60}, "too short"),
    (0, 4, {"min_len": 1, "max_len": 10}, "too long"),
    (3, 9, {"min_len": 1, "max_len": 60}, "does not exist"),
    (2, 1, {"min_len": 1, "max_len": 60}, "does not exist"),
])
def test_bad_candidates_are_rejected(a, b, kw, msg):
    with pytest.raises(SnapError, match=msg):
        snap(a, b, SHOTS, SILENT, **kw)


def test_weighted_score_and_response_schema():
    assert weighted_score({"a": 10, "b": 0}, {"a": 3, "b": 1}) == 7.5
    model = response_model(["a", "b"])
    schema = json.dumps(model.model_json_schema())
    assert '"a"' in schema and '"start_shot"' in schema


def test_timeline_lists_shots_with_their_words():
    t = transcript([(4.5, 6.0, [("North", 4.5, 4.9), ("Korea", 5.0, 5.5)])])
    text = timeline(SHOTS, t)
    assert text.splitlines()[1].startswith("[H001]  4.0s")
    assert '"North Korea"' in text.splitlines()[1]
    assert "motion high" in text.splitlines()[4]


# ---------------------------------------------------------------- full stage, fake LLM


@pytest.fixture
def ws(tmp_path, monkeypatch):
    camp = tmp_path / "campaigns" / CAMPAIGN
    camp.mkdir(parents=True)
    shutil.copy(REPO / "docs/examples/brief.example.md", camp / "brief.md")
    shutil.copy(REPO / "docs/examples/rules.example.yaml", camp / "rules.yaml")
    store.approve(CAMPAIGN, tmp_path / "campaigns")

    (tmp_path / "data/transcripts").mkdir(parents=True)
    t = transcript([(4.5, 6.0, [("North", 4.5, 4.9), ("Korea", 5.0, 5.5)])])
    (tmp_path / "data/transcripts" / f"{VIDEO}.json").write_text(t.model_dump_json())
    adir = tmp_path / "data/analysis" / VIDEO
    adir.mkdir(parents=True)
    (adir / "shots.json").write_text(SHOTS.model_dump_json())
    for s in SHOTS.shots:
        (adir / s.keyframe).write_bytes(b"\xff\xd8 fake jpeg")
    reg = tmp_path / "data/sources/registry.csv"
    reg.parent.mkdir(parents=True)
    reg.write_text(f"video_id,origin,campaign,downloaded_at,notes\n{VIDEO},folder,{CAMPAIGN},,\n")

    calls = []

    def fake_llm(**kw):
        calls.append(kw)
        Resp = kw["output_type"]
        keys = list(load_rubric_config(REPO / "config/rubric.yaml").weights)

        def cand(a, b, title, val):
            return {"start_shot": a, "end_shot": b,
                    "hook_options": [f" {title} hook ", "Rated 84 by critics", f"{title} hook"],
                    "title": title, "scores": dict.fromkeys(keys, val), "reason": "r"}

        return Resp.model_validate({"candidates": [
            cand(0, 2, "middle", 6),
            cand(1, 3, "overlaps", 5),
            cand(3, 4, "end", 8),
            cand(7, 9, "made-up shots", 9),
            cand(0, 0, "too short", 4),
        ]})

    monkeypatch.setattr(llm, "parse_structured", fake_llm)
    monkeypatch.chdir(tmp_path)
    return {"root": tmp_path, "calls": calls}


def rubric() -> RubricConfig:
    r = load_rubric_config(REPO / "config/rubric.yaml")
    return r.model_copy(update={"window_seconds": r.window_seconds.model_copy(update={"min": 5})})


def run(**kw):
    return select_clips(CAMPAIGN, VIDEO, model="m", rubric=rubric(), **kw)


def test_select_writes_clips_ranked_and_unapproved(ws):
    result = run()
    assert [c.title for c in result.clips] == ["end", "middle"]
    assert [c.id for c in result.clips] == ["c01", "c02"]
    assert all(c.approved is None for c in result.clips)
    assert result.clips[0].hook_line == "end hook"
    assert result.clips[0].hook_options == ["end hook"]  # ungrounded 84 + duplicate dropped
    assert result.clips[0].score == 8.0
    reasons = {r.title: r.reason for r in result.rejected}
    assert "overlaps a higher-scoring clip" in reasons["overlaps"]
    assert "does not exist" in reasons["made-up shots"]
    assert "too short" in reasons["too short"]  # example campaign: min 8 s, shot 0 is 4 s
    on_disk = ClipsFile.model_validate_json(
        (ws["root"] / "data/clips" / f"{VIDEO}.clips.json").read_text())
    assert on_disk == result

    content = ws["calls"][0]["user"]
    images = [b for b in content if b["type"] == "image"]
    assert len(images) == len(SHOTS.shots)
    prompt = content[-1]["text"]
    assert "[H001]" in prompt and "exaggerated hooks, rage bait" in prompt
    assert "between 8 and 20 s" in prompt and "no false claims" in prompt
    assert "did i just see" in prompt  # fan-anticipation hook guidance


def test_select_refuses_existing_clips_without_force(ws):
    run()
    with pytest.raises(SelectError, match="--force"):
        run()
    assert run(force=True).clips


def test_select_refuses_unregistered_source(ws):
    (ws["root"] / "data/sources/registry.csv").write_text(
        "video_id,origin,campaign,downloaded_at,notes\n")
    with pytest.raises(SelectError, match="not registered"):
        run()


def test_select_refuses_draft_campaign(ws):
    brief = ws["root"] / "campaigns" / CAMPAIGN / "brief.md"
    brief.write_text(brief.read_text() + "\nchanged\n")
    with pytest.raises(store.CampaignNotReviewed):
        run()


def test_select_needs_transcript_and_analysis(ws):
    (ws["root"] / "data/analysis" / VIDEO / "shots.json").unlink()
    with pytest.raises(SelectError, match="analyze"):
        run()
