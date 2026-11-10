"""Small ffmpeg/ffprobe helpers shared by several stages."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000  # what whisper expects; also used for loudness analysis


class MediaError(RuntimeError):
    pass


def decode_audio(video: Path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """First audio stream as mono float32 PCM, decoded by the system ffmpeg.

    faster-whisper's own decoder (PyAV) is broken with av>=19 (it passes the removed
    `metadata_errors` argument), so we decode here and hand whisper a numpy array.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-map", "0:a:0", "-vn",
        "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg could not decode audio from {video}: {err}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def probe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed on {path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def duration_s(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def contract_video_path(video: Path) -> str:
    """Path as stored in file contracts: relative to the repo root (cwd) when possible."""
    p = video.resolve()
    try:
        return p.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.as_posix()
