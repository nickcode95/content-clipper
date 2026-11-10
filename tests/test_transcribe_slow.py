"""Integration test: real faster-whisper model on a real video.

Gated by RUN_SLOW=1 because it downloads the whisper model on first run and takes a while on CPU.
Uses $CLIPPER_TEST_VIDEO, or else the first media file in data/sources/.

    RUN_SLOW=1 uv run pytest tests/test_transcribe_slow.py -s
"""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from content_clipper.cli import app
from content_clipper.transcribe import core as tx

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SLOW") != "1", reason="slow: set RUN_SLOW=1 (loads a real whisper model)"
)

MEDIA = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".wav", ".mp3"}


def find_video(repo: Path) -> Path | None:
    if env := os.environ.get("CLIPPER_TEST_VIDEO"):
        return Path(env)
    found = sorted(p for p in (repo / "data" / "sources").glob("*") if p.suffix.lower() in MEDIA)
    return found[0] if found else None


def test_transcribe_real_video(tmp_path, repo):
    video = find_video(repo)
    if video is None:
        pytest.skip("no video in data/sources/ and CLIPPER_TEST_VIDEO is not set")

    result = CliRunner().invoke(app, ["transcribe", str(video), "--out-dir", str(tmp_path),
                                      "--config", str(repo / "config" / "transcribe.yaml")])
    print(result.output)
    assert result.exit_code == 0, result.output
    assert "RTF" in result.output

    t = tx.read_transcript(tmp_path / f"{video.stem}.json")
    assert t.duration_s > 0
    assert t.words, "no words recognised; does the video contain speech?"
    for w in t.words:
        assert w.start <= w.end
    for a, b in zip(t.words, t.words[1:]):
        assert a.end <= b.start
    assert t.words[-1].end <= t.duration_s + 0.5
