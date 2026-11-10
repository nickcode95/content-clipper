#!/usr/bin/env bash
# Creates Claude Code assets, docs and configs for the content-clipper repo.
# Run from the repo root:   bash setup_claude_assets.sh
# Safe to re-run: it overwrites its own files and appends to CLAUDE.md only once.
set -euo pipefail

if [ ! -f pyproject.toml ] || [ ! -f .python-version ]; then
  echo "Run this from the content-clipper repo root (pyproject.toml not found)." >&2
  exit 1
fi

mkdir -p .claude/skills/{select-clips,render-clip,campaign-rules,music-licensing,research-brief,session-wrap} \
         docs config campaigns/_template data/research

# ---------------------------------------------------------------- settings
cat > .claude/settings.json <<'EOF'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(uv run *)",
      "Bash(uv add *)",
      "Bash(uv remove *)",
      "Bash(uv sync)",
      "Bash(uv lock)",
      "Bash(ffmpeg *)",
      "Bash(ffprobe *)",
      "Bash(git status)",
      "Bash(git diff *)",
      "Bash(git add *)",
      "Bash(git commit *)",
      "Bash(git log *)"
    ],
    "deny": [
      "Read(./.env)",
      "Bash(sudo *)",
      "Bash(rm -rf *)",
      "Bash(git push *)",
      "Bash(pip *)",
      "Bash(pip3 *)",
      "Bash(curl *)",
      "Bash(wget *)"
    ]
  }
}
EOF

grep -qxF '.claude/settings.local.json' .gitignore 2>/dev/null || echo '.claude/settings.local.json' >> .gitignore

# ---------------------------------------------------------------- configs
cat > config/transcribe.yaml <<'EOF'
# CPU-first. Switch device to cuda only after the GPU driver task is done.
model_size: small
device: cpu
compute_type: int8
beam_size: 5
vad_filter: true
language: en
EOF

cat > config/models.yaml <<'EOF'
# Verify these model IDs against the current Anthropic docs before the first run.
selection: claude-sonnet-5-5
light: claude-haiku-5-5
EOF

cat > config/render.yaml <<'EOF'
width: 1080
height: 1920
fps: 30
video: {codec: libx264, preset: veryfast, crf: 20, pix_fmt: yuv420p}
audio: {codec: aac, bitrate: 160k, loudnorm: "I=-14:TP=-1.5:LRA=11"}
captions:
  font: "DejaVu Sans"
  bold: true
  size: 72
  highlight_color: "#FFD400"
  max_words_per_line: 4
  safe_top_pct: 10
  safe_bottom_pct: 20
hook: {seconds: 3, size: 84}
music: {gain_db: -18, duck: true, fade_s: 1.0}
EOF

cat > campaigns/_template/rules.yaml <<'EOF'
# Copy this folder to campaigns/<campaign-name>/ and fill it in from the campaign page.
# Use "unknown" for anything the campaign page does not state. Never guess.
name: ""
platforms: [tiktok, instagram]
min_length_s: 15
max_length_s: 90
required_mentions: []      # handles, hashtags, brand mentions
prohibited: []             # topics, wording, formats
music_allowed: unknown
ai_generated_content_allowed: unknown
source_notes: ""           # where the authorized footage comes from
rate_per_1k_views: 0.0
other_rules: ""
EOF

# ---------------------------------------------------------------- docs
cat > docs/ARCHITECTURE.md <<'EOF'
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
EOF

cat > docs/BUILD_PLAN.md <<'EOF'
# Build plan (fixed sessions, one scope per session)

Rule: do ONLY the current session's scope. Tick the box and add a short note when done.

- [x] S0 Scaffold (done by hand)
- [ ] S1 Wed 11 Nov 19:30. Transcribe.
  `uv run content-clipper transcribe <video>` writes the transcript contract.
  Accept: word timestamps are monotonic; prints duration, wall time and real-time factor;
  unit tests use fake segments (no model download); one slow integration test gated by RUN_SLOW=1.
- [ ] S2 Fri 13 Nov 19:30. Select.
  LLM scoring per the select-clips skill, snapping to word timestamps, clips.json written.
  Accept: unit tests for the snapper on a synthetic transcript, including a rejected mismatch.
- [ ] S3 Sat 14 Nov 10:00. Cut + captions.
  pysubs2 word-highlight ASS, ffmpeg cut and burn for one clip.
  Accept: output duration within 0.1 s of end_s - start_s; captions visible.
- [ ] S4 Mon 16 Nov 19:30. 9:16 face-tracked reframe with centre-crop fallback.
- [ ] S5 Wed 18 Nov 19:30. Hook overlay and QA script against campaign rules.
- [ ] S6 Fri 20 Nov 19:30. Research step (YouTube Data API) writing the trend brief.
- [ ] S7 Sat 21 Nov 10:00. First full run on a real campaign video, no music.
- [ ] S8 Mon 23 Nov 19:30. Music mixing from config/music_manifest.yaml.
- [ ] Later, Sat 28 Nov 10:00. GPU driver and CUDA setup (separate task, read-only checks first).
- [ ] Later. Publish and scheduling.
EOF

cat > docs/HANDOFF.md <<'EOF'
# Handoff for Claude Code

## Goal
Build a local pipeline on this Ubuntu laptop (Surface Laptop Studio, CPU-only for now) that turns
campaign-authorized long videos into captioned 9:16 short clips for Whop Content Rewards
campaigns, posted mainly to Instagram and TikTok. The earlier Sora-based repo is NOT used here.

## Read first, in this order
1. CLAUDE.md  2. docs/ARCHITECTURE.md  3. docs/BUILD_PLAN.md  4. the skills in .claude/skills/

## Environment (already set up by hand)
Python 3.12 via uv. Dependencies are already added with `uv add`: faster-whisper,
opencv-python-headless, numpy, pysubs2, anthropic, google-genai, google-api-python-client,
python-dotenv, pydantic, pyyaml, typer, tenacity, httpx; dev: pytest, ruff. ffmpeg 6.1 from apt
with the subtitles and drawtext filters. No NVIDIA driver: use CPU int8. Secrets are in .env,
which you must not read. If you need a new dependency, say why, then use `uv add`.

## First actions
1. Verify the environment: run the imports check, `ffmpeg -version`, `nproc`, `uv run pytest --version`.
2. Check data/sources/ for a test video. If none, stop and ask me for a short authorized
   video with speech (2 to 5 minutes).
3. Work on Session 1 from docs/BUILD_PLAN.md ONLY. Do not start Session 2.

## Working rules
- Small, tested steps. Run `uv run ruff check .` and `uv run pytest` before finishing.
- Never commit media, data/ or .env. Never push.
- Ask me when something in a campaign rule or licence is unclear; never guess.
- When Session 1 is done, run the session-wrap skill and report: what was built, how to run it,
  test results, the measured transcription speed, and what I need for Session 2.
EOF

# ---------------------------------------------------------------- skills
cat > .claude/skills/select-clips/SKILL.md <<'EOF'
---
name: select-clips
description: Use when building or running the clip selection stage. Scores transcript windows with an LLM against the rubric, campaign rules and trend brief, snaps results to exact Whisper word timestamps, and writes clips.json.
---
# Selecting clips

Inputs: transcript JSON, config/rubric.yaml, campaigns/<name>/rules.yaml, optional trend brief.

Rules:
1. Send the transcript as lines like `[S012] text` using segment IDs. Never ask the model for timestamps.
2. Ask for JSON only. Each candidate has: start_seg, end_seg, first_words (first 6 words),
   last_words (last 6 words), hook_line, title, scores (one 0-10 value per rubric key), reason.
3. Snap: find first_words and last_words inside the named segments using normalised tokens
   (lowercase, punctuation stripped). If either is not found, discard the candidate and log it.
   Never repair a mismatch by guessing.
4. Take start_s and end_s from the word timestamps. Pad 0.15 s before and 0.25 s after.
   Enforce the campaign min and max length. Remove overlaps, keeping the higher score.
5. score = sum(weight * value) / sum(weights), weights from config/rubric.yaml.
   Sort descending and write data/clips/<video_id>.clips.json with approved: null.
6. hook_line must be new wording based on the clip. Never copy trend-brief example titles verbatim.
7. Use the model in config/models.yaml, low temperature, retries via tenacity, and validate the
   response with pydantic before using it.

System prompt to start from:

    You are an editor choosing short-form clips from a transcript. Pick moments that work as
    standalone 30-90 second videos. Score each on the given rubric keys from 0 to 10. Obey the
    campaign rules exactly. Return only JSON matching the schema. Do not invent text that is not
    in the transcript. The hook_line is a short original line that could open the clip.
EOF

cat > .claude/skills/render-clip/SKILL.md <<'EOF'
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
EOF

cat > .claude/skills/campaign-rules/SKILL.md <<'EOF'
---
name: campaign-rules
description: Use when turning a pasted campaign brief into campaigns/<name>/rules.yaml, or when checking clips against campaign rules in QA or selection.
---
# Campaign rules

1. Copy campaigns/_template to campaigns/<name>/ and fill rules.yaml from the text I paste.
2. Record only what the campaign page states. Use `unknown` for anything missing and list the
   unknowns back to me as questions. Never invent a rule.
3. The select stage reads min_length_s, max_length_s, required_mentions and prohibited.
   The QA stage re-checks them on the rendered file (length via ffprobe, mentions in the caption text).
4. Flag any rule about AI-generated content, music, or source footage as high priority.
5. Clips come only from footage the campaign authorizes. If source_notes is empty, ask me where
   the footage is allowed to come from before rendering anything.
EOF

cat > .claude/skills/music-licensing/SKILL.md <<'EOF'
---
name: music-licensing
description: Use when adding or choosing background music for a clip. Enforces the music manifest, licence proof and per-platform eligibility.
---
# Music licensing

Source of truth: config/music_manifest.yaml. Every entry needs file, source, license_url,
platforms, attribution (if required) and moods.

Eligibility function: a track is eligible only if it is in the manifest, has a license_url, its
platforms list includes the target platform, and the campaign rules do not forbid music.
Choose among eligible tracks by mood. If none qualify, use no music. When unsure, no music.

Reminders (verify each licence yourself):
- A licence for one platform does not cover the others. YouTube Audio Library tracks are not
  cleared for TikTok or Instagram.
- TikTok expects commercial accounts to use its Commercial Music Library.
- Free archives can include tracks uploaded by people who do not own the rights. Prefer clear licences.
- Footage may already contain copyrighted music. Flag it for review instead of ignoring it.
- If a track requires attribution, append the attribution text to the caption and log it.
- Keep licence proof (page URL or saved text) in licenses/ for every track.
EOF

cat > .claude/skills/research-brief/SKILL.md <<'EOF'
---
name: research-brief
description: Use when building the weekly research step that writes a trend brief for a campaign using official APIs only.
---
# Research brief

Output: data/research/<campaign>.trend_brief.json (contract in docs/ARCHITECTURE.md).

1. Read the campaign rules and the source transcript. Ask the light model in config/models.yaml
   for 3-5 topic keywords.
2. For each keyword, call the YouTube Data API search endpoint, ordered by view count, recent
   uploads, and read titles and public statistics. Respect quota: cache results in data/research/
   and cap searches per run.
3. Summarise the hook patterns and angles that work, in new wording. Record patterns, never copy
   other people's scripts, footage or distinctive phrasing.
4. Hard limits: no scraping or unattended browser crawling of Instagram, TikTok or any other site.
   No Reddit unless I confirm API approval. Content I paste in manually is fine.
5. Read YOUTUBE_API_KEY from the environment via python-dotenv. Never print it.
EOF

cat > .claude/skills/session-wrap/SKILL.md <<'EOF'
---
name: session-wrap
description: Wrap up a build session. Runs lint and tests, updates docs/BUILD_PLAN.md, commits, and reports what is next.
disable-model-invocation: true
---
# Session wrap

1. Run `uv run ruff check .` and `uv run pytest`. Fix failures, or report why one cannot be fixed.
2. Tick the finished session in docs/BUILD_PLAN.md and add a one-line note (what works, what was measured).
3. Run `git status`. Confirm that .env, data/ and media files are not staged.
4. `git add -A` then commit with the message "Session N: <what was built>". Do not push.
5. Report in at most 6 lines: built, how to run it, test result, measurements, blockers,
   and what I must prepare for the next session.
EOF

# ---------------------------------------------------------------- CLAUDE.md addendum (once)
if ! grep -q 'handoff-assets' CLAUDE.md 2>/dev/null; then
cat >> CLAUDE.md <<'EOF'

<!-- handoff-assets -->
# Project docs and conventions
- Start with docs/HANDOFF.md. Contracts are in docs/ARCHITECTURE.md; the plan is in docs/BUILD_PLAN.md.
- Do only the current session's scope. Tests must not download models or call paid APIs;
  gate slow or paid tests behind RUN_SLOW=1.
- CLI is Typer: replace the generated `main` in src/content_clipper/__init__.py or add cli.py and
  point [project.scripts] at it.
- Skills in .claude/skills/ describe each stage. Follow them. Use /session-wrap to finish a session.
- Never read .env. Never commit media, data/ or secrets. Never push.
EOF
fi

echo "Done. Files created:"
find .claude docs config campaigns/_template -type f | sort
echo
echo "Next: git add -A && git commit -m 'Add Claude Code assets and docs'"
