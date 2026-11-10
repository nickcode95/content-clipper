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
- Layout (config/render.yaml `layout`): `band` puts the full-width video in the middle of the
  1080x1920 canvas over a blurred, darkened copy of itself (`background: blur`) or a solid colour.
  The hook goes in the band below the video (`hook_position: below`) and the word captions in the
  band above; swap with `hook_position: above`. `hook.seconds: 0` keeps the hook up for the whole
  clip. Text is wrapped and sized in code and every text box is checked against the safe area.
- Game footage rarely has faces, so face tracking is not the default. An action-following 9:16
  crop is planned as a second layout (S5).
- Human gate: `content-clipper approve-clips <video_id> c01 c02` sets approved: true; `render
  <campaign> <video_id>` renders only approved clips and re-checks the campaign and the registry.
- Code: src/content_clipper/render/core.py.

<!-- campaign-aware -->
## Campaign awareness
- Output to data/out/<campaign>/<video_id>/<clip_id>.mp4 and write <clip_id>.render.json with
  source video, campaign, music track (or null), on-screen lines drawn, hook text, ai voice false.
- Add music only when rules.audio.music_allowed is true AND the music-licensing skill finds an
  eligible track. Otherwise keep the original audio untouched.
- Draw overlay text produced by the caption stage. Required on-screen lines (required_lines with
  where on_screen) are drawn for at least config.hook.seconds. Honour hook_style.mode and
  on_screen_text.policy. Never invent overlay text in the render stage.
- Refuse to render when rules.status is draft.
