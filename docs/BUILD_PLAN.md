# Build plan (fixed sessions, one scope per session)

Rule: do ONLY the current session's scope. Tick the box and add a short note when done.

- [x] S0 Scaffold (done by hand)
- [x] S1 Wed 11 Nov 19:30. Transcribe.
  `uv run content-clipper transcribe <video>` writes the transcript contract.
  Accept: word timestamps are monotonic; prints duration, wall time and real-time factor;
  unit tests use fake segments (no model download); one slow integration test gated by RUN_SLOW=1.
  Done 10 Nov: small/cpu/int8 RTF 0.08-0.09 (~11x real time) on a 64 s 4K60 clip, model load ~1.1 s.
  Audio is decoded by system ffmpeg (faster-whisper 1.2.1 + av 19 breaks its own decoder).
- [ ] S2 Fri 13 Nov 19:30. Select.
  LLM scoring per the select-clips skill, snapping to word timestamps, clips.json written.
  Accept: unit tests for the snapper on a synthetic transcript, including a rejected mismatch.
- [ ] S3 Sat 14 Nov 10:00. Cut + captions.
  pysubs2 word-highlight ASS, ffmpeg cut and burn for one clip.
  Accept: output duration within 0.1 s of end_s - start_s; captions visible.
- [ ] S4 Mon 16 Nov 19:30. 9:16 face-tracked reframe with centre-crop fallback.
- [ ] S5 Wed 18 Nov 19:30. Hook overlay and QA script against campaign rules.
- [ ] S6 Fri 20 Nov 19:30. Research step (YouTube Data API) writing the trend brief.
- [ ] S7 Sat 21 Nov 10:00. First full run on a real campaign video, no music.
- [ ] S8 Mon 23 Nov 19:30. Music mixing from config/music_manifest.yaml.
- [ ] Later, Sat 28 Nov 10:00. GPU driver and CUDA setup (separate task, read-only checks first).
- [ ] Later. Publish and scheduling.
