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
  same_clip_on_multiple_accounts or same_clip_across_platforms is forbidden, warn if the clip is
  already logged on another account or platform. Cross-platform is allowed by default in every
  campaign.
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
cannot control (engagement rate, audience mix, likes visible, days live).
