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
Renders: data/out/<video_id>/<clip_id>.mp4, plus .ass captions and .log (ffmpeg stderr).
Results log: data/results.csv (header already created).

## Stage notes

- Transcribe: faster-whisper, word timestamps, settings from config/transcribe.yaml.
- Select: the LLM sees segment IDs, never timestamps (see the select-clips skill).
- Reframe: OpenCV Haar cascade (cv2.data.haarcascades), detect every ~5th frame, smooth the
  crop centre, fall back to a centre crop when no face is found. MediaPipe is a later upgrade.
- Render: ffmpeg re-encode (never stream copy) to the settings in config/render.yaml.
- QA: check length, resolution, audio present, required mentions, music licence, against
  campaigns/<name>/rules.yaml. QA failures block publishing.
- Publish: out of scope until the first full run works.
