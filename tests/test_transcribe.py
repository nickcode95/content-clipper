"""Unit tests for the transcribe stage. Fake segments only: no model download."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from content_clipper.cli import app
from content_clipper.config import TranscribeConfig, load_transcribe_config
from content_clipper.models import Transcript
from content_clipper.transcribe import core as tx


def seg(start, end, text, words):
    return NS(
        start=start,
        end=end,
        text=text,
        words=[NS(start=s, end=e, word=w, probability=0.9) for w, s, e in words],
    )


def fake_segments():
    return [
        seg(0.0, 2.0, " Hello there world.", [(" Hello", 0.0, 0.4), (" there", 0.4, 0.8),
                                              (" world.", 0.9, 1.6)]),
        seg(2.0, 4.2, " Second line here.", [(" Second", 2.1, 2.5), (" line", 2.5, 2.9),
                                             (" here.", 3.0, 3.6)]),
    ]


def build(segments, **kw):
    args = {"video": "data/sources/x.mp4", "video_id": "x", "duration_s": 4.5,
            "language": "en", "model": "small", **kw}
    return tx.build_transcript(segments, **args)


def assert_monotonic(t: Transcript):
    for w in t.words:
        assert w.start <= w.end
    for a, b in zip(t.words, t.words[1:]):
        assert a.end <= b.start, (a, b)


def test_build_matches_contract():
    t, adjusted = build(fake_segments())
    assert adjusted == 0
    data = json.loads(t.model_dump_json())
    assert set(data) == {"video", "video_id", "duration_s", "language", "model", "segments",
                         "words"}
    assert data["segments"][0] == {"id": 0, "start": 0.0, "end": 2.0, "text": "Hello there world."}
    assert data["segments"][1]["id"] == 1
    assert data["words"][0] == {"seg": 0, "w": "Hello", "start": 0.0, "end": 0.4}
    assert [w["seg"] for w in data["words"]] == [0, 0, 0, 1, 1, 1]
    assert [w["w"] for w in data["words"]] == ["Hello", "there", "world.", "Second", "line",
                                               "here."]
    assert_monotonic(t)


def test_overlapping_and_backwards_words_are_clamped():
    segments = [
        seg(0.0, 3.0, "a b c d", [
            ("a", 0.0, 0.5),
            ("b", 0.4, 0.9),   # starts before a ends
            ("c", 0.95, 0.8),  # ends before it starts
            ("d", 0.7, 0.75),  # wholly inside the previous word
        ]),
        seg(2.5, 4.0, "e", [("e", 0.6, 3.2)]),  # jumps back across the segment boundary
    ]
    t, adjusted = build(segments)
    assert adjusted == 4
    assert_monotonic(t)
    assert [(w.start, w.end) for w in t.words] == [
        (0.0, 0.5), (0.5, 0.9), (0.95, 0.95), (0.95, 0.95), (0.95, 3.2)]


def test_timestamps_rounded_to_ms_and_never_negative():
    t, _ = build([seg(-0.01, 1.23456, "x", [("x", -0.002, 1.00049)])])
    assert (t.segments[0].start, t.segments[0].end) == (0.0, 1.235)
    assert (t.words[0].start, t.words[0].end) == (0.0, 1.0)


def test_blank_words_and_missing_word_lists_are_skipped():
    segments = [
        seg(0.0, 1.0, " hi ", [(" ", 0.0, 0.1), (" hi", 0.1, 0.5)]),
        NS(start=1.0, end=2.0, text=" (music)", words=None),
    ]
    t, _ = build(segments)
    assert [w.w for w in t.words] == ["hi"]
    assert [s.text for s in t.segments] == ["hi", "(music)"]


def test_out_of_order_segment_start_is_clamped():
    t, _ = build([seg(1.0, 2.0, "a", []), seg(0.5, 0.4, "b", [])])
    assert [(s.start, s.end) for s in t.segments] == [(1.0, 2.0), (1.0, 1.0)]


def test_progress_callback_gets_segment_ends():
    seen = []
    build(fake_segments(), on_segment=seen.append)
    assert seen == [2.0, 4.2]


def _contract(**over):
    data = {
        "video": "v.mp4", "video_id": "v", "duration_s": 3.0, "language": "en", "model": "small",
        "segments": [{"id": 0, "start": 0.0, "end": 2.0, "text": "a b"}],
        "words": [{"seg": 0, "w": "a", "start": 0.0, "end": 0.5},
                  {"seg": 0, "w": "b", "start": 0.6, "end": 1.0}],
    }
    data.update(over)
    return data


def test_validator_accepts_good_contract():
    Transcript.model_validate(_contract())


@pytest.mark.parametrize("over, msg", [
    ({"words": [{"seg": 0, "w": "a", "start": 0.0, "end": 0.5},
                {"seg": 0, "w": "b", "start": 0.4, "end": 1.0}]}, "previous word end"),
    ({"words": [{"seg": 0, "w": "a", "start": 0.5, "end": 0.4}]}, "end 0.4 < start"),
    ({"words": [{"seg": 3, "w": "a", "start": 0.0, "end": 0.5}]}, "missing segment"),
    ({"segments": [{"id": 1, "start": 0.0, "end": 2.0, "text": "a"}], "words": []}, "ids must"),
    ({"segments": [{"id": 0, "start": 1.0, "end": 2.0, "text": "a"},
                   {"id": 1, "start": 0.5, "end": 2.0, "text": "b"}], "words": []},
     "previous start"),
    ({"extra_field": 1}, "Extra inputs"),
])
def test_validator_rejects_broken_contract(over, msg):
    with pytest.raises(ValidationError, match=msg):
        Transcript.model_validate(_contract(**over))


def test_write_read_roundtrip(tmp_path):
    t, _ = build(fake_segments())
    path = tx.write_transcript(t, tmp_path / "transcripts")
    assert path == tmp_path / "transcripts" / "x.json"
    assert not list(path.parent.glob("*.tmp"))
    assert tx.read_transcript(path) == t


def test_contract_video_path_relative_to_cwd(tmp_path, monkeypatch):
    video = tmp_path / "data" / "sources" / "My Clip.mp4"
    video.parent.mkdir(parents=True)
    video.touch()
    monkeypatch.chdir(tmp_path)
    assert tx.contract_video_path(Path("data/sources/My Clip.mp4")) == "data/sources/My Clip.mp4"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert tx.contract_video_path(video) == video.resolve().as_posix()


def test_repo_config_is_cpu_int8(repo):
    cfg = load_transcribe_config(repo / "config" / "transcribe.yaml")
    assert (cfg.device, cfg.compute_type) == ("cpu", "int8")


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("model_size: small\ndevcie: cpu\n")
    with pytest.raises(ValidationError):
        load_transcribe_config(p)


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        return iter(fake_segments()), NS(duration=4.5, language="en")


def test_cli_transcribe_with_fake_model(tmp_path, monkeypatch, repo):
    fake = FakeModel()
    loaded = []

    def fake_load(cfg: TranscribeConfig):
        loaded.append(cfg)
        return fake

    monkeypatch.setattr(tx, "load_model", fake_load)
    pcm = np.zeros(4 * tx.SAMPLE_RATE, dtype=np.float32)
    monkeypatch.setattr(tx, "decode_audio", lambda video: pcm)
    video = tmp_path / "Some Video.mp4"
    video.touch()
    out_dir = tmp_path / "transcripts"

    result = CliRunner().invoke(app, ["transcribe", str(video), "--out-dir", str(out_dir),
                                      "--config", str(repo / "config" / "transcribe.yaml")])

    assert result.exit_code == 0, result.output
    for label in ("duration", "wall time", "RTF"):
        assert label in result.output
    assert "4.50 s" in result.output
    assert loaded[0].device == "cpu"
    audio, kw = fake.calls[0]
    assert audio is pcm
    assert kw["word_timestamps"] is True
    assert kw["language"] == "en"

    t = tx.read_transcript(out_dir / "Some Video.json")
    assert t.video_id == "Some Video"
    assert len(t.words) == 6
    assert_monotonic(t)


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@needs_ffmpeg
def test_decode_audio_gives_16k_mono_float32(tmp_path):
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=1:sample_rate=44100", "-ac", "2", str(src)],
                   check=True)
    pcm = tx.decode_audio(src)
    assert pcm.dtype == np.float32
    assert abs(len(pcm) - tx.SAMPLE_RATE) <= 160
    assert 0.1 < float(np.abs(pcm).max()) <= 1.0


@needs_ffmpeg
def test_decode_audio_fails_loudly_on_non_media(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_text("hello")
    with pytest.raises(RuntimeError, match="could not decode audio"):
        tx.decode_audio(bad)


def test_cli_missing_video_fails(tmp_path):
    result = CliRunner().invoke(app, ["transcribe", str(tmp_path / "nope.mp4")])
    assert result.exit_code != 0
