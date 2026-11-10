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
