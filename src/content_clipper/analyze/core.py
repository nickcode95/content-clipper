"""Shot detection, per-shot signals and one keyframe per shot.

One ffmpeg decode pass produces small grey frames (for cut detection, motion and brightness)
and colour keyframes (for the LLM). Cut detection is plain numpy on frame differences, tuned on
dark, fast-cut game trailers: a frame is a cut when its difference from the previous frame is
above an absolute floor AND several times the local median (so busy action shots do not split).
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from content_clipper.media import (
    SAMPLE_RATE,
    contract_video_path,
    decode_audio,
    duration_s,
    probe,
)
from content_clipper.models import Shot, ShotAnalysis

GREY_W, GREY_H = 160, 90


@dataclass(frozen=True)
class AnalyzeConfig:
    fps: float = 30.0  # analysis frame rate: shot boundaries are accurate to 1/fps
    keyframe_fps: float = 2.0  # colour frames sampled for keyframes
    keyframe_width: int = 640
    cut_abs_min: float = 0.07  # minimum mean abs difference for a cut
    cut_ratio: float = 3.0  # ...and at least this many times the local median difference
    median_window_s: float = 0.5
    min_shot_s: float = 0.3  # cuts closer than this to the previous cut are ignored (flashes)


DEFAULT = AnalyzeConfig()


def frame_signals(frames: Iterator[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per frame: brightness, difference from the previous frame (first is 0), and that
    difference per column (shape frames x width). All 0..1."""
    brightness, diffs, cols = [], [], []
    prev = None
    for f in frames:
        g = f.astype(np.float32) / 255.0
        brightness.append(float(g.mean()))
        d = np.zeros(g.shape[1], np.float32) if prev is None else np.abs(g - prev).mean(axis=0)
        cols.append(d)
        diffs.append(float(d.mean()))
        prev = g
    width = cols[0].size if cols else 0
    return np.array(brightness), np.array(diffs), np.array(cols).reshape(len(cols), width)


def focus_x(col_motion: np.ndarray, window_frac: float, min_motion: float = 0.004,
            min_lift: float = 1.2) -> float | None:
    """Centre (0..1) of the window (window_frac of the width) holding the most motion.

    None when the shot is nearly still, or when motion is spread so evenly that no window holds
    clearly more than its share (min_lift); the crop then stays in the centre. A mild centre
    prior breaks near-ties.
    """
    if col_motion.size == 0 or float(col_motion.mean()) < min_motion:
        return None
    e = col_motion.sum(axis=0).astype(np.float64)
    n = e.size
    w = min(n, max(1, round(window_frac * n)))
    csum = np.r_[0.0, np.cumsum(e)]
    sums = csum[w:] - csum[:-w]
    centres = (np.arange(sums.size) + w / 2) / n
    best = int(np.argmax(sums * (1 - 0.15 * np.abs(centres - 0.5) * 2)))
    if sums[best] < min_lift * e.sum() * w / n:
        return None
    return round(float(centres[best]), 3)


def detect_cuts(diffs: np.ndarray, fps: float, cfg: AnalyzeConfig = DEFAULT) -> list[int]:
    """Frame indices that start a new shot (frame 0 excluded)."""
    win = max(1, round(cfg.median_window_s * fps))
    min_gap = max(1, round(cfg.min_shot_s * fps))
    cuts: list[int] = []
    for t in range(1, len(diffs)):
        if diffs[t] < cfg.cut_abs_min:
            continue
        lo, hi = max(t - win, 1), min(t + win + 1, len(diffs))
        around = np.r_[diffs[lo:max(t - 1, lo)], diffs[min(t + 2, hi):hi]]
        median = float(np.median(around)) if around.size else 0.0
        if diffs[t] < cfg.cut_ratio * max(median, 0.005):
            continue
        if cuts and t - cuts[-1] < min_gap:
            continue
        if not cuts and t < min_gap:
            continue
        cuts.append(t)
    return cuts


def shot_bounds(cuts: list[int], fps: float, duration: float) -> list[tuple[float, float]]:
    edges = [0.0] + [round(c / fps, 3) for c in cuts if c / fps < duration] + [round(duration, 3)]
    return [(a, b) for a, b in itertools.pairwise(edges) if b > a]


def rms_db(pcm: np.ndarray, sr: int, start: float, end: float) -> float:
    seg = pcm[int(start * sr):int(end * sr)]
    if seg.size == 0:
        return -90.0
    rms = float(np.sqrt(np.mean(np.square(seg, dtype=np.float64))))
    return round(max(20 * np.log10(rms + 1e-12), -90.0), 1)


def pick_keyframe(start: float, end: float, times: list[float]) -> int:
    """Index of the sampled frame closest to the shot's middle, preferring frames inside it."""
    mid = (start + end) / 2
    inside = [i for i, t in enumerate(times) if start <= t < end]
    pool = inside or range(len(times))
    return min(pool, key=lambda i: abs(times[i] - mid))


def _decode(
    video: Path, tmp: Path, cfg: AnalyzeConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[Path]]:
    graph = (
        f"[0:v]fps={cfg.fps},split=2[a][b];"
        f"[a]scale={GREY_W}:{GREY_H},format=gray[g];"
        f"[b]fps={cfg.keyframe_fps},scale={cfg.keyframe_width}:-2[k]"
    )
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video), "-an",
        "-filter_complex", graph,
        "-map", "[g]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        "-map", "[k]", "-q:v", "4", str(tmp / "k_%06d.jpg"),
    ]
    size = GREY_W * GREY_H
    with open(tmp / "ffmpeg.log", "wb") as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log)
        assert proc.stdout is not None

        def frames() -> Iterator[np.ndarray]:
            while chunk := proc.stdout.read(size):
                if len(chunk) < size:
                    break
                yield np.frombuffer(chunk, np.uint8).reshape(GREY_H, GREY_W)

        brightness, diffs, cols = frame_signals(frames())
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed on {video}: {(tmp / 'ffmpeg.log').read_text()}")
    return brightness, diffs, cols, sorted(tmp.glob("k_*.jpg"))


def analyze_video(video: Path, out_root: Path, cfg: AnalyzeConfig = DEFAULT) -> ShotAnalysis:
    out_dir = out_root / video.stem
    duration = duration_s(video)
    v = next(s for s in probe(video)["streams"] if s["codec_type"] == "video")
    window = min(1.0, (9 / 16) * int(v["height"]) / int(v["width"]))  # 9:16 crop, as a fraction
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        brightness, diffs, cols, jpgs = _decode(video, tmp, cfg)
        if len(diffs) == 0 or not jpgs:
            raise RuntimeError(f"no video frames decoded from {video}")
        cuts = detect_cuts(diffs, cfg.fps, cfg)
        bounds = shot_bounds(cuts, cfg.fps, duration)
        pcm = decode_audio(video)
        times = [i / cfg.keyframe_fps for i in range(len(jpgs))]

        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        shots = []
        for i, (start, end) in enumerate(bounds):
            a, b = int(start * cfg.fps), max(int(end * cfg.fps), int(start * cfg.fps) + 1)
            inner = diffs[a + 1:b]  # skip the cut frame itself
            name = f"shot_{i:03d}.jpg"
            shutil.copy(jpgs[pick_keyframe(start, end, times)], out_dir / name)
            shots.append(Shot(
                id=i, start=start, end=end, keyframe=name,
                loudness_db=rms_db(pcm, SAMPLE_RATE, start, end),
                motion=round(float(inner.mean()) if inner.size else 0.0, 4),
                brightness=round(float(brightness[a:b].mean()) if b > a else 0.0, 4),
                focus_x=focus_x(cols[a + 1:b], window),
            ))
    analysis = ShotAnalysis(video=contract_video_path(video), video_id=video.stem,
                            duration_s=round(duration, 3), analysis_fps=cfg.fps, shots=shots)
    tmp_json = out_dir / "shots.json.tmp"
    tmp_json.write_text(analysis.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_json, out_dir / "shots.json")
    return analysis


def read_analysis(out_root: Path, video_id: str) -> ShotAnalysis:
    path = out_root / video_id / "shots.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `content-clipper analyze <video>` first")
    return ShotAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
