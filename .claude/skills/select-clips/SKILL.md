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
