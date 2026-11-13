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
- `crop` layout (`render <campaign> <video_id> --layout crop`): full-screen 9:16 cut that follows
  the action, one crop position per shot from shots.json focus_x (centre when null), captions at
  layout.crop_caption_y, no hook. Writes <clip_id>.crop.* next to the band files and skips hook
  variants (they would be identical). Game footage rarely has faces, so no face tracking.
- Tweet-style hook (`render ... --hook-style tweet`, or hook.style: tweet): the hook becomes a
  card (round profile picture, display name, @handle, text; light or dark). Personas are images
  in assets/personas/ named after the handle base (webp/png/jpg; names.yaml for display names);
  each render picks a persona not used recently (data/personas_used.json) and adds 5 random
  digits to the handle. No platform logo, verified badge or like/view counts. Files get a
  `.tweet` suffix; render.json records persona and handle. Code: render/tweet.py.
- Emoji: libass cannot draw colour emoji, so on-screen text with emoji is drawn with Pillow
  (render/richtext.py, Noto Color Emoji): tweet cards always, plain hooks when they contain emoji
  (same white text with black outline, overlaid in the hook band). Emoji-free hooks still use ASS.
- Outro (`set-outro <video_id> START END`, saved to data/outro/<video_id>.json): a span of the
  same source, usually its intro title card, played after every clip of that video. Always in
  the band look (never cropped), no text on it. Its audio fades to silence before the first
  word Whisper hears inside the span, so no sentence starts and gets cut off. Picture hard-cuts
  to the outro; the clip's sound carries on under it for outro.clip_tail_s (0.05 s), fading
  out (an L-cut), so there is no dropout (intros often open with a few ms of silence). The
  tail stops 0.05 s before the next word Whisper hears after the cut. Both parts are cut to
  whole frames, so the outro sound starts on its first frame and its beat never drifts.
  Placing the cut: end the clip where the action's sound has finished, so the outro's bass
  hit lands on the clip's beat. Never fade the clip's last hits (it drains their bass). Whisper
  word starts can be ~0.4 s early, so check a voice onset on a spectrogram
  (`showspectrumpic`): speech shows stacked horizontal harmonics, gunfire vertical streaks.
  Decode from 0 (no -ss) for timings to the millisecond. render.json
  records it under `outro`; `render --no-outro` skips it. `set-outro <video_id>` alone lists
  the first shots to choose from.
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
