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
