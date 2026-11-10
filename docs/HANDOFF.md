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
