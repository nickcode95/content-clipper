"""Transcribe stage: faster-whisper -> data/transcripts/<video_id>.json.

The model is loaded only through `load_model`, so unit tests can swap in a fake and never
download weights. Everything after the model call is pure and works on any objects shaped like
faster-whisper segments (`.start`, `.end`, `.text`, `.words[i].start/.end/.word`).
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from content_clipper.config import TranscribeConfig

SAMPLE_RATE = 16_000  # what whisper expects
from content_clipper.models import Segment, Transcript, Word


@dataclass(frozen=True)
class TranscribeResult:
    transcript: Transcript
    load_s: float  # model load time
    wall_s: float  # decode + VAD + transcription time, excluding model load
    adjusted_words: int  # words whose timestamps were clamped to keep them monotonic

    @property
    def rtf(self) -> float:
        """Real-time factor: wall time / media duration (< 1 is faster than real time)."""
        d = self.transcript.duration_s
        return self.wall_s / d if d > 0 else float("inf")


def load_model(cfg: TranscribeConfig) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        cfg.model_size,
        device=cfg.device,
        compute_type=cfg.compute_type,
        cpu_threads=cfg.cpu_threads,
    )


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


def _t(x: float) -> float:
    return max(0.0, round(float(x), 3))


def build_transcript(
    segments: Iterable[Any],
    *,
    video: str,
    video_id: str,
    duration_s: float,
    language: str,
    model: str,
    on_segment: Callable[[float], None] | None = None,
) -> tuple[Transcript, int]:
    """Convert whisper-style segments to the transcript contract.

    Whisper word timings sometimes overlap or run backwards. Each word is clamped so it starts
    no earlier than the previous word ends, and ends no earlier than it starts. Returns the
    transcript and the number of words that needed clamping.
    """
    out_segs: list[Segment] = []
    out_words: list[Word] = []
    adjusted = 0
    prev_seg_start = 0.0
    prev_end = 0.0

    for raw in segments:
        idx = len(out_segs)
        s_start = max(_t(raw.start), prev_seg_start)
        s_end = max(_t(raw.end), s_start)
        out_segs.append(Segment(id=idx, start=s_start, end=s_end, text=raw.text.strip()))
        prev_seg_start = s_start

        for rw in raw.words or ():
            text = rw.word.strip()
            if not text:
                continue
            w_start, w_end = _t(rw.start), _t(rw.end)
            start = max(w_start, prev_end)
            end = max(w_end, start)
            if (start, end) != (w_start, w_end):
                adjusted += 1
            out_words.append(Word(seg=idx, w=text, start=start, end=end))
            prev_end = end

        if on_segment is not None:
            on_segment(s_end)

    transcript = Transcript(
        video=video,
        video_id=video_id,
        duration_s=_t(duration_s),
        language=language,
        model=model,
        segments=out_segs,
        words=out_words,
    )
    return transcript, adjusted


def contract_video_path(video: Path) -> str:
    """Path as stored in the contract: relative to the repo root (cwd) when possible."""
    p = video.resolve()
    try:
        return p.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def transcribe_file(
    video: Path,
    cfg: TranscribeConfig,
    *,
    on_progress: Callable[[float, float], None] | None = None,
) -> TranscribeResult:
    t0 = time.perf_counter()
    model = load_model(cfg)
    load_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    audio = decode_audio(video)
    # faster-whisper returns a lazy generator: the real work happens in build_transcript.
    segments, info = model.transcribe(
        audio,
        language=cfg.language,
        beam_size=cfg.beam_size,
        vad_filter=cfg.vad_filter,
        word_timestamps=True,
    )
    on_segment = None
    if on_progress is not None:

        def on_segment(end_s: float) -> None:
            on_progress(end_s, info.duration)

    transcript, adjusted = build_transcript(
        segments,
        video=contract_video_path(video),
        video_id=video.stem,
        duration_s=info.duration,
        language=info.language,
        model=cfg.model_size,
        on_segment=on_segment,
    )
    wall_s = time.perf_counter() - t1
    return TranscribeResult(transcript, load_s=load_s, wall_s=wall_s, adjusted_words=adjusted)


def write_transcript(transcript: Transcript, out_dir: Path) -> Path:
    """Write <out_dir>/<video_id>.json atomically."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{transcript.video_id}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(transcript.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_transcript(path: Path) -> Transcript:
    return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
