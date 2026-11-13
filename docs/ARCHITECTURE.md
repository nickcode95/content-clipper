# Architecture: stage contracts

Pipeline: ingest -> transcribe + analyze -> select -> HUMAN APPROVAL -> render -> qa -> publish.
A weekly research step writes a trend brief that select reads.
Every stage is a Typer subcommand (`uv run content-clipper <stage>`) that reads and writes
files under data/. Stages do not import each other's internals; they exchange files.
Use pydantic models in src/content_clipper/models.py for every file contract below.

Layout: src/content_clipper/{cli.py, config.py, models.py, llm.py, media.py, sources.py,
campaign/, ingest/, transcribe/, analyze/, select/, render/, qa/, research/, publish/}. Config lives in config/*.yaml. Media is never committed.

## File contracts

Transcript: data/transcripts/<video_id>.json

    {
      "video": "data/sources/<file>",
      "video_id": "<file stem>",
      "duration_s": 0.0,
      "language": "en",
      "model": "small",
      "segments": [{"id": 0, "start": 0.0, "end": 4.2, "text": "..."}],
      "words": [{"seg": 0, "w": "Hello", "start": 0.0, "end": 0.4}]
    }

Shot analysis: data/analysis/<video_id>/shots.json, plus shot_###.jpg keyframes next to it

    {
      "video": "data/sources/<file>", "video_id": "...", "duration_s": 0.0, "analysis_fps": 30.0,
      "shots": [{"id": 0, "start": 0.0, "end": 2.0, "keyframe": "shot_000.jpg",
                 "loudness_db": -18.0, "motion": 0.02, "brightness": 0.3, "focus_x": 0.62}]
    }

Shots are contiguous (each starts where the previous ended). Boundaries are accurate to 1/analysis_fps.
focus_x is the horizontal centre (0..1) of the 9:16-wide window holding the most motion, or null
(still or evenly spread: use the centre).

Clips: data/clips/<video_id>.clips.json

    {
      "video_id": "...", "campaign": "...", "generated_at": "ISO-8601", "model": "...",
      "clips": [{
        "id": "c01", "start_s": 0.0, "end_s": 0.0, "start_shot": 0, "end_shot": 3,
        "start_seg": 0, "end_seg": 2,
        "hook_line": "...", "title": "...", "score": 0.0,
        "scores": {"<rubric key>": 0, "...": 0},
        "reason": "...", "speech_cut": false, "approved": null
      }],
      "rejected": [{"start_shot": 0, "end_shot": 0, "title": "...", "reason": "..."}]
    }

start_seg/end_seg are null for clips with no speech. speech_cut is true when a sentence had to
be cut at an edge. Rubric keys come from config/rubric.yaml.

`approved` stays null until I set it. Render only processes clips with approved == true.

Trend brief: data/research/<campaign>.trend_brief.json

    {
      "campaign": "...", "generated_at": "ISO-8601", "sources": ["data/research/<campaign>.notes.md"],
      "keywords": ["..."],
      "fan_references": [{"name": "...", "context": "...", "emotion": "nostalgia"}],
      "patterns": [{"pattern": "...", "why_it_works": "...", "example_titles": ["..."]}],
      "hook_formulas": ["..."], "avoid": ["..."]
    }

emotion is one of anticipation, speculation, excitement, intrigue, nostalgia, frustration.
Clips carry hook_options (validated suggestions) and hook_line (the chosen one). A variant clip
(`c01-2`, variant_of: "c01") has the same cut with a different hook; variants count as the same
video for posting limits.

Campaign rules: campaigns/<name>/rules.yaml (see campaigns/_template).
Outro: data/outro/<video_id>.json `{"video_id": "...", "start_s": 0.0, "end_s": 2.0}`, a span
of the source played after every rendered clip of it (set by me with `set-outro`).
Renders: data/out/<campaign>/<video_id>/<clip_id>.mp4 plus sidecar files (see Campaign-aware stages below).
Results log: data/results.csv (header already created).

## Stage notes

- Transcribe: faster-whisper, word timestamps, settings from config/transcribe.yaml.
- Analyze: one ffmpeg pass; cut detection on 160x90 grey frames at 30 fps (difference above a
  floor and 3x the local median, shots at least 0.3 s), per-shot RMS loudness, motion, brightness,
  one 640 px keyframe per shot.
- Select: video-aware. The LLM sees shot IDs, keyframes and the words spoken per shot, never
  timestamps, and returns shot ranges. Code snaps them (see the select-clips skill).
- Layouts (render): `band` puts the full-width 16:9 video on the 9:16 canvas over a blurred copy
  of itself (or a solid colour), with the hook in the band below the video and captions in the
  band above. `crop` (`render --layout crop`) fills the 9:16 frame with a crop that follows the
  action: one position per shot from focus_x, centre fallback, captions only, no hook; files get a
  `.crop` suffix (`c01.crop.mp4`) so band renders are never replaced. Hook style `tweet` draws
  the hook as a tweet-style card with a rotating persona (assets/personas/, not committed) and
  adds a `.tweet` suffix (`c01-3.tweet.mp4`). Game footage rarely has
  usable faces, so face tracking is not used.
- Render: ffmpeg re-encode (never stream copy) to the settings in config/render.yaml.
- QA: see Campaign-aware stages below. QA failures block publishing.
- Publish: out of scope until the first full run works.

<!-- campaign-aware -->
## Campaign-aware stages (supersedes earlier notes on renders and QA)

Principle: no campaign-specific logic in code. Everything campaign-specific comes from
campaigns/<name>/brief.md (verbatim) and campaigns/<name>/rules.yaml (schema and enforcement table in
docs/CAMPAIGN_RULES.md). Rules the schema cannot express go to other_rules and are reviewed by the
LLM and a human.

Flow: brief.md -> campaign add -> rules.yaml (draft) -> human review -> campaign approve
-> select -> HUMAN clip approval -> caption -> render -> qa -> HUMAN final approval -> publish.

Commands (Typer): `campaign add <name> --brief <file>`, `campaign show <name>`, `campaign approve <name>`,
`research add <campaign> --notes <file>`, `transcribe <video>`, `analyze <video>`, `select <campaign> <video_id>`,
`approve-clips <video_id> <clip_id>...`, `hooks <campaign> <video_id>`, `pick-hook <video_id> <clip_id> <n>`, `caption <campaign> <video_id>`, `set-outro <video_id> <start> <end>`, `render <campaign> <video_id>`,
`qa <campaign> <video_id>`, `approve <campaign> <video_id> <clip_id>`, `checklist <campaign>`.

Output layout: data/out/<campaign>/<video_id>/ with, per clip_id:
`<clip_id>.mp4`, `.ass`, `.log`, `.render.json`, `<clip_id>.<platform>.caption.txt`, `.caption.json`, `.qa.json`.

Source registry: data/sources/registry.csv, header `video_id,origin,campaign,downloaded_at,notes`.
I add a row by hand when I download authorised footage.

render.json:

    {"clip_id": "c01", "campaign": "...", "source_video": "...", "music": null,
     "ai_voice": false, "hook_text": null, "on_screen_lines": ["..."], "duration_s": 0.0,
     "outro": null}

`outro` is null or {"start_s", "end_s", "duration_s", "audio_until_s", "clip_tail_s"}; duration_s includes it.

caption.json:

    {"clip_id": "c01", "platform": "tiktok", "caption_text": "...", "hashtags": ["#..."],
     "on_screen_text": null, "claims": [{"claim": "...", "source_quote": "..."}],
     "validation": [{"id": "...", "status": "pass"}], "attempts": 1}

qa.json:

    {"clip_id": "c01", "campaign": "...", "checks": [{"id": "length", "status": "pass", "detail": "..."}],
     "passed": true, "human_approved": null}

Tests use docs/examples/ as the fixture campaign. No test calls a paid API; LLM calls are replaced by
recorded fake responses, and one slow test is gated by RUN_SLOW=1.
