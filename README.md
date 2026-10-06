# treasure-master-bot

Offline visual inspection of Treasure Master recordings. No ADB connection,
clicks, taps, or advertisement handling are implemented in this milestone.

Run from this repository with the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe tools/inspect_video.py
.\.venv\Scripts\python.exe tools/inspect_video.py --headless --report debug/state-analysis.json --csv debug/state-analysis.csv
.\.venv\Scripts\python.exe tools/inspect_video.py --headless --report debug/knife-analysis.json --csv debug/knife-analysis.csv --knife-review debug/knife-review
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

Offline attached-knife detection runs only on confirmed PLAYING frames.
`src/vision/knife_detector.py` directly samples a fixed-size polar annulus
(0.85-1.90 target radii). It finds color/luminance contrast against the angular
neighborhood and requires continuous, narrow radial evidence in three outer
bands. Screen-fixed gift, boost and speaker pixels are excluded as occlusions,
so those icons cannot join a target decoration into fake knife evidence.
Small radial gaps are joined for ornate hilts. Local peak plateaus are
collapsed, with circular suppression and valley checks between nearby peaks.
No knife or enemy template is used. A median circular-rim scan can enlarge an
annulus when the supplied circle follows an inner boss ring; this changes only
the knife ROI, never the game-state classifier or its target evidence.

Angles are clockwise-positive: 0 degrees = top, 90 = right, 180 = bottom,
270 = left. All output angles are sorted and normalized to [0, 360).
`detect_knives(frame, target, state=...)` returns validity, per-candidate support
scores, angles/count, and ROI diagnostics. It defaults to UNKNOWN and skips
image processing unless PLAYING is explicitly supplied. A valid empty result
means no candidates were detected, not proof that there are no attached knives.

`src/vision/knife_tracker.py` uses one-to-one circular matches and requires three
consecutive observations for a new track. It estimates the shared angular step
from multiple consistent observations and holds missing tracks for at most 12
frames or 200ms only with that support. A mapped HUD occlusion permits at most
36 frames or 600ms; expiration still uses time since the last full observation.
Complete observation loss survives one frame with explicitly uncertain angles.
Overlapping replacement tracks are suppressed.
Green rays are current observations; amber rays are briefly held observations.
UNKNOWN, seeking, backward timestamps, gaps over 150ms, and large target changes
reset tracking. Counts may decrease; they are not forced to increase forever.
This is observation tracking, not a safe-shot or future rotation predictor.

Headless JSON includes raw/stable count histograms, resets and their reasons,
one-frame candidate rejections, count variation, large jumps, angular match
residuals, and detector/pipeline timing. The PLAYING pipeline timing includes
decoding, target/state detection, knife detection, and tracking; review image and
CSV writes are excluded from that timing. Overall FPS includes the complete run.
Reset counts mark state/capture boundaries, not verified physical stage changes.
Jumps and residuals are review flags, not automatically classification errors.
`--knife-review` saves representative target crops and contact sheets.

Dense recall diagnostics and a human-labeled calibration benchmark:

```powershell
.\.venv\Scripts\python.exe tools/inspect_video.py --headless --report debug/knife-benchmark/after.json --csv debug/knife-benchmark/after.csv --knife-review debug/knife-benchmark/review --knife-debug
.\.venv\Scripts\python.exe tools/knife_benchmark.py debug/knife-benchmark/annotations.json debug/knife-benchmark/after.csv --output debug/knife-benchmark/metrics-after.json
```

The local ignored annotation file contains sequential frame IDs, decoded
timestamps, expected counts, approximate angles, categories and explicitly
excluded ambiguous frames. The evaluator requires every certain frame to be
PLAYING with the same timestamp. It computes count accuracy/MAE, under/overcount
rates and maximum-cardinality, minimum-error one-to-one circular angle matching
within the annotation tolerance (6 degrees here). Missed and extra angles are
reported individually. Labels come from raw-frame review, not detector outputs.
This is a calibration benchmark from one recording, not a held-out accuracy claim.
Video/image fixtures and annotations remain ignored; source regressions include
the known dense boss and the sword-edge false-positive sequence.

Dense separation uses narrower angular morphology/smoothing and an 8-degree
minimum, rather than the old 13-degree cutoff. Peaks closer than 13 degrees need
strong middle-band evidence and an intervening radial valley; this prevents
counting both edges of a wide sword. Global contrast/support thresholds remain
unchanged. Masked samples are unknown, with each radial band requiring at least
45% visible samples before normalization. The gift mask uses its circular
footprint; speaker/boost/reward masks use fixed normalized layout coordinates.
These occlusion zones are sample-specific and require validation on new layouts.

`KnifeTrackingResult` separates confirmed observed knives, held/occluded tracks
and probable unconfirmed births. Diagnostics expose `count_uncertain`, current
observation quality, expired tracks, suppressed births and merged IDs. An empty
result, mapped unseen HUD sectors, partial observations or held tracks cannot
establish a complete knife count. Scores/quality are not calibrated probabilities.
The inspector displays held/probable counts and uncertainty. Detailed review
adds annuli, HUD footprints, blue raw peaks, red rejected peaks, magenta probable
births, per-frame JSON decisions and radial support charts. Ordinary playback
keeps the green/amber confirmed overlays and existing keyboard controls.

Known limits: HUD overlap, hit flashes, short/dark edges, inaccurate circles,
and tightly packed knives can cause misses or candidate angle jitter. Raw hits
near projectile contact are tentative until tracking confirms them; a waiting
knife is outside the annulus. Three-frame confirmation introduces a small birth
delay. A long radial decoration can still resemble a knife. The reviewed sample
and synthetic shapes are regression coverage, not validation of unseen skins or
advertisements. Knife counts must not yet be used to authorize firing.

Observation-only runtime foundations are documented in
[docs/RUNTIME_FOUNDATION.md](docs/RUNTIME_FOUNDATION.md). Run
`python tools/run_runtime.py` for offline dry-run orchestration. This milestone
has no executable Android input backend. External reuse and licensing decisions
are recorded in [docs/EXTERNAL_REUSE_AUDIT.md](docs/EXTERNAL_REUSE_AUDIT.md) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
