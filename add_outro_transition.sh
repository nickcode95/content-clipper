#!/usr/bin/env bash
# Adds the outro-transition skill to the content-clipper repo.
# Run from the repo root:   bash add_outro_transition.sh
# Safe to re-run: it overwrites only its own file.
set -euo pipefail

if [ ! -f pyproject.toml ] || [ ! -d .claude/skills ]; then
  echo "Run this from the content-clipper repo root (pyproject.toml / .claude/skills not found)." >&2
  exit 1
fi

mkdir -p .claude/skills/outro-transition

cat > .claude/skills/outro-transition/SKILL.md <<'EOF'
---
name: outro-transition
description: Use when joining an outro, sting or end card to a clip, or when a join feels jumpy. Gives the audio context (rhythmic gunfire into a descending bass boom), how to measure the clip's tail, and how to place the outro so the rhythm resolves instead of restarting.
---
# Outro transitions: arrange the audio, do not just concatenate it

This is an arrangement problem, not a click or quality problem. Claude cannot listen, so work from
measurements and spectrograms, and let Nick's ears make the final call with an A/B render.

## The audio context
- Source clips are game footage. Machine-gun fire is percussive and repeats at a steady rate
  (about 8 to 10 shots per second). The ear hears that as a beat grid. Each shot has a short
  decay, a low thump around 100 Hz, and the burst ends with a ring-out tail (hundreds of ms).
- The outro is a descending bass boom: a sine sweep falling from about 150 Hz to 30 Hz, a sharp
  attack, and a 1 to 2 second decay. Its impact is at its start.
- Joined naively, the clip ring-outs and then the boom arrives at a random time, off the gunfire's
  grid, so it sounds like a missed beat or a second rhythm. The low end of both also stacks around
  100 Hz and turns to mud.

## Principles
1. Resolve the rhythm, do not restart it. The boom should read as the final beat or the big last
   hit of the burst, never as a new, unrelated event.
2. Land the boom on the gunfire grid (next expected shot) or on the last real shot. Not after
   the ring-out, and not at an arbitrary time.
3. Duck the clip under the boom (sidechain) so the two low ends do not stack.
4. Do the join in one filter graph and normalise loudness once at the end (see render-clip).

## Measure the tail first (reference code, verified on a synthetic burst)
Needs `librosa` (`uv add librosa`: ask Nick before adding it; it pulls in numba, scipy and
scikit-learn). Normalise the onset envelope and use a RELATIVE delta. An absolute delta found 1 of
38 shots in testing. librosa onset times lag the true time by about 25 ms; that is fine here.

    import json, librosa, numpy as np

    def tail_profile(path, sr=22050, hop=128):
        y, sr = librosa.load(path, sr=sr, mono=True)
        oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        oenv = oenv / max(oenv.max(), 1e-9)
        on = librosa.onset.onset_detect(onset_envelope=oenv, sr=sr, hop_length=hop, units="time",
                                        backtrack=False, delta=0.06, wait=int(0.05 * sr / hop))
        if len(on) == 0:
            return {"duration_s": round(len(y) / sr, 3), "burst_onsets": 0}
        rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
        t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
        db = 20 * np.log10(np.maximum(rms, 1e-6) / np.max(rms))
        last = float(on[-1])
        run = [last]                       # trailing burst: onsets with gaps under 0.5 s
        for o in on[-2::-1]:
            if run[0] - o < 0.5:
                run.insert(0, float(o))
            else:
                break
        ioi = np.diff(run)
        med = float(np.median(ioi)) if len(ioi) else None
        cv = float(np.std(ioi) / np.mean(ioi)) if len(ioi) > 2 else None
        peak = db[(t >= run[0]) & (t <= last + 0.1)].max()
        after = (t > last) & (db < peak - 30)
        decay = float(t[after][0] - last) if after.any() else float(t[-1] - last)
        return {"duration_s": round(len(y) / sr, 3), "burst_onsets": len(run),
                "burst_start_s": round(run[0], 3), "last_onset_s": round(last, 3),
                "ioi_median_s": None if med is None else round(med, 4),
                "ioi_cv": None if cv is None else round(cv, 3),
                "decay_to_minus30db_s": round(decay, 3),
                "tail_after_last_onset_s": round(len(y) / sr - last, 3)}

Expected on a clean 9 shots/s burst: all shots found, IOI median about 0.11 s, ioi_cv about 0.04,
last onset within 40 ms of the truth. These thresholds were checked on synthetic audio only. On real
footage (voices, music, explosions) tune delta and report the numbers to Nick.
Run it on the clip audio only, never the mixed result. Cache the profile next to the clip.

## Choose the mode from the profile
- grid_snap: burst_onsets >= 6 and ioi_cv < 0.15 (regular fire). Boom starts at
  last_onset + 1 x ioi_median (the next expected shot).
- impact: irregular or sparse bursts (ioi_cv >= 0.15 or burst_onsets < 6). Boom starts exactly at
  last_onset, so it reads as the final hit.
- gap: no percussive activity at the end (speech, quiet). End on a natural pause, then a 0.3 to
  0.4 s `acrossfade`. Do not apply the two modes above.
Default comes from config/render.yaml (add `outro.mode: auto | grid_snap | impact | gap`). For
every new outro or campaign, render ALL applicable modes side by side for Nick to listen to.

## ffmpeg recipes (tested on ffmpeg 6.1, 48 kHz)
Let LAST = last_onset_s, IOI = ioi_median_s, MS = boom start in whole milliseconds.

grid_snap (boom at LAST + IOI, clip tail shortened, faded, and ducked under the boom):

    -filter_complex "[0:a]atrim=0:{LAST+IOI+0.45},afade=t=out:st={LAST+IOI}:d=0.45[c];
    [1:a]adelay={MS}|{MS},asplit=2[bm][bk];
    [c][bk]sidechaincompress=threshold=0.02:ratio=8:attack=5:release=250[cd];
    [cd][bm]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[a]"

impact (clip cut 60 ms after the last shot, boom lands on it):

    -filter_complex "[0:a]atrim=0:{LAST+0.06},afade=t=out:st={LAST+0.04}:d=0.02[c];
    [1:a]adelay={MS}|{MS}[b];
    [c][b]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[a]"

Cut the video at the same instant as the audio, and play the outro's video from the same offset
as its audio, so picture and sound stay in sync. If the outro file has pre-roll before its
impact, set `impact_s` in config/outro.yaml and use `adelay = start - impact_s`.

## Outro manifest: config/outro.yaml
    file: assets/outro/boom.wav
    kind: descending_boom
    impact_s: 0.0          # time inside the file where the hit lands
    duration_s: 2.5
    license_url: ""        # proof of licence, kept in licenses/
Analyse the outro once and store these values. Do not re-analyse it on every render.

## Verify by numbers and by picture
1. Measure the result: low band (25 to 120 Hz) envelope onset of the boom minus last_onset_s,
   divided by ioi_median_s. grid_snap should give about 1.0, impact about 0.0. A naive join
   gave 13.5 in testing (off the grid, after the ring-out).
2. Render a spectrogram and LOOK at it with Read:
   `ffmpeg -i out.wav -lavfi showspectrumpic=s=900x300:legend=0:scale=log out.png`
   (for an mp4 use `-i out.mp4`). Good: the boom starts right where the gunfire stripes stop.
   Bad: a visible gap or ring-out between the stripes and the boom.
3. Ask Nick to listen to the variants. Never claim it sounds right from numbers alone.

## Compliance
The outro is added music. When rules.audio.music_allowed is not true, do not add it, and the QA
stage must fail a render whose render.json lists an outro. Otherwise it needs a licence entry like
any other track (see music-licensing). Record the outro and the mode used in render.json.

## Tests
Use synthetic fixtures (no downloads): a noise-burst train at 9 shots per second with 3 ms jitter
and a decaying tail, plus a descending sine sweep for the boom. Assert the shot count, the mode
chosen, and the boom onset in units of IOI.
EOF

echo "Done. Added .claude/skills/outro-transition/SKILL.md"
echo "Next: git add -A && git commit -m 'Add outro-transition skill'"