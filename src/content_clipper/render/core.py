"""Render stage: approved clips -> 1080x1920 MP4 with the band layout, hook and word captions.

Band layout: the full-width source video sits in the middle of the 9:16 canvas over a blurred,
darkened copy of itself (or a solid colour). The hook goes in the band below the video and the
word-highlight captions in the band above (configurable). All text stays inside the safe area
(top 10% and bottom 20% are covered by platform UI).

Always re-encodes (frame-accurate cuts), keeps only the source audio (loudness-normalised), and
verifies the output duration with ffprobe.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pysubs2

from content_clipper.campaign.store import require_reviewed
from content_clipper.config import RenderConfig
from content_clipper.media import duration_s, probe
from content_clipper.models import (
    CampaignRules,
    Clip,
    ClipsFile,
    Shot,
    ShotAnalysis,
    Transcript,
)
from content_clipper.sources import is_registered

CHAR_W = 0.6  # average glyph width of DejaVu Sans Bold, as a fraction of the font size
LINE_H = 1.2
SIDE_MARGIN = 70
GROUP_GAP_S = 0.6
DURATION_TOLERANCE_S = 0.1
MIN_BAND = 150  # px kept for each text band


class RenderError(RuntimeError):
    pass


# ---------------------------------------------------------------- geometry (pure)


@dataclass(frozen=True)
class Box:
    top: int
    bottom: int

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> int:
        return (self.top + self.bottom) // 2


@dataclass(frozen=True)
class BandGeometry:
    video_x: int
    video_y: int
    video_w: int
    video_h: int
    safe: Box
    hook: Box
    captions: Box


def _even(x: float) -> int:
    return int(round(x / 2) * 2)


def band_geometry(cfg: RenderConfig, src_w: int, src_h: int) -> BandGeometry:
    W, H = cfg.width, cfg.height
    safe = Box(round(H * cfg.captions.safe_top_pct / 100),
               round(H * (1 - cfg.captions.safe_bottom_pct / 100)))
    vw, vh = W, _even(W * src_h / src_w)
    max_h = _even(safe.height - 2 * MIN_BAND)  # tall sources: fit height, keep text bands
    if vh > max_h:
        vw, vh = _even(max_h * src_w / src_h), max_h
    vy = _even(cfg.layout.video_center_y * H - vh / 2)
    vy = max(safe.top + MIN_BAND, min(vy, safe.bottom - MIN_BAND - vh))
    above, below = Box(safe.top, vy), Box(vy + vh, safe.bottom)
    hook, captions = (below, above) if cfg.layout.hook_position == "below" else (above, below)
    if hook.height < MIN_BAND or captions.height < MIN_BAND:
        raise RenderError(f"layout leaves less than {MIN_BAND} px for the hook or the captions")
    return BandGeometry((W - vw) // 2, vy, vw, vh, safe, hook, captions)


def crop_geometry(cfg: RenderConfig) -> BandGeometry:
    """Full-frame 9:16 layout: only the caption position matters (no hook band)."""
    H = cfg.height
    safe = Box(round(H * cfg.captions.safe_top_pct / 100),
               round(H * (1 - cfg.captions.safe_bottom_pct / 100)))
    cy = round(cfg.layout.crop_caption_y * H)
    captions = Box(max(safe.top, cy - MIN_BAND), min(safe.bottom, cy + MIN_BAND))
    return BandGeometry(0, 0, cfg.width, H, safe, Box(safe.top, safe.top), captions)


@dataclass(frozen=True)
class CropPlan:
    w: int  # crop size in source pixels (9:16)
    h: int
    y: int
    xs: list[tuple[float, int]]  # (clip-relative start time, crop x) per run of shots


def crop_plan(src_w: int, src_h: int, shots: list[Shot], clip: Clip) -> CropPlan:
    """One crop position per shot, centred on the shot's action (centre when unknown)."""
    if src_w * 16 >= src_h * 9:  # wider than 9:16: crop the width
        w, h = min(src_w, _even(src_h * 9 / 16)), src_h
    else:  # narrower: crop the height
        w, h = src_w, min(src_h, _even(src_w * 16 / 9))
    xs: list[tuple[float, int]] = []
    for s in shots:
        if s.end <= clip.start_s or s.start >= clip.end_s:
            continue
        focus = 0.5 if s.focus_x is None else s.focus_x
        x = max(0, min(src_w - w, _even(focus * src_w - w / 2)))
        t = round(max(0.0, s.start - clip.start_s), 3)
        if not xs or xs[-1][1] != x:
            xs.append((t, x))
    if not xs:
        xs = [(0.0, (src_w - w) // 2 // 2 * 2)]
    return CropPlan(w, h, (src_h - h) // 2 // 2 * 2, xs)


def crop_x_expr(plan: CropPlan) -> str:
    """ffmpeg expression for x(t): a step at each shot change (no panning inside a shot)."""
    expr = str(plan.xs[0][1])
    for (_, prev), (t, x) in zip(plan.xs, plan.xs[1:], strict=False):
        expr += f"+({x - prev})*gte(t,{t:.3f})"
    return expr


# ---------------------------------------------------------------- text (pure)


def text_width(text: str, size: float) -> float:
    return len(text) * CHAR_W * size


def wrap(words: list[str], size: float, max_width: float) -> list[list[int]]:
    """Greedy line breaks; returns word indices per line."""
    lines: list[list[int]] = [[]]
    for i, w in enumerate(words):
        trial = " ".join(words[j] for j in lines[-1] + [i])
        if lines[-1] and text_width(trial, size) > max_width:
            lines.append([i])
        else:
            lines[-1].append(i)
    return [ln for ln in lines if ln]


def fit_size(text: str, size: int, box: Box, max_width: float, min_size: int = 36) -> int:
    words = text.split()
    while size > min_size and len(wrap(words, size, max_width)) * size * LINE_H > box.height * 0.9:
        size -= 4
    return size


def _ms(t: float) -> int:
    return round(t * 1000)


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def ass_colour(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


@dataclass(frozen=True)
class RelWord:
    w: str
    start: float
    end: float
    seg: int


def clip_words(transcript: Transcript, clip: Clip) -> list[RelWord]:
    eps = 0.05
    dur = clip.end_s - clip.start_s
    out = []
    for w in transcript.words:
        if w.start >= clip.start_s - eps and w.end <= clip.end_s + eps:
            s = max(0.0, w.start - clip.start_s)
            out.append(RelWord(w.w, round(s, 3), round(min(dur, max(s, w.end - clip.start_s)), 3),
                               w.seg))
    return out


def caption_groups(words: list[RelWord], max_words: int) -> list[list[RelWord]]:
    groups: list[list[RelWord]] = []
    for w in words:
        g = groups[-1] if groups else None
        if (g is None or len(g) >= max_words or w.seg != g[-1].seg
                or w.start - g[-1].end > GROUP_GAP_S):
            groups.append([w])
        else:
            g.append(w)
    return groups


def build_ass(
    words: list[RelWord],
    hook_text: str | None,
    geom: BandGeometry,
    cfg: RenderConfig,
    duration: float,
) -> pysubs2.SSAFile:
    W = cfg.width
    max_w = W - 2 * SIDE_MARGIN
    subs = pysubs2.SSAFile()
    subs.info.update({"PlayResX": str(W), "PlayResY": str(cfg.height), "WrapStyle": "2",
                      "ScaledBorderAndShadow": "yes"})
    white, black = pysubs2.Color(255, 255, 255), pysubs2.Color(0, 0, 0)
    base = {"fontname": cfg.captions.font, "bold": cfg.captions.bold, "primarycolor": white,
            "outlinecolor": black, "backcolor": pysubs2.Color(0, 0, 0, 128), "outline": 5,
            "shadow": 0, "alignment": pysubs2.Alignment.MIDDLE_CENTER,
            "marginl": SIDE_MARGIN, "marginr": SIDE_MARGIN, "marginv": 0}
    subs.styles["Caption"] = pysubs2.SSAStyle(fontsize=cfg.captions.size, **base)
    hl = ass_colour(cfg.captions.highlight_color)

    cy = geom.captions.center
    groups = caption_groups(words, cfg.captions.max_words_per_line)
    for gi, g in enumerate(groups):
        texts = [ass_escape(w.w) for w in g]
        lines = wrap(texts, cfg.captions.size, max_w)
        next_start = groups[gi + 1][0].start if gi + 1 < len(groups) else duration
        for i, w in enumerate(g):
            end = g[i + 1].start if i + 1 < len(g) else min(next_start, w.end + 0.4)
            end = max(end, w.start + 0.05)
            rendered = "\\N".join(
                " ".join(f"{{\\c{hl}}}{texts[j]}{{\\c&HFFFFFF&}}" if j == i else texts[j]
                         for j in line)
                for line in lines
            )
            subs.events.append(pysubs2.SSAEvent(
                start=_ms(w.start), end=_ms(min(end, duration)), style="Caption",
                text=f"{{\\pos({W // 2},{cy})}}{rendered}"))

    if hook_text:
        size = fit_size(hook_text, cfg.hook.size, geom.hook, max_w)
        subs.styles["Hook"] = pysubs2.SSAStyle(fontsize=size, **base)
        words_h = [ass_escape(t) for t in hook_text.split()]
        text = "\\N".join(" ".join(words_h[j] for j in ln) for ln in wrap(words_h, size, max_w))
        until = duration if cfg.hook.seconds <= 0 else min(cfg.hook.seconds, duration)
        subs.events.append(pysubs2.SSAEvent(
            start=0, end=_ms(until), style="Hook",
            text=f"{{\\pos({W // 2},{geom.hook.center})}}{text}"))
    return subs


def text_boxes(subs: pysubs2.SSAFile) -> list[tuple[int, int]]:
    """Vertical extent (top, bottom) of every event, from its \\pos, style size and line count."""
    out = []
    for ev in subs.events:
        y = int(re.search(r"\\pos\(\d+,(\d+)\)", ev.text).group(1))
        size = subs.styles[ev.style].fontsize
        h = (ev.text.count("\\N") + 1) * size * LINE_H
        out.append((int(y - h / 2), int(y + h / 2)))
    return out


# ---------------------------------------------------------------- ffmpeg


def hook_for(clip: Clip, rules: CampaignRules) -> str | None:
    if rules.on_screen_text.policy == "forbidden" or rules.hook_style.mode == "none":
        return None
    return clip.hook_line.strip() or None


def crop_filter_graph(plan: CropPlan, cfg: RenderConfig) -> str:
    return (f"[0:v]fps={cfg.fps},crop=w={plan.w}:h={plan.h}:x='{crop_x_expr(plan)}':y={plan.y},"
            f"scale={cfg.width}:{cfg.height},setsar=1,ass=clip.ass,format={cfg.video.pix_fmt}[v]")


def filter_graph(geom: BandGeometry, cfg: RenderConfig) -> str:
    W, H, fps = cfg.width, cfg.height, cfg.fps
    fg = (f"[fgsrc]scale={geom.video_w}:{geom.video_h}[fg];"
          f"[bg][fg]overlay=x={geom.video_x}:y={geom.video_y}:shortest=1,"
          f"ass=clip.ass,format={cfg.video.pix_fmt}[v]")
    if cfg.layout.background == "blur":
        return (f"[0:v]fps={fps},split=2[bgsrc][fgsrc];"
                f"[bgsrc]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                f"crop={W // 4}:{H // 4},boxblur=8:2,scale={W}:{H},"
                f"eq=brightness=-0.15:saturation=1.2[bg];" + fg)
    colour = cfg.layout.background_color.lstrip("#")
    return (f"[0:v]fps={fps}[fgsrc];color=c=0x{colour}:s={W}x{H}:r={fps}[bg];" + fg)


@dataclass(frozen=True)
class Rendered:
    path: Path
    duration_s: float
    render_json: dict


def render_clip(
    clip: Clip,
    *,
    source: Path,
    transcript: Transcript,
    rules: CampaignRules,
    campaign: str,
    cfg: RenderConfig,
    out_dir: Path,
    layout: str = "band",
    shots: list[Shot] | None = None,
) -> Rendered:
    info = probe(source)
    vstream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if vstream is None or not any(s["codec_type"] == "audio" for s in info["streams"]):
        raise RenderError(f"{source} needs a video and an audio stream")
    src_w, src_h = int(vstream["width"]), int(vstream["height"])
    dur = round(clip.end_s - clip.start_s, 3)
    plan = None
    if layout == "crop":
        geom, hook = crop_geometry(cfg), None  # full-screen: captions only, no hook
        plan = crop_plan(src_w, src_h, shots or [], clip)
        graph = crop_filter_graph(plan, cfg)
    else:
        geom, hook = band_geometry(cfg, src_w, src_h), hook_for(clip, rules)
        graph = filter_graph(geom, cfg)
    subs = build_ass(clip_words(transcript, clip), hook, geom, cfg, dur)
    for top, bottom in text_boxes(subs):
        if top < geom.safe.top or bottom > geom.safe.bottom:
            raise RenderError(f"text box {top}-{bottom} px leaves the safe area")

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = clip.id if layout == "band" else f"{clip.id}.{layout}"  # band files keep plain names
    mp4, log = out_dir / f"{stem}.mp4", out_dir / f"{stem}.log"
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        subs.save(str(tmp / "clip.ass"))
        shutil.copy(tmp / "clip.ass", out_dir / f"{stem}.ass")
        cmd = [
            "ffmpeg", "-nostdin", "-hide_banner", "-y",
            "-ss", f"{clip.start_s:.3f}", "-t", f"{dur:.3f}", "-i", str(source.resolve()),
            "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0",
            "-af", f"loudnorm={cfg.audio.loudnorm},aresample=48000",
            "-c:v", cfg.video.codec, "-preset", cfg.video.preset, "-crf", str(cfg.video.crf),
            "-pix_fmt", cfg.video.pix_fmt, "-r", str(cfg.fps),
            "-c:a", cfg.audio.codec, "-b:a", cfg.audio.bitrate, "-movflags", "+faststart",
            "out.mp4",
        ]
        with log.open("wb") as f:
            code = subprocess.run(cmd, cwd=tmp, stdout=subprocess.DEVNULL, stderr=f,
                                  check=False).returncode
        if code != 0:
            tail = log.read_text(errors="replace").strip().splitlines()[-5:]
            raise RenderError(f"ffmpeg failed for {clip.id} (see {log}): " + " | ".join(tail))
        os.replace(tmp / "out.mp4", mp4)

    got = duration_s(mp4)
    if abs(got - dur) > DURATION_TOLERANCE_S:
        raise RenderError(f"{mp4} is {got:.3f} s, expected {dur:.3f} s (+-{DURATION_TOLERANCE_S})")
    meta = {
        "clip_id": clip.id, "campaign": campaign, "source_video": source.as_posix(),
        "start_s": clip.start_s, "end_s": clip.end_s, "duration_s": round(got, 3),
        "layout": layout, "background": cfg.layout.background if layout == "band" else None,
        "crop": None if plan is None else {"w": plan.w, "h": plan.h, "xs": plan.xs},
        "music": None, "ai_voice": False, "hook_text": hook, "on_screen_lines": [],
    }
    (out_dir / f"{stem}.render.json").write_text(json.dumps(meta, indent=2) + "\n")
    return Rendered(mp4, round(got, 3), meta)


# ---------------------------------------------------------------- stage


def set_approval(clips_path: Path, clip_ids: list[str], approved: bool | None) -> ClipsFile:
    """Human action: mark clips approved (true), rejected (false) or undecided (None)."""
    clips = ClipsFile.model_validate_json(clips_path.read_text(encoding="utf-8"))
    known = {c.id for c in clips.clips}
    missing = [i for i in clip_ids if i not in known]
    if missing:
        raise RenderError(f"unknown clip id(s) {missing}; {clips_path} has {sorted(known)}")
    for c in clips.clips:
        if c.id in clip_ids:
            c.approved = approved
    tmp = clips_path.with_name(clips_path.name + ".tmp")
    tmp.write_text(clips.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, clips_path)
    return clips


def render_approved(
    campaign: str,
    video_id: str,
    *,
    cfg: RenderConfig,
    only: list[str] | None = None,
    layout: str | None = None,
    campaigns_root: Path = Path("campaigns"),
    data: Path = Path("data"),
) -> list[Rendered]:
    layout = layout or cfg.layout.mode
    c = require_reviewed(campaign, campaigns_root)
    registry = data / "sources" / "registry.csv"
    if c.rules.source.require_registry and not is_registered(video_id, campaign, registry):
        raise RenderError(f"{video_id!r} is not registered for {campaign!r} in {registry}")
    clips_path = data / "clips" / f"{video_id}.clips.json"
    if not clips_path.exists():
        raise RenderError(f"{clips_path} not found; run `content-clipper select` first")
    clips = ClipsFile.model_validate_json(clips_path.read_text(encoding="utf-8"))
    if clips.campaign != campaign:
        raise RenderError(f"{clips_path} was selected for {clips.campaign!r}, not {campaign!r}")
    todo = [x for x in clips.clips if x.approved is True and (not only or x.id in only)]
    if layout == "crop":  # no hook in this layout, so hook variants would be identical videos
        todo = [x for x in todo if x.variant_of is None]
    if not todo:
        raise RenderError(
            f"no approved clips to render; run `content-clipper approve-clips {video_id!r} c01 ...`")
    t_path = data / "transcripts" / f"{video_id}.json"
    transcript = Transcript.model_validate_json(t_path.read_text(encoding="utf-8"))
    source = Path(transcript.video)
    if not source.exists():
        raise RenderError(f"source video {source} not found")
    shots = None
    if layout == "crop":
        a_path = data / "analysis" / video_id / "shots.json"
        if not a_path.exists():
            raise RenderError(f"{a_path} not found; run `content-clipper analyze <video>` first")
        shots = ShotAnalysis.model_validate_json(a_path.read_text(encoding="utf-8")).shots
    out_dir = data / "out" / campaign / video_id
    return [render_clip(x, source=source, transcript=transcript, rules=c.rules,
                        campaign=campaign, cfg=cfg, out_dir=out_dir, layout=layout, shots=shots)
            for x in todo]
