"""Render stage: approved clips -> 1080x1920 MP4 with the band layout, hook and word captions.

Band layout: the full-width source video sits in the middle of the 9:16 canvas over a blurred,
darkened copy of itself (or a solid colour). The hook goes in the band below the video and the
word-highlight captions in the band above (configurable). All text stays inside the safe area
(top 10% and bottom 20% are covered by platform UI).

Always re-encodes (frame-accurate cuts), keeps only the source audio (loudness-normalised), and
verifies the output duration with ffprobe. An optional outro (a span of the same source, e.g.
its title card, saved with `set-outro`) is appended after the clip.
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
from PIL import Image

from content_clipper.campaign.store import require_reviewed
from content_clipper.config import RenderConfig
from content_clipper.media import duration_s, probe
from content_clipper.models import (
    CampaignRules,
    Clip,
    ClipsFile,
    Outro,
    Shot,
    ShotAnalysis,
    Transcript,
    Word,
)
from content_clipper.render import richtext as rt
from content_clipper.render import tweet
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


def hook_image(text: str, cfg: RenderConfig, geom: BandGeometry) -> Image.Image:
    """Plain hook text with colour emoji, drawn like the ASS hook (white, black outline)."""
    bold = rt.font_file(f"{cfg.captions.font}:bold" if cfg.captions.bold else cfg.captions.font)
    size = cfg.hook.size
    while True:
        img = rt.text_block(text, rt.RichFont(bold, size), max_width=cfg.width - 2 * SIDE_MARGIN,
                            fill=(255, 255, 255, 255), stroke_width=5, stroke_fill=(0, 0, 0, 255))
        if img.height <= geom.hook.height * 0.9 or size <= 36:
            return img
        size -= 4


def _finish(cfg: RenderConfig, card_xy: tuple[int, int] | None, out: str = "v") -> str:
    """[base] -> optional card overlay (input 1) -> captions -> [out]."""
    over = f"[base][1:v]overlay=x={card_xy[0]}:y={card_xy[1]}," if card_xy else "[base]"
    return f"{over}ass=clip.ass,format={cfg.video.pix_fmt}[{out}]"


def crop_filter_graph(plan: CropPlan, cfg: RenderConfig,
                      card_xy: tuple[int, int] | None = None, out: str = "v") -> str:
    return (f"[0:v]fps={cfg.fps},crop=w={plan.w}:h={plan.h}:x='{crop_x_expr(plan)}':y={plan.y},"
            f"scale={cfg.width}:{cfg.height},setsar=1[base];" + _finish(cfg, card_xy, out))


def _band(inp: int, geom: BandGeometry, cfg: RenderConfig, p: str = "") -> str:
    """Input `inp` full-width in the middle of the canvas over its blurred self -> [<p>base]."""
    W, H, fps = cfg.width, cfg.height, cfg.fps
    fg = (f"[{p}fgsrc]scale={geom.video_w}:{geom.video_h}[{p}fg];"
          f"[{p}bg][{p}fg]overlay=x={geom.video_x}:y={geom.video_y}:shortest=1[{p}base]")
    if cfg.layout.background == "blur":
        return (f"[{inp}:v]fps={fps},split=2[{p}bgsrc][{p}fgsrc];"
                f"[{p}bgsrc]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                f"crop={W // 4}:{H // 4},boxblur=8:2,scale={W}:{H},"
                f"eq=brightness=-0.15:saturation=1.2[{p}bg];" + fg)
    colour = cfg.layout.background_color.lstrip("#")
    return f"[{inp}:v]fps={fps}[{p}fgsrc];color=c=0x{colour}:s={W}x{H}:r={fps}[{p}bg];" + fg


def filter_graph(geom: BandGeometry, cfg: RenderConfig,
                 card_xy: tuple[int, int] | None = None, out: str = "v") -> str:
    return _band(0, geom, cfg) + ";" + _finish(cfg, card_xy, out)


# ---------------------------------------------------------------- outro

OUTRO_FADE_S = 0.3  # outro audio fades out over this long ...
SPEECH_MARGIN_S = 0.15  # ... ending this long before the first word spoken inside the outro
MIN_FADE_S = 0.01  # outro audio shorter than this before speech is muted outright
TAIL_MARGIN_S = 0.05  # the clip's sound tail under the outro ends this long before the next word


def outro_path(video_id: str, data: Path = Path("data")) -> Path:
    return data / "outro" / f"{video_id}.json"


def load_outro(video_id: str, data: Path = Path("data")) -> Outro | None:
    p = outro_path(video_id, data)
    return Outro.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def save_outro(outro: Outro, transcript: Transcript, data: Path = Path("data")) -> Path:
    """Human action: remember which span of the source video to play after every clip."""
    if outro.video_id != transcript.video_id:
        raise RenderError(f"outro is for {outro.video_id!r}, transcript for {transcript.video_id!r}")
    if outro.end_s > transcript.duration_s + 0.05:
        raise RenderError(f"outro ends at {outro.end_s} s but {outro.video_id!r} is only "
                          f"{transcript.duration_s:.3f} s long")
    p = outro_path(outro.video_id, data)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(outro.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def outro_speech(outro: Outro, transcript: Transcript) -> list[Word]:
    return [w for w in transcript.words if w.end > outro.start_s and w.start < outro.end_s]


def outro_audio_until(outro: Outro, transcript: Transcript) -> float:
    """Seconds into the outro at which its audio has faded to silence: before the first word
    spoken inside it (Whisper word timestamps), so no sentence starts and gets cut off."""
    speech = outro_speech(outro, transcript)
    if not speech:
        return round(outro.end_s - outro.start_s, 3)
    return round(max(0.0, speech[0].start - SPEECH_MARGIN_S - outro.start_s), 3)


def clip_tail(transcript: Transcript, clip: Clip, max_tail: float) -> float:
    """How long the clip's sound may carry on under the outro (an L-cut): up to max_tail, ending
    TAIL_MARGIN_S before the next word Whisper hears after the cut. A word Whisper places across
    the cut is ignored: where to cut was the human's call when setting the clip end."""
    nxt = min((w.start for w in transcript.words if w.start >= clip.end_s), default=None)
    room = max_tail if nxt is None else nxt - clip.end_s - TAIL_MARGIN_S
    return round(max(MIN_FADE_S, min(max_tail, room)), 3)


def outro_graph(geom: BandGeometry, cfg: RenderConfig, inp: int, *, clip_dur: float,
                outro_dur: float, audio_until: float, clip_tail: float = 0.06) -> str:
    """Append input `inp` (the outro span) to [mv] + input 0's audio -> [v] and [a].

    Video is a hard cut. The clip's sound carries on for `clip_tail` seconds under the outro's
    first frames, fading out (input 0 must run that much past the clip), while the outro's own
    sound starts untouched on its first frame: no dropout at the join (intros often open with a
    few ms of silence) and the outro's beat stays where it is. The outro always uses the band look
    so a title card is never cropped, carries no text, and its audio fades to silence at
    `audio_until`. Loudness is normalised over the joined audio.

    Both parts are cut to whole frames, video and audio alike, so the outro sound starts exactly
    on its first frame and nothing pushes its beat late."""
    if audio_until < MIN_FADE_S:
        ofade = "volume=0"
    else:
        d = min(OUTRO_FADE_S, audio_until)
        ofade = f"afade=t=out:st={audio_until - d:.3f}:d={d:.3f}"
    n_clip, n_outro = max(1, round(clip_dur * cfg.fps)), max(1, round(outro_dur * cfg.fps))
    clip_len, outro_len = n_clip / cfg.fps, n_outro / cfg.fps
    tail = max(MIN_FADE_S, min(clip_tail, outro_len))
    return (_band(inp, geom, cfg, "o") +
            f";[obase]trim=end_frame={n_outro},setpts=PTS-STARTPTS,"
            f"format={cfg.video.pix_fmt},setsar=1[ov];"
            f"[mv]trim=end_frame={n_clip},setpts=PTS-STARTPTS,setsar=1[mvs];"
            f"[mvs][ov]concat=n=2:v=1:a=0[v];"
            f"[0:a:0]apad,atrim=end={clip_len + tail:.4f},asetpts=N/SR/TB[ma];"
            f"[{inp}:a:0]{ofade},apad,atrim=end={outro_len:.4f},asetpts=N/SR/TB[oa];"
            f"[ma][oa]acrossfade=d={tail:.4f}:c1=tri:c2=nofade[cat];"
            f"[cat]loudnorm={cfg.audio.loudnorm},aresample=48000[a]")


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
    hook_style: str = "text",
    personas_dir: Path = tweet.PERSONAS_DIR,
    persona_history: Path = Path("data/personas_used.json"),
    outro: Outro | None = None,
) -> Rendered:
    info = probe(source)
    vstream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if vstream is None or not any(s["codec_type"] == "audio" for s in info["streams"]):
        raise RenderError(f"{source} needs a video and an audio stream")
    src_w, src_h = int(vstream["width"]), int(vstream["height"])
    dur = round(clip.end_s - clip.start_s, 3)
    plan = None
    geom = crop_geometry(cfg) if layout == "crop" else band_geometry(cfg, src_w, src_h)
    hook = hook_for(clip, rules)
    card = card_xy = tweet_meta = None
    if hook_style == "tweet" and hook:
        persona, handle = tweet.pick_persona(tweet.load_personas(personas_dir), persona_history)
        max_h = 420 if layout == "crop" else geom.hook.height - 16
        try:
            card = tweet.make_card(hook, persona, handle, width=cfg.width - 130, max_height=max_h,
                                   theme=cfg.hook.tweet_theme)
        except tweet.TweetError as e:
            raise RenderError(str(e)) from e
        cw, ch = card.size
        if layout == "crop":  # card near the bottom of the safe area, captions just above it
            top = geom.safe.bottom - ch - 24
            geom = BandGeometry(0, 0, cfg.width, cfg.height, geom.safe, geom.hook,
                                Box(max(geom.safe.top, top - 300), top - 20))
        else:
            top = geom.hook.center - ch // 2
        card_xy = ((cfg.width - cw) // 2, top)
        if top < geom.safe.top or top + ch > geom.safe.bottom:
            raise RenderError(f"tweet card {top}-{top + ch} px leaves the safe area")
        tweet_meta = {"persona": persona.base, "display_name": persona.display_name,
                      "handle": handle, "text": hook}
    elif hook and layout != "crop" and rt.has_emoji(hook):
        card = hook_image(hook, cfg, geom)  # libass cannot draw colour emoji
        card_xy = ((cfg.width - card.width) // 2, geom.hook.center - card.height // 2)
    ass_hook = None if layout == "crop" or card is not None else hook
    main_out = "mv" if outro else "v"
    if layout == "crop":
        plan = crop_plan(src_w, src_h, shots or [], clip)
        graph = crop_filter_graph(plan, cfg, card_xy, main_out)
    else:
        graph = filter_graph(geom, cfg, card_xy, main_out)
    words = clip_words(transcript, clip)
    outro_dur = outro_meta = None
    read_s = dur  # how much of the source input 0 reads (more when the clip's sound carries on)
    if outro:
        outro_dur = round(outro.end_s - outro.start_s, 3)
        until = outro_audio_until(outro, transcript)
        tail = clip_tail(transcript, clip, cfg.outro.clip_tail_s)
        read_s = dur + tail + 0.1
        graph += ";" + outro_graph(band_geometry(cfg, src_w, src_h), cfg, 2 if card else 1,
                                   clip_dur=dur, outro_dur=outro_dur, audio_until=until,
                                   clip_tail=tail)
        outro_meta = {"start_s": outro.start_s, "end_s": outro.end_s, "duration_s": outro_dur,
                      "audio_until_s": until, "clip_tail_s": tail}
    expected = round(dur + (outro_dur or 0.0), 3)
    subs = build_ass(words, ass_hook, geom, cfg, dur)
    for top, bottom in text_boxes(subs):
        if top < geom.safe.top or bottom > geom.safe.bottom:
            raise RenderError(f"text box {top}-{bottom} px leaves the safe area")

    out_dir.mkdir(parents=True, exist_ok=True)
    # Default choices keep plain names (c01.mp4); others get suffixes (c01.crop.tweet.mp4).
    stem = ".".join([clip.id] + ([layout] if layout != "band" else [])
                    + (["tweet"] if tweet_meta is not None else []))
    mp4, log = out_dir / f"{stem}.mp4", out_dir / f"{stem}.log"
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        subs.save(str(tmp / "clip.ass"))
        shutil.copy(tmp / "clip.ass", out_dir / f"{stem}.ass")
        card_input: list[str] = []
        if card is not None:
            card.save(tmp / "card.png")
            shutil.copy(tmp / "card.png", out_dir / f"{stem}.card.png")
            card_input = ["-i", "card.png"]
        if outro:
            outro_input = ["-ss", f"{outro.start_s:.3f}", "-t", f"{outro_dur:.3f}",
                           "-i", str(source.resolve())]
            audio = ["-map", "[v]", "-map", "[a]"]
        else:
            outro_input = []
            audio = ["-map", "[v]", "-map", "0:a:0",
                     "-af", f"loudnorm={cfg.audio.loudnorm},aresample=48000"]
        cmd = [
            "ffmpeg", "-nostdin", "-hide_banner", "-y",
            "-ss", f"{clip.start_s:.3f}", "-t", f"{read_s:.3f}", "-i", str(source.resolve()),
            *card_input, *outro_input,
            "-filter_complex", graph, *audio,
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
    if abs(got - expected) > DURATION_TOLERANCE_S:
        raise RenderError(
            f"{mp4} is {got:.3f} s, expected {expected:.3f} s (+-{DURATION_TOLERANCE_S})")
    meta = {
        "clip_id": clip.id, "campaign": campaign, "source_video": source.as_posix(),
        "start_s": clip.start_s, "end_s": clip.end_s, "duration_s": round(got, 3),
        "layout": layout, "background": cfg.layout.background if layout == "band" else None,
        "crop": None if plan is None else {"w": plan.w, "h": plan.h, "xs": plan.xs},
        "music": None, "ai_voice": False,
        "hook_text": hook if (card is not None or layout != "crop") else None,
        "hook_style": "tweet" if tweet_meta is not None else "text", "tweet": tweet_meta,
        "on_screen_lines": [],
        "outro": outro_meta,  # span of the same source played after the clip (no text on it)
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
    hook_style: str | None = None,
    campaigns_root: Path = Path("campaigns"),
    data: Path = Path("data"),
    personas_dir: Path = tweet.PERSONAS_DIR,
    use_outro: bool = True,
) -> list[Rendered]:
    """use_outro: append the video's saved outro (data/outro/<video_id>.json) when there is one."""
    layout = layout or cfg.layout.mode
    hook_style = hook_style or cfg.hook.style
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
    if layout == "crop" and hook_style != "tweet":  # no hook, so variants would be identical
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
    outro = load_outro(video_id, data) if use_outro else None
    out_dir = data / "out" / campaign / video_id
    return [render_clip(x, source=source, transcript=transcript, rules=c.rules,
                        campaign=campaign, cfg=cfg, out_dir=out_dir, layout=layout, shots=shots,
                        hook_style=hook_style, personas_dir=personas_dir,
                        persona_history=data / "personas_used.json", outro=outro)
            for x in todo]
