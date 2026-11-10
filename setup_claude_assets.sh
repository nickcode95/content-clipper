#!/usr/bin/env bash
# Upgrade for the content-clipper repo: makes the pipeline campaign-brief-aware.
# Run from the repo root:   bash upgrade_campaign_aware.sh
# Safe to re-run. Overwrites the files it owns; patches ARCHITECTURE.md and CLAUDE.md once.
# Run it only while Claude Code is NOT mid-session, then commit the result.
set -euo pipefail

if [ ! -f pyproject.toml ] || [ ! -d .claude/skills/campaign-rules ] || [ ! -f docs/BUILD_PLAN.md ]; then
  echo "Run this from the content-clipper repo root, after setup_claude_assets.sh." >&2
  exit 1
fi

mkdir -p .claude/skills/{caption-generation,compliance-qa} \
         campaigns/_template docs/examples data/sources

# ---------------------------------------------------------------- source registry (data/ is gitignored)
[ -f data/sources/registry.csv ] || echo 'video_id,origin,campaign,downloaded_at,notes' > data/sources/registry.csv

# ---------------------------------------------------------------- rules template
cat > campaigns/_template/rules.yaml <<'EOF'
# campaigns/<name>/rules.yaml
# Produced from brief.md by `content-clipper campaign add`, then reviewed by a human.
# null means "the brief does not say". Never guess a value.
# For safety-critical flags (audio.music_allowed, audio.ai_voice_allowed, source.policy)
# null is treated as NOT ALLOWED / NOT VERIFIED.
schema_version: 1
name: ""
status: draft              # draft | reviewed. Render, caption and publish refuse to run on draft.
brief_file: brief.md       # the pasted brief, verbatim. Source of truth.
brief_sha256: ""           # written on review; QA warns if brief.md changes afterwards
valid_until: null          # YYYY-MM-DD. Warning only, never blocks.
platforms: []              # tiktok | instagram | youtube
rate_per_1k_views: null

source:
  policy: unknown          # official_only | own_footage_only | any | unknown
  location_notes: ""       # where the authorised footage lives, in the brief's words
  require_registry: true   # each source video must be listed in data/sources/registry.csv

video:
  min_length_s: null
  max_length_s: null
  language: null           # e.g. en
  quality_notes: []

audio:
  music_allowed: null      # true | false | null (null = treated as false)
  ai_voice_allowed: null   # null = treated as false
  original_audio_only: false

caption:
  required_mentions: []    # [{text: "@brand", platforms: [tiktok, instagram]}]
  disclosure:
    required: false
    accepted_tags: []      # e.g. ["#Ad", "#Sponsored"]
    must_be_first_hashtag: false
    own_line: false          # the disclosure tag is alone on its line
    after_caption_text: false  # the disclosure line comes right after the caption text
  max_additional_hashtags: null  # hashtags allowed beyond the disclosure tag
  hashtags_must_relate: false
  max_length_chars: null
  required_lines: []       # [{id, where: caption|on_screen|either, verbatim: true, options: [...], expires: null}]
  forbidden_phrases: []
  example_captions: []     # tone guide only; write new wording unless style_notes allow reuse
  style_notes: []          # e.g. "keep it genuine", "do not copy other clippers"

allowed_facts: []          # facts from the brief that captions may state (a caption may not state any other fact)

on_screen_text:
  policy: optional         # optional | required | forbidden
  must_match_screen: false
  notes: []

hook_style:
  mode: default            # default | descriptive | none  (descriptive = accurate, no exaggeration)
  notes: []

prohibited: []             # content the campaign bans, each as a short sentence

posting:
  max_posts_per_clip: null
  same_clip_on_multiple_accounts: null   # allowed | forbidden | null
  same_clip_across_platforms: null       # allowed | forbidden | null
  min_days_live: null
  dedicated_page_required: null
  account_notes: []

checklist: []              # things code cannot control (engagement rate, audience mix, likes visible...)
other_rules: []            # anything that fits no field above. Reviewed by the LLM and by a human.
unknowns: []               # questions to ask the campaign team
evidence: {}               # dotted.field.path -> verbatim quote from brief.md that supports it
EOF

cat > campaigns/_template/brief.md <<'EOF'
Paste the campaign brief here, exactly as shown on the campaign page. Do not edit or tidy it.
EOF

# ---------------------------------------------------------------- docs
cat > docs/CAMPAIGN_RULES.md <<'EOF'
# Campaign rules: schema and who enforces what

Briefs differ per campaign and change over time, so NO campaign-specific logic lives in code.
A campaign is two files: campaigns/<name>/brief.md (verbatim paste) and campaigns/<name>/rules.yaml
(structured). Template: campaigns/_template/rules.yaml. A filled, fictional example is in
docs/examples/.

## Life cycle
1. `content-clipper campaign add <name> --brief <file>` stores brief.md and asks the LLM to draft
   rules.yaml. Each value the LLM fills must come with a verbatim quote from the brief (`evidence`).
   Code checks the quote exists in brief.md; if not, the value is reset to null and a question is
   added to `unknowns`.
2. A human reads the draft next to brief.md, answers the unknowns, edits, then runs
   `content-clipper campaign approve <name>` which sets status: reviewed and stores brief_sha256.
3. select, caption, render and qa refuse to run while status is draft.
4. If brief.md changes later, the hash no longer matches: QA warns and status goes back to draft.
5. Rules that fit no field go to `other_rules` (never dropped). Do not hardcode them. If the same
   kind of rule keeps appearing, propose a schema change instead.

## Who reads which field

| Field | Select | Caption | Render | QA (code) | Human / LLM review |
|---|---|---|---|---|---|
| source.* | skips unregistered sources | | | blocks if source not in registry.csv | |
| video.min/max_length_s | window limits | | | ffprobe length | |
| video.language | | writes in it | | transcript language | |
| audio.* | | | music on only if allowed AND eligible | render.json shows no music when disallowed | |
| caption.required_mentions | | must include | | string check per platform | |
| caption.disclosure | | builds the tag line | | position and first-hashtag checks | |
| caption.required_lines | | includes verbatim | on-screen lines drawn | string check; expiry warns | |
| caption.max_*, forbidden_phrases | | respects | | checks | |
| allowed_facts | | only source of non-transcript facts | | | LLM checks claims |
| on_screen_text, hook_style | scoring emphasis | writes overlay text | draws overlay | policy check | LLM checks match to screen |
| prohibited, other_rules | rejects violating clips | avoids | | | LLM review, then human |
| posting.* | | | | counts rows in results.csv | |
| checklist | | | | listed by `checklist` command | human |
| valid_until | | | | warning only | |

null in a safety field (music, AI voice, source) is treated as not allowed.
EOF

cat > docs/examples/brief.example.md <<'EOF'
Acme Studio - Launch Trailer Clipping (EXAMPLE, FICTIONAL)
TikTok, Instagram Reels

WHAT TO DO
Clip the launch trailer from the official content folder and post it.
Use only footage from the content folder. Outside footage is rejected.

CAPTION
Tag @acmestudio. Add #Ad as the first hashtag on its own line after the caption text.
Up to 2 more hashtags that relate to the content.
Keep it genuine. No made-up facts, stats or dates.
Every post must say "Out now on all platforms".

ON-SCREEN TEXT
Optional. If used it must match what is on screen.

EDITING
No added music. No AI voiceovers.

REQUIREMENTS
English only. Minimum 8 seconds. Posts must stay live for 14 days.
Minimum 1% engagement rate. Likes must be visible.

NOT ALLOWED
Exaggerated hooks, rage bait, reposting another creator's video, posting the same clip more than 3 times.
EOF

cat > docs/examples/rules.example.yaml <<'EOF'
# Fictional example showing how a brief maps onto the schema. Also usable as a test fixture.
schema_version: 1
name: acme-launch-trailer
status: reviewed
brief_file: brief.md
brief_sha256: ""
valid_until: null
platforms: [tiktok, instagram]
rate_per_1k_views: null

source:
  policy: official_only
  location_notes: "official content folder"
  require_registry: true

video:
  min_length_s: 8
  max_length_s: null
  language: en
  quality_notes: []

audio:
  music_allowed: false
  ai_voice_allowed: false
  original_audio_only: true

caption:
  required_mentions:
    - {text: "@acmestudio", platforms: [tiktok, instagram]}
  disclosure:
    required: true
    accepted_tags: ["#Ad"]
    must_be_first_hashtag: true
    own_line: true
    after_caption_text: true
  max_additional_hashtags: 2
  hashtags_must_relate: true
  max_length_chars: null
  required_lines:
    - {id: availability, where: caption, verbatim: true, options: ["Out now on all platforms"], expires: null}
  forbidden_phrases: []
  example_captions: []
  style_notes: ["keep it genuine", "no made-up facts, stats or dates"]

allowed_facts: ["Out now on all platforms"]

on_screen_text:
  policy: optional
  must_match_screen: true
  notes: []

hook_style:
  mode: descriptive
  notes: ["no exaggerated hooks, rage bait"]

prohibited:
  - "exaggerated hooks, rage bait"
  - "reposting another creator's video"

posting:
  max_posts_per_clip: 3
  same_clip_on_multiple_accounts: null
  same_clip_across_platforms: null
  min_days_live: 14
  dedicated_page_required: null
  account_notes: []

checklist:
  - "Minimum 1% engagement rate"
  - "Likes must be visible"
  - "Posts stay live for 14 days"
other_rules: []
unknowns:
  - "Does posting the same clip to TikTok and Instagram count as the same clip on multiple accounts?"
evidence:
  source.policy: "Use only footage from the content folder."
  audio.music_allowed: "No added music."
  caption.disclosure.accepted_tags: "#Ad as the first hashtag"
EOF

# ---------------------------------------------------------------- skills (new and rewritten)
cat > .claude/skills/campaign-rules/SKILL.md <<'EOF'
---
name: campaign-rules
description: Use when ingesting a pasted campaign brief into campaigns/<name>/rules.yaml, reviewing a draft, or reading rules in any stage. Briefs differ per campaign, so rules are data, never code.
---
# Campaign rules

Schema: campaigns/_template/rules.yaml. Semantics and who enforces what: docs/CAMPAIGN_RULES.md.
Example: docs/examples/. Never hardcode anything from a specific campaign into code or tests;
use the example fixture.

## Ingesting a brief
1. Save the pasted text unchanged as campaigns/<name>/brief.md.
2. Ask the LLM (config/models.yaml, selection model, low temperature) for JSON matching the
   schema. For every non-null value it must also return a verbatim quote from the brief.
3. Verify each quote exists in brief.md (case-insensitive, whitespace-collapsed). If a quote is
   missing, set that field to null and add a question to `unknowns`. Same trick as clip
   snapping: never trust the model's claim without a check.
4. Rules that fit no field go in `other_rules` as short sentences. Never drop a rule.
5. Safety fields (music, AI voice, source) stay null unless the brief states them.
6. Write rules.yaml with status: draft. Print the unknowns and the other_rules for the human.
7. Only `campaign approve` (a human action) sets status: reviewed and stores brief_sha256.

## Reading rules
- Load with a pydantic model, validate, and refuse to run any later stage on status: draft.
- If brief.md no longer matches brief_sha256, warn loudly and treat the status as draft.
- valid_until and required_lines[].expires only ever produce warnings.
- If a stage needs a rule the schema cannot express, add it to other_rules and flag it to me.
  Propose a schema change in your report; do not special-case a campaign.
EOF

cat > .claude/skills/caption-generation/SKILL.md <<'EOF'
---
name: caption-generation
description: Use when building or running the caption stage. Writes a post caption (and optional on-screen line) per clip and platform from the campaign rules, grounded in the clip transcript, then validates it deterministically.
---
# Caption generation

Inputs: data/clips/<video_id>.clips.json (approved clips), transcript, campaigns/<name>/rules.yaml
(status reviewed), config/models.yaml. Output per clip and platform:
data/out/<campaign>/<video_id>/<clip_id>.<platform>.caption.txt (exact text to paste) and
<clip_id>.caption.json (see docs/ARCHITECTURE.md).

## Generate
1. Give the model ONLY: the clip's transcript text, the rules fields below, and the platform.
   Rules fields: caption.*, allowed_facts, hook_style, on_screen_text, prohibited, other_rules,
   video.language.
2. Ask for JSON: {caption_text, hashtags, on_screen_text, claims: [{claim, source_quote}]}.
   Every factual statement in the caption or on-screen text must be listed in `claims` with a
   source_quote that appears verbatim in the clip transcript or in allowed_facts.
3. Code verifies each source_quote exists. A claim with no verifiable source rejects the caption.
   If the clip makes no checkable claim, the caption must stay descriptive and generic.
4. Never invent stats, dates, names or events. Match the campaign's tone (style_notes). Examples
   in caption.example_captions guide tone only; write new wording unless style_notes allow reuse.

## Assemble in code, not in the LLM
The code builds the final text so the layout is exact: caption text line(s), then the
disclosure line when required (accepted tag, on its own line if own_line, directly after the text
if after_caption_text, before any other hashtag if must_be_first_hashtag), then the other
hashtags (at most max_additional_hashtags), then required mentions where the rules need them. For each required_lines entry
whose `where` includes caption, insert one of its options verbatim. If `where` is on_screen, pass
the line to the render stage as an overlay line. If `either`, prefer the caption.

## Validate (deterministic), retry up to 2 times
Required mentions for this platform, disclosure rules, hashtag count, max_length_chars,
forbidden_phrases, required_lines present verbatim, language, and claims grounded. On a failure,
feed the failed check names back to the model once or twice, then stop and report. Never patch
text silently.

## Hook and on-screen text
- hook_style.mode default: a short punchy hook is fine.
- descriptive: plain, accurate on-screen text drawn from what the clip shows or says.
- none: no hook overlay; only required on-screen lines.
- If on_screen_text.must_match_screen is true, the text must be a phrase or close paraphrase of
  words spoken or shown in the clip; the LLM review in compliance-qa double-checks it.
- on_screen_text.policy forbidden means no overlay at all.

## Platform variants
Generate one caption per platform listed in rules.platforms. Mentions and tags can differ per
platform (required_mentions[].platforms).
EOF

cat > .claude/skills/compliance-qa/SKILL.md <<'EOF'
---
name: compliance-qa
description: Use when building or running the QA stage that checks rendered clips and captions against a campaign's rules before the human approval gate.
---
# Compliance QA

Input: campaigns/<name>/rules.yaml, data/out/<campaign>/<video_id>/ files, data/sources/registry.csv,
data/results.csv. Output: <clip_id>.qa.json. Exit code non-zero when any check fails.
Each check returns {id, status: pass|fail|warn|manual, detail}.

## Deterministic checks (code, driven only by rules.yaml)
- rules_reviewed: status is reviewed and brief_sha256 matches brief.md (fail otherwise).
- source_registered: when source.require_registry, the source video is in registry.csv for this
  campaign. Policy official_only with an unregistered source fails. Policy unknown fails.
- length: ffprobe duration within video.min_length_s and max_length_s (skip nulls).
- format: 1080x1920 (from config/render.yaml), an audio stream exists.
- language: transcript language equals video.language.
- music: audio.music_allowed not true => render.json must show music null. True => the track
  must pass the music-licensing eligibility check.
- ai_voice: audio.ai_voice_allowed not true => render.json shows no synthetic voice.
- caption checks: same validator the caption stage uses, run again on the .caption.txt file.
- required_lines: present verbatim in the caption, or in render.json on-screen lines when the
  rule puts them on screen. Past `expires` gives a warn, not a fail.
- on_screen_text: policy forbidden => no overlay; required => overlay present.
- posting limits: count rows for the clip in results.csv. Over max_posts_per_clip fails. When
  same_clip_on_multiple_accounts or same_clip_across_platforms is forbidden or null (unknown),
  warn if the clip is already logged on another account or platform.
- valid_until passed: warn.

## LLM review (advisory, cheap model)
Give it the rules (prohibited, other_rules, style_notes, allowed_facts, on_screen_text), the
caption, the on-screen lines and the clip transcript. Ask for a list of {issue, severity,
quote}. It checks: each prohibited item, each other_rule, claims not supported by the transcript
or allowed_facts, and whether on-screen text matches what is spoken. Mark results `warn` or
`manual`; the LLM never produces a final pass.

## Human gate
`qa.json` carries human_approved: null. Publishing requires every check pass or acknowledged warn
AND human_approved == true, set by the `approve` command after I watch the clip. Never set it
in code.

## Checklist
`content-clipper checklist <campaign>` prints rules.checklist and rules.posting items that code
cannot control (engagement rate, audience mix, likes visible, days live), plus unknowns.
EOF

# ---------------------------------------------------------------- patches to existing skills (once)
if ! grep -q 'campaign-aware' .claude/skills/select-clips/SKILL.md; then
cat >> .claude/skills/select-clips/SKILL.md <<'EOF'

<!-- campaign-aware -->
## Campaign awareness
- Read campaigns/<name>/rules.yaml (status reviewed). Take length limits from video.min_length_s
  and video.max_length_s, falling back to config/rubric.yaml when null.
- Put `prohibited` and `other_rules` into the prompt verbatim and tell the model to reject any
  window that breaks them (for example violent or controversial moments when the brief bans them).
- If hook_style.mode is descriptive or none, score hook_strength as "clear and accurate
  opening", not "attention-grabbing exaggeration", and do not reward clickbait.
- Skip source videos that are not in data/sources/registry.csv when source.require_registry.
- Write the campaign name into clips.json.
EOF
fi

if ! grep -q 'campaign-aware' .claude/skills/render-clip/SKILL.md; then
cat >> .claude/skills/render-clip/SKILL.md <<'EOF'

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
EOF
fi

# ---------------------------------------------------------------- ARCHITECTURE.md patch (once)
python3 -I - <<'PY'
import pathlib
p = pathlib.Path("docs/ARCHITECTURE.md")
t = p.read_text()
t = t.replace(
    "Renders: data/out/<video_id>/<clip_id>.mp4, plus .ass captions and .log (ffmpeg stderr).",
    "Renders: data/out/<campaign>/<video_id>/<clip_id>.mp4 plus sidecar files (see Campaign-aware stages below).")
t = t.replace(
    "- QA: check length, resolution, audio present, required mentions, music licence, against\n"
    "  campaigns/<name>/rules.yaml. QA failures block publishing.",
    "- QA: see Campaign-aware stages below. QA failures block publishing.")
if "<!-- campaign-aware -->" not in t:
    t = t.rstrip("\n") + "\n" + '''
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
'''
p.write_text(t)
PY

# ---------------------------------------------------------------- BUILD_PLAN.md (rewritten; S1 recorded as done)
cat > docs/BUILD_PLAN.md <<'EOF'
# Build plan (fixed sessions, one scope per session)

Rule: do ONLY the first unticked session. Tick the box and add a short note when done.
Fixed rhythm: Mon, Wed, Fri 19:30 and Sat 10:00.

- [x] S0 Scaffold (done by hand)
- [x] S1 Wed 11 Nov. Transcribe. Commit 32c93d5.
  Whisper small, CPU int8: about 0.08-0.09x real time on a 64 s clip (11x faster than real time).
  Audio decoding uses system ffmpeg because faster-whisper 1.2.1 cannot decode with PyAV 19.
- [ ] S2 Fri 13 Nov 19:30. Campaign rules.
  pydantic CampaignRules model from docs/CAMPAIGN_RULES.md, `campaign add / show / approve`,
  LLM draft with verbatim-quote evidence check, brief hash check, draft-status gate, `checklist`.
  Accept: tests on docs/examples/ (valid, invalid, a hallucinated quote reset to null, a changed
  brief reverting status). Needs ANTHROPIC_API_KEY and one real pasted brief of mine.
- [ ] S3 Sat 14 Nov 10:00. Select.
  LLM scoring per the select-clips skill, snapping to word timestamps, rules-aware prompt,
  clips.json written. Accept: snapper unit tests, including a rejected mismatch.
- [ ] S4 Mon 16 Nov 19:30. Cut + word-highlight captions (ASS) for one clip.
  Accept: duration within 0.1 s of end_s - start_s; captions visible.
- [ ] S5 Wed 18 Nov 19:30. 9:16 face-tracked reframe (centre-crop fallback) and overlay text.
- [ ] S6 Fri 20 Nov 19:30. Caption stage and compliance QA, per the caption-generation and
  compliance-qa skills. Accept: all deterministic checks tested on the example campaign.
- [ ] S7 Sat 21 Nov 10:00. First full run on a real campaign video, no music, end to end
  through the human approval gates.
- [ ] S8 Mon 23 Nov 19:30. Research step (YouTube Data API) writing the trend brief.
- [ ] S9 Wed 25 Nov 19:30. Music mixing from config/music_manifest.yaml, only where rules allow it.
- [ ] Later, Sat 28 Nov 10:00. GPU driver and CUDA setup (separate task, read-only checks first).
- [ ] Later. Publish and scheduling.
EOF

# ---------------------------------------------------------------- HANDOFF.md (rewritten; works for every session)
cat > docs/HANDOFF.md <<'EOF'
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
EOF

# ---------------------------------------------------------------- CLAUDE.md addendum (once)
if ! grep -q 'campaign-aware' CLAUDE.md 2>/dev/null; then
cat >> CLAUDE.md <<'EOF'

<!-- campaign-aware -->
# Campaign rules are data
- Every campaign has campaigns/<name>/brief.md (verbatim) and rules.yaml (structured). See
  docs/CAMPAIGN_RULES.md. Never put campaign-specific logic or wording in code or tests; use the
  fixture in docs/examples/.
- No stage runs on a rules.yaml with status: draft. Only a human sets status: reviewed.
- Captions are assembled and validated in code; the LLM supplies wording and a list of claims, each
  backed by a verbatim quote from the transcript or rules.allowed_facts.
- QA fails closed: a null safety rule (music, AI voice, source) means not allowed.
EOF
fi

echo "Done. Files created or updated:"
find campaigns/_template docs .claude/skills -type f | sort
echo
echo "Next: git add -A && git commit -m 'Make pipeline campaign-aware: rules schema, caption and QA skills'"