# Handoff for Claude Code

## Goal
Build a local pipeline on this Ubuntu laptop (Surface Laptop Studio, CPU-only for now) that turns
campaign-authorised long videos into captioned 9:16 short clips for Whop Content Rewards
campaigns, posted mainly to Instagram and TikTok. The earlier Sora-based repo is NOT used here.

## Read first, in this order
1. CLAUDE.md  2. docs/ARCHITECTURE.md  3. docs/CAMPAIGN_RULES.md  4. docs/BUILD_PLAN.md
5. the skills in .claude/skills/

## Key design rule
Campaign briefs differ and change. No campaign-specific logic in code. Briefs become
campaigns/<name>/rules.yaml (data), and every stage reads that file. If a rule does not fit the
schema, put it in other_rules and tell me; do not special-case it.

## Environment (already set up)
Python 3.12 via uv, dependencies already added with `uv add`. ffmpeg 6.1 from apt with the
subtitles and drawtext filters. No NVIDIA driver: CPU int8. Secrets are in .env, which you must not
read. If you need a new dependency, say why, then use `uv add`. Whisper audio decoding goes through
system ffmpeg (PyAV 19 workaround from Session 1).

## Each session
1. Verify the environment (imports check, `ffmpeg -version`, `uv run pytest --version`).
2. Open docs/BUILD_PLAN.md and work on the FIRST UNTICKED session only. Do not start the next one.
3. If something the session needs from me is missing (a video, a brief, an API key), stop and ask
   before writing code that depends on it.

## Working rules
- Small, tested steps. Run `uv run ruff check .` and `uv run pytest` before finishing.
- Tests must not download models or call paid APIs. Gate slow or paid tests behind RUN_SLOW=1.
- Never commit media, data/ or .env. Never push.
- Ask me when a campaign rule or licence is unclear; never guess.
- Finish with the session-wrap skill and report: what was built, how to run it, test results,
  measurements, and what I need for the next session.
