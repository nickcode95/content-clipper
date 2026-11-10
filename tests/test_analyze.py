"""Analyze stage: cut detection and per-shot signals on synthetic data (no downloads)."""

import shutil
import subprocess

import numpy as np
import pytest

from content_clipper.analyze.core import (
    AnalyzeConfig,
    analyze_video,
    detect_cuts,
    focus_x,
    frame_signals,
    pick_keyframe,
    rms_db,
    shot_bounds,
)
from content_clipper.models import ShotAnalysis

FPS = 30


def noise(n, level=0.01, seed=0):
    return np.abs(np.random.default_rng(seed).normal(level, level / 3, n))


def test_clear_cuts_are_found():
    d = noise(150)
    d[30], d[90] = 0.25, 0.12
    assert detect_cuts(d, FPS) == [30, 90]


def test_small_changes_are_not_cuts():
    d = noise(150)
    d[60] = 0.06  # below the absolute floor
    assert detect_cuts(d, FPS) == []


def test_busy_action_shot_does_not_split():
    d = noise(150, level=0.05)  # lots of motion everywhere
    d[60] = 0.12  # big, but under 3x the local median
    assert detect_cuts(d, FPS) == []


def test_flash_frames_close_together_give_one_cut():
    d = noise(150)
    d[60], d[63] = 0.3, 0.3  # 0.1 s apart: muzzle flash, not two shots
    assert detect_cuts(d, FPS) == [60]


def test_frame_signals():
    frames = [np.zeros((9, 16), np.uint8), np.zeros((9, 16), np.uint8),
              np.full((9, 16), 255, np.uint8)]
    brightness, diffs, cols = frame_signals(iter(frames))
    assert brightness.tolist() == [0.0, 0.0, 1.0]
    assert diffs.tolist() == [0.0, 0.0, 1.0]
    assert cols.shape == (3, 16) and cols[2].tolist() == [1.0] * 16


@pytest.mark.parametrize("lo, hi, side", [(120, 140, "right"), (10, 30, "left")])
def test_crop_window_contains_the_action_and_stays_as_central_as_it_can(lo, hi, side):
    cols = np.full((30, 160), 0.002, np.float32)
    cols[:, lo:hi] = 0.08
    f = focus_x(cols, 0.316)
    half, col = 0.316 / 2, 1 / 160  # tolerance: one analysis column
    assert f - half - col <= lo / 160 and hi / 160 <= f + half + col  # action inside the crop
    assert (f > 0.6) if side == "right" else (f < 0.4)


def test_focus_is_none_when_still_or_evenly_spread():
    assert focus_x(np.zeros((30, 160), np.float32), 0.316) is None
    assert focus_x(np.full((30, 160), 0.05, np.float32), 0.316) is None
    assert focus_x(np.zeros((0, 160), np.float32), 0.316) is None


def test_shot_bounds_and_keyframes():
    assert shot_bounds([45, 90], FPS, 4.0) == [(0.0, 1.5), (1.5, 3.0), (3.0, 4.0)]
    times = [0.0, 0.5, 1.0, 1.5, 2.0]
    assert pick_keyframe(0.0, 1.5, times) == 1  # 0.5 and 1.0 tie for the middle; first wins
    assert pick_keyframe(1.5, 1.6, times) == 3  # inside the shot beats closer-but-outside


def test_rms_db():
    sr = 16000
    t = np.arange(sr) / sr
    sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    assert rms_db(sine, sr, 0, 1) == pytest.approx(-3.0, abs=0.1)
    assert rms_db(np.zeros(sr, np.float32), sr, 0, 1) == -90.0
    assert rms_db(sine, sr, 2, 3) == -90.0  # past the end


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_synthetic_three_shot_video(tmp_path):
    video = tmp_path / "three shots.mp4"
    srcs = []
    for colour in ("red", "blue", "white"):
        srcs += ["-f", "lavfi", "-i", f"color={colour}:s=320x180:d=1.5:r=30"]
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", *srcs,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4.5",
         "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
        check=True,
    )
    a = analyze_video(video, tmp_path / "analysis", AnalyzeConfig())
    assert [round(s.start, 1) for s in a.shots] == [0.0, 1.5, 3.0]
    assert a.shots[-1].end == pytest.approx(4.5, abs=0.1)
    assert a.shots[2].brightness > 0.9 > a.shots[1].brightness
    assert all(-24 < s.loudness_db < -18 for s in a.shots)  # lavfi sine plays at 1/8 scale
    out = tmp_path / "analysis" / "three shots"
    assert ShotAnalysis.model_validate_json((out / "shots.json").read_text()) == a
    assert all((out / s.keyframe).stat().st_size > 0 for s in a.shots)
