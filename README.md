# treasure-master-bot

Offline visual inspection of Treasure Master recordings. No ADB connection,
clicks, taps, or advertisement handling are implemented in this milestone.

Run from this repository with the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe tools/inspect_video.py
.\.venv\Scripts\python.exe tools/inspect_video.py --headless --report debug/state-analysis.json --csv debug/state-analysis.csv
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The default input is `debug/treasure-sample-2.mp4`; an optional positional path
selects another video. SPACE pauses/resumes, A/D seek about one second, S saves
the raw frame, and Q quits. Debug frames and reports are ignored by Git.

`src/vision/target_detector.py` retains the Hough circle detector, normalizes its
working resolution, and excludes small inner rings. It returns a candidate in
input pixels, never a gameplay decision.

`src/states/game_state.py` returns a `StateResult` with state, diagnostic score,
individual feature values, and failed checks. PLAYING requires plausible target
geometry, four bright green HUD diamonds, bright stage text, the magenta currency
gem, a clear lower playfield, and the supported portrait aspect ratio. Every gate
is mandatory; `score` is a fraction of passing checks, not a probability.
`StateStabilizer` requires six consecutive positives (~112ms at the sample's
average FPS) and revokes PLAYING
immediately on missing evidence. Update once per fresh frame; reset on seeking
or capture discontinuities. Paused frames never count toward confirmation.

Calibration uses multiple enemies and gameplay, Continue, restart, reward,
popup, transition, and full-screen ad frames from this recording. Unknown layouts,
dimmed HUDs, missing/partial circles, and moving/shrinking targets intentionally
return UNKNOWN. An in-game banner can coexist with PLAYING; this does not authorize
interacting with that banner. These heuristics are not validated against unseen
advertisements or different game versions. Other states are deliberately unnamed.

The recording has variable frame timing. Display/report times use decoded video
timestamps. OpenCV random seeks produced different images from sequential decoding
on this sample, so A/D replay-decode from the beginning to the requested timestamp.
This can take a few seconds near the end. Regression fixtures are independent,
visually reviewed sequential frame IDs rather than random-seek screenshots.

Headless mode evaluates every decoded frame, checks the expected frame count,
and reports raw/stabilized counts, state segments, short PLAYING runs (<0.2s),
failure counts, and processing FPS including decoding. Short runs are review
candidates, not automatically false positives. Video-dependent tests are explicitly
skipped if the local recording is unavailable; this milestone was validated with it.
