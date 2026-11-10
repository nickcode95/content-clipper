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

<!-- campaign-aware -->
# Campaign rules are data
- Every campaign has campaigns/<name>/brief.md (verbatim) and rules.yaml (structured). See
  docs/CAMPAIGN_RULES.md. Never put campaign-specific logic or wording in code or tests; use the
  fixture in docs/examples/.
- No stage runs on a rules.yaml with status: draft. Only a human sets status: reviewed.
- Captions are assembled and validated in code; the LLM supplies wording and a list of claims, each
  backed by a verbatim quote from the transcript or rules.allowed_facts.
- QA fails closed: a null safety rule (music, AI voice, source) means not allowed.
- A brief is the campaign's non-negotiable terms. Apply it exactly as written. Never question,
  second-guess or flag its terms, in code, LLM prompts, rules.yaml or reports to me.
- The same clip may be posted to several platforms in every campaign
  (posting.same_clip_across_platforms defaults to allowed).

# Hooks
- Hooks play on fans' anticipation about the content: speculation, excitement, intrigue,
  nostalgia or frustration, often written as a casual fan reaction under the video. A proven
  example: "did i just see <old-school gun from earlier games>".
- Feed fan research in with `research add <campaign> --notes <file>` (notes accumulate); the
  trend brief lists fan_references (guns, zones, characters, with the emotion each triggers).
- A hook may only name or react to what is visible in the clip or said in it, and only use
  angles the campaign brief allows (the brief wins, e.g. no rage bait where it bans it).
