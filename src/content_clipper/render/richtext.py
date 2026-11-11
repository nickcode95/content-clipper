"""Text with colour emoji, drawn with Pillow.

libass (the ASS caption renderer in ffmpeg) cannot draw colour emoji, so any on-screen text that
contains emoji is drawn here: plain text in a TrueType font, emoji clusters with Noto Color Emoji
(a bitmap font that only exists at 109 px, so each emoji is drawn at 109 px and scaled to fit).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import cache

from PIL import Image, ImageDraw, ImageFont

ZWJ, VS16, KEYCAP = "‍", "️", "⃣"
EMOJI_NATIVE_SIZE = 109

_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2300, 0x23FF), (0x2B00, 0x2BFF),
    (0x1F1E6, 0x1F1FF), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3299),
)


def _in_ranges(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def _joins(ch: str) -> bool:
    cp = ord(ch)
    return ch in (ZWJ, VS16, KEYCAP) or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F


def split_runs(text: str) -> list[tuple[str, bool]]:
    """[(chunk, is_emoji)]; emoji clusters (ZWJ sequences, skin tones, flags, keycaps) stay whole."""
    runs: list[tuple[str, bool]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if _in_ranges(ch) or nxt in (VS16, KEYCAP):
            j = i + 1
            if 0x1F1E6 <= ord(ch) <= 0x1F1FF and j < n and 0x1F1E6 <= ord(text[j]) <= 0x1F1FF:
                j += 1  # flag: pair of regional indicators
            while j < n and (_joins(text[j]) or (text[j - 1] == ZWJ)):
                j += 1
            runs.append((text[i:j], True))
            i = j
        else:
            j = i + 1
            while j < n and not (_in_ranges(text[j]) or (j + 1 < n and text[j + 1] in (VS16, KEYCAP))):
                j += 1
            if runs and not runs[-1][1]:
                runs[-1] = (runs[-1][0] + text[i:j], False)
            else:
                runs.append((text[i:j], False))
            i = j
    return runs


def has_emoji(text: str) -> bool:
    return any(is_emoji for _, is_emoji in split_runs(text))


def font_file(pattern: str) -> str:
    out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True,
                         check=False).stdout.strip()
    if not out:
        raise RuntimeError(f"no font found for {pattern!r}")
    return out


@cache
def _emoji_font() -> ImageFont.FreeTypeFont:
    path = font_file("Noto Color Emoji")
    if "emoji" not in path.lower():
        raise RuntimeError("Noto Color Emoji is not installed (apt package fonts-noto-color-emoji)")
    return ImageFont.truetype(path, EMOJI_NATIVE_SIZE)


@cache
def _emoji_image(cluster: str) -> Image.Image:
    font = _emoji_font()
    left, top, right, bottom = font.getbbox(cluster)
    img = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((-left, -top), cluster, font=font, embedded_color=True)
    return img


@dataclass(frozen=True)
class RichFont:
    path: str
    size: int

    @property
    def font(self) -> ImageFont.FreeTypeFont:
        return _truetype(self.path, self.size)

    @property
    def emoji_px(self) -> int:
        return round(self.size * 1.15)


@cache
def _truetype(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def run_width(chunk: str, is_emoji: bool, rf: RichFont) -> float:
    if is_emoji:
        img = _emoji_image(chunk)
        return img.width * rf.emoji_px / img.height + rf.size * 0.08
    return rf.font.getlength(chunk)


def text_width(text: str, rf: RichFont) -> float:
    return sum(run_width(c, e, rf) for c, e in split_runs(text))


def wrap(text: str, rf: RichFont, max_width: float) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and text_width(trial, rf) <= max_width:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def draw_line(img: Image.Image, xy: tuple[float, float], line: str, rf: RichFont, *,
              fill: tuple, stroke_width: int = 0, stroke_fill: tuple | None = None) -> None:
    """Draw one line at (x, y) where y is the top of a line `rf.size` tall."""
    x, y = xy
    draw = ImageDraw.Draw(img)
    ascent, _ = rf.font.getmetrics()
    for chunk, is_emoji in split_runs(line):
        if is_emoji:
            em = _emoji_image(chunk)
            h = rf.emoji_px
            w = round(em.width * h / em.height)
            scaled = em.resize((w, h), Image.Resampling.LANCZOS)
            img.alpha_composite(scaled, (round(x), round(y + ascent - h * 0.92)))
            x += w + rf.size * 0.08
        else:
            draw.text((x, y), chunk, font=rf.font, fill=fill, stroke_width=stroke_width,
                      stroke_fill=stroke_fill)
            x += rf.font.getlength(chunk)


def text_block(text: str, rf: RichFont, *, max_width: int, fill: tuple, stroke_width: int = 0,
               stroke_fill: tuple | None = None, line_spacing: float = 1.25) -> Image.Image:
    """Centred multi-line text (with emoji) as a transparent RGBA image."""
    lines = wrap(text, rf, max_width)
    line_h = round(rf.size * line_spacing)
    pad = stroke_width + 4
    width = max(round(text_width(ln, rf)) for ln in lines) + 2 * pad
    height = line_h * len(lines) + 2 * pad
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for i, ln in enumerate(lines):
        x = (width - text_width(ln, rf)) / 2
        draw_line(img, (x, pad + i * line_h), ln, rf, fill=fill, stroke_width=stroke_width,
                  stroke_fill=stroke_fill)
    return img
