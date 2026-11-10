---
name: render-clip
description: Use when writing or changing the render stage. Cuts approved clips with ffmpeg, reframes to 9:16, burns word-highlight captions and the hook text, and optionally mixes licensed music.
---
# Rendering clips

Settings come from config/render.yaml. Render only clips with approved == true.

- Re-encode, never stream copy, so cuts are frame-accurate. Verify the output duration with ffprobe.
- Output 1080x1920, 30 fps, libx264 preset veryfast, crf 20, yuv420p, aac, `-movflags +faststart`.
- Captions: build an ASS file with pysubs2 from word timestamps, highlighting the current word,
  at most 4 words per line. Burn with the ffmpeg `ass` filter. Run ffmpeg with the temp folder as
  the working directory and relative filenames to avoid path-escaping problems.
- Hook text: a second ASS style shown for the first config.hook.seconds, inside the safe area.
- Keep all text out of the top 10% and bottom 20% of the frame (platform UI overlaps these areas).
- Audio: normalise with loudnorm using the config values. Music, when used, is mixed under the
  speech with `sidechaincompress`, then `amix`, with fades from config.music.
- Music must come from the music-licensing skill. If no track is eligible, render without music.
- Save ffmpeg stderr to a .log next to the output and fail loudly on a non-zero exit.
- Face-tracked crop: OpenCV Haar cascade, smoothed, with a centre-crop fallback (see docs/ARCHITECTURE.md).
