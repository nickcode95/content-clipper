# Build plan (fixed sessions, one scope per session)

Rule: do ONLY the first unticked session. Tick the box and add a short note when done.
Fixed rhythm: Mon, Wed, Fri 19:30 and Sat 10:00.

- [x] S0 Scaffold (done by hand)
- [x] S1 Wed 11 Nov. Transcribe. Commit 32c93d5.
  Whisper small, CPU int8: about 0.08-0.09x real time on a 64 s clip (11x faster than real time).
  Audio decoding uses system ffmpeg because faster-whisper 1.2.1 cannot decode with PyAV 19.
- [x] S2 Fri 13 Nov 19:30. Campaign rules.
  pydantic CampaignRules model from docs/CAMPAIGN_RULES.md, `campaign add / show / approve`,
  LLM draft with verbatim-quote evidence check, brief hash check, draft-status gate, `checklist`.
  Accept: tests on docs/examples/ (valid, invalid, a hallucinated quote reset to null, a changed
  brief reverting status). Needs ANTHROPIC_API_KEY and one real pasted brief of mine.
  Done 10 Nov: campaign add/show/approve + checklist. Real MW4 DMZ brief drafted in 48 s with
  claude-sonnet-5-5: 67 values quote-verified, 0 rejected, 7 unknowns. Rules schema is too large
  for grammar-constrained output, so that call uses schema-in-prompt + pydantic + 1 retry.
- [x] S3 Video-aware select.
  `analyze <video>`: shot detection, per-shot loudness/motion/brightness, one keyframe per shot
  (data/analysis/<video_id>/). `select <campaign> <video_id>`: the LLM sees keyframes + narration
  per shot + rules, returns shot ranges; code snaps cuts to shot boundaries, keeps sentences whole
  where it can, never cuts inside a word, enforces lengths, removes overlaps, writes clips.json.
  Accept: shot detection and snapper unit tests on synthetic data, including rejected candidates.
  Done 10 Nov: analyze finds 41 shots in the 64 s 4K60 DMZ trailer in 20 s (beats ffmpeg's scene
  filter on dark cuts); select with claude-sonnet-5-5 + 41 keyframes returns 3 clips (17-23 s) in 19 s.
- [x] S4 Render v1: cut + "band" layout + hook + word-highlight captions for approved clips.
  Full-width 16:9 video on a 1080x1920 canvas (blurred-video or solid background), hook text in
  the band below the video, word-highlight captions (ASS) in the band above, loudnorm, original
  audio only. Accept: duration within 0.1 s of end_s - start_s; text inside the safe area.
  Done 10 Nov: 3 approved DMZ clips (17-23 s) rendered from the 4K60 source in 45 s total on CPU;
  durations exact; frames checked by eye (video centred, captions above, hook below).
- [x] Hooks from fan research (pulled forward from S8): `research add` (pasted notes, kept and
  accumulated), trend brief with fan_references; up to 4 fan-voice hook options per clip, checked
  in code; `hooks` refreshes options, `pick-hook` chooses. Done 10 Nov on the MW4 DMZ clips.
- [x] S5 Action-following 9:16 crop as a second layout (motion-weighted, smoothed, centre fallback).
  Done 11 Nov: analyze stores focus_x per shot (busiest 9:16 window, centre prior, None when still);
  `render --layout crop` cuts one crop position per shot (no panning inside a shot), captions only,
  files <clip>.crop.*; band renders untouched; hook variants skipped. 3 DMZ clips in 44 s.
- [x] Tweet-style hook cards (11 Nov): personas from assets/personas/ (image + handle base), new
  @handle digits per render, rotation; card in the band under the video or above the bottom of
  the crop layout; Pillow added for text, rounded corners and round avatars.
- [ ] S6 Fri 20 Nov 19:30. Caption stage and compliance QA, per the caption-generation and
  compliance-qa skills. Accept: all deterministic checks tested on the example campaign.
- [ ] S7 Sat 21 Nov 10:00. First full run on a real campaign video, no music, end to end
  through the human approval gates.
- [ ] S8 Mon 23 Nov 19:30. Research step (YouTube Data API) writing the trend brief.
- [ ] S9 Wed 25 Nov 19:30. Music mixing from config/music_manifest.yaml, only where rules allow it.
- [ ] Later, Sat 28 Nov 10:00. GPU driver and CUDA setup (separate task, read-only checks first).
- [ ] Later. Publish and scheduling.
