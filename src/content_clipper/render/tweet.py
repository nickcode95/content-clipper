"""Tweet-style hook card: round profile picture, display name, @handle, text. Built with Pillow.

Personas are images in assets/personas/ named after the handle base (hagrids_beard.webp).
Each render picks a persona (avoiding the most recent ones) and adds random digits to the
handle, so every video shows a different @handle. Display names come from
assets/personas/names.yaml (`hagrids_beard: Hagrid's Beard`) or are made from the file name.
No platform logo, no verified badge, no like or view counts.
"""

from __future__ import annotations

import json
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont, ImageOps

PERSONAS_DIR = Path("assets/personas")
IMAGE_EXT = {".webp", ".png", ".jpg", ".jpeg", ".gif"}

THEMES = {
    "light": {"bg": (255, 255, 255, 255), "text": (15, 20, 25), "muted": (83, 100, 113)},
    "dark": {"bg": (21, 24, 28, 255), "text": (231, 233, 234), "muted": (113, 118, 123)},
}


class TweetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Persona:
    base: str  # handle without digits
    display_name: str
    image: Path


def _font_file(pattern: str) -> str:
    out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True,
                         check=False).stdout.strip()
    if not out:
        raise TweetError(f"no font found for {pattern!r}")
    return out


def load_personas(folder: Path = PERSONAS_DIR) -> list[Persona]:
    names_file = folder / "names.yaml"
    names = yaml.safe_load(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
    personas = [
        Persona(p.stem, str((names or {}).get(p.stem) or p.stem.replace("_", " ").title()), p)
        for p in sorted(folder.glob("*")) if p.suffix.lower() in IMAGE_EXT
    ]
    if not personas:
        raise TweetError(f"no persona images in {folder} (webp, png or jpg named after the handle)")
    return personas


def pick_persona(personas: list[Persona], history: Path, *, recent: int = 3,
                 rng: random.Random | None = None) -> tuple[Persona, str]:
    """Choose a persona not used in the last `recent` renders (when possible) + a fresh handle."""
    rng = rng or random.Random()
    used = json.loads(history.read_text()) if history.exists() else []
    fresh = [p for p in personas if p.base not in used[-recent:]] or personas
    persona = rng.choice(fresh)
    handle = f"{persona.base}_{rng.randint(10000, 99999)}"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(json.dumps((used + [persona.base])[-50:]))
    return persona, handle


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and font.getlength(trial) <= width:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def _avatar(path: Path, size: int) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(path))
    img.seek(0)  # first frame of animated images
    img = ImageOps.fit(img.convert("RGBA"), (size * 4, size * 4), Image.Resampling.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, *img.size), fill=255)
    img.putalpha(mask)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def make_card(text: str, persona: Persona, handle: str, *, width: int, max_height: int,
              theme: str = "light", text_size: int = 46) -> Image.Image:
    """The card as an RGBA image `width` px wide and at most `max_height` px tall."""
    colours = THEMES[theme]
    bold, regular = _font_file("DejaVu Sans:bold"), _font_file("DejaVu Sans")
    pad, avatar, gap = 34, 96, 22
    while True:
        body = ImageFont.truetype(regular, text_size)
        lines = _wrap(text, body, width - 2 * pad)
        line_h = round(text_size * 1.3)
        height = pad + avatar + gap + len(lines) * line_h + pad - (line_h - text_size) // 2
        if height <= max_height or text_size <= 30:
            break
        text_size -= 2
    if height > max_height:
        raise TweetError(f"tweet text too long for a {max_height}px card: shorten it")

    scale = 2  # draw at 2x and downsample for smooth corners
    card = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, width * scale - 1, height * scale - 1), radius=28 * scale,
                        fill=colours["bg"])
    card = card.resize((width, height), Image.Resampling.LANCZOS)
    card.alpha_composite(_avatar(persona.image, avatar), (pad, pad))

    d = ImageDraw.Draw(card)
    name_font = ImageFont.truetype(bold, 36)
    handle_font = ImageFont.truetype(regular, 32)
    x = pad + avatar + 22
    d.text((x, pad + 6), persona.display_name, font=name_font, fill=colours["text"])
    d.text((x, pad + 52), f"@{handle}", font=handle_font, fill=colours["muted"])
    y = pad + avatar + gap
    for line in lines:
        d.text((pad, y), line, font=body, fill=colours["text"])
        y += line_h
    return card
