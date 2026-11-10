---
name: select-clips
description: Use when building or running the clip selection stage. The LLM sees one keyframe per shot plus the narration and campaign rules, picks shot ranges, and code snaps them to shot and word boundaries and writes clips.json.
---
# Selecting clips

Inputs: transcript JSON, shot analysis + keyframes, config/rubric.yaml, campaigns/<name>/rules.yaml,
optional trend brief.

Rules (video-aware; code in src/content_clipper/select/core.py):
1. Inputs: transcript, data/analysis/<video_id>/shots.json + keyframes (run `analyze` first),
   reviewed campaign rules, config/rubric.yaml. The source must be in data/sources/registry.csv
   for the campaign when source.require_registry.
2. Send one keyframe per shot labelled [H###] and a timeline: shot length, audio level, motion,
   brightness and the words spoken in it. Never send or accept timestamps.
3. Ask for JSON: candidates with start_shot, end_shot, hook_line, title, scores (one 0-10 value
   per rubric key), reason. Validate with pydantic; unknown shot IDs are rejected and logged.
4. Snap in code: cuts on shot boundaries (end pulled back one analysis frame); extend to the
   nearest shot boundary that keeps a sentence whole if that costs at most 3 s; otherwise move
   the cut to a word edge (never inside a word) and set speech_cut. Enforce min/max length from
   the rules (rubric window_seconds when null). Remove overlaps, keeping the higher score.
5. score = sum(weight * value) / sum(weights). Sort descending, ids c01.., approved: null.
   Refuse to overwrite an existing clips.json without --force (it may hold approvals).
6. Hooks: up to 4 hook_options per clip, read like a fan's post reacting to the clip, playing on
   anticipation (speculation, excitement, intrigue, nostalgia, frustration), e.g. "did i just see
   <old-school gun>". Name only what is visible or said in the clip; follow hook_style.mode and
   the campaign rules. Code drops options with numbers not in the clip, brief, allowed_facts or
   fan_reference names, forbidden phrases, or 5-word runs copied from the research notes.
   `hooks <campaign> <video_id>` refreshes options without touching cuts or approvals;
   `pick-hook <video_id> <clip_id> <n>` is the human choice. Never copy trend-brief example
   titles verbatim.
7. Use the selection model in config/models.yaml (no temperature setting on current models).

System prompt: `SYSTEM` and `HOOK_GUIDE` in src/content_clipper/select/core.py.

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
