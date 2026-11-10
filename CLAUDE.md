# Rules for this repo
- Dependencies: ONLY use `uv add <pkg>` / `uv remove <pkg>`. Never hand-edit versions in pyproject.toml, never use pip, never create requirements.txt.
- Run code with `uv run ...`. Python is pinned in .python-version.
- No Instagram scraping or unattended browser-agent crawling. Research uses official APIs or content I supply manually.
- Clips come only from campaign-authorized source footage. Copy patterns (hooks, pacing), never others' footage or wording.
- LLMs return transcript TEXT to keep; match back to Whisper word timestamps for cuts. Never trust LLM-generated timestamps.
- Transcription runs on CPU (device="cpu", compute_type="int8") for now; GPU is a later, separate task.
- Music: only tracks listed in config/music_manifest.yaml with a license_url and a platform match. Never use unlisted or platform-trending audio.
- Secrets live in .env, never committed.

<!-- handoff-assets -->
# Project docs and conventions
- Start with docs/HANDOFF.md. Contracts are in docs/ARCHITECTURE.md; the plan is in docs/BUILD_PLAN.md.
- Do only the current session's scope. Tests must not download models or call paid APIs;
  gate slow or paid tests behind RUN_SLOW=1.
- CLI is Typer: replace the generated `main` in src/content_clipper/__init__.py or add cli.py and
  point [project.scripts] at it.
- Skills in .claude/skills/ describe each stage. Follow them. Use /session-wrap to finish a session.
- Never read .env. Never commit media, data/ or secrets. Never push.
