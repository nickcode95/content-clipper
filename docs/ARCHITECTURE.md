# Architecture: stage contracts

Pipeline: ingest -> transcribe -> select -> HUMAN APPROVAL -> render -> qa -> publish.
A weekly research step writes a trend brief that select reads.
Every stage is a Typer subcommand (`uv run content-clipper <stage>`) that reads and writes
files under data/. Stages do not import each other's internals; they exchange files.
Use pydantic models in src/content_clipper/models.py for every file contract below.

Layout: src/content_clipper/{cli.py, config.py, models.py, ingest/, transcribe/, select/,
render/, qa/, research/, publish/}. Config lives in config/*.yaml. Media is never committed.

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

Clips: data/clips/<video_id>.clips.json

    {
      "video_id": "...", "campaign": "...", "generated_at": "ISO-8601",
      "clips": [{
        "id": "c01", "start_s": 0.0, "end_s": 0.0, "start_seg": 0, "end_seg": 0,
        "hook_line": "...", "title": "...", "score": 0.0,
        "scores": {"hook_strength": 0, "self_contained": 0, "payoff": 0,
                   "emotional_intensity": 0, "density": 0, "campaign_fit": 0},
        "reason": "...", "approved": null
      }]
    }

`approved` stays null until I set it. Render only processes clips with approved == true.

Trend brief: data/research/<campaign>.trend_brief.json

    {
      "campaign": "...", "generated_at": "ISO-8601", "keywords": ["..."],
      "patterns": [{"pattern": "...", "why_it_works": "...", "example_titles": ["..."]}],
      "hook_formulas": ["..."], "avoid": ["..."]
    }

Campaign rules: campaigns/<name>/rules.yaml (see campaigns/_template).
Renders: data/out/<campaign>/<video_id>/<clip_id>.mp4 plus sidecar files (see Campaign-aware stages below).
Results log: data/results.csv (header already created).

## Stage notes

- Transcribe: faster-whisper, word timestamps, settings from config/transcribe.yaml.
- Select: the LLM sees segment IDs, never timestamps (see the select-clips skill).
- Reframe: OpenCV Haar cascade (cv2.data.haarcascades), detect every ~5th frame, smooth the
  crop centre, fall back to a centre crop when no face is found. MediaPipe is a later upgrade.
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
`select <campaign> <video_id>`, `caption <campaign> <video_id>`, `render <campaign> <video_id>`,
`qa <campaign> <video_id>`, `approve <campaign> <video_id> <clip_id>`, `checklist <campaign>`.

Output layout: data/out/<campaign>/<video_id>/ with, per clip_id:
`<clip_id>.mp4`, `.ass`, `.log`, `.render.json`, `<clip_id>.<platform>.caption.txt`, `.caption.json`, `.qa.json`.

Source registry: data/sources/registry.csv, header `video_id,origin,campaign,downloaded_at,notes`.
I add a row by hand when I download authorised footage.

render.json:

    {"clip_id": "c01", "campaign": "...", "source_video": "...", "music": null,
     "ai_voice": false, "hook_text": null, "on_screen_lines": ["..."], "duration_s": 0.0}

caption.json:

    {"clip_id": "c01", "platform": "tiktok", "caption_text": "...", "hashtags": ["#..."],
     "on_screen_text": null, "claims": [{"claim": "...", "source_quote": "..."}],
     "validation": [{"id": "...", "status": "pass"}], "attempts": 1}

qa.json:

    {"clip_id": "c01", "campaign": "...", "checks": [{"id": "length", "status": "pass", "detail": "..."}],
     "passed": true, "human_approved": null}

Tests use docs/examples/ as the fixture campaign. No test calls a paid API; LLM calls are replaced by
recorded fake responses, and one slow test is gated by RUN_SLOW=1.
