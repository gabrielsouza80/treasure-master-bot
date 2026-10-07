# Autoplay MVP validation status

The autoplay MVP is **not commissioned**. Do not treat a passing unit suite,
mocked taps, or the presence of `--autoplay` as proof of working live autoplay.

## Guarded commissioning (2026-10-07)

The initial acquisition path is now implemented. `--calibrate` can request a
typed calibration-only permit on a `TRUE_ZERO` target without predicting flight
time. This exception never grants a normal `SAFE` autoplay permit.

`TRUE_ZERO` requires confirmed PLAYING, matching fresh package/activity, a fresh
frame, a healthy watchdog, a waiting projectile, valid detectors, zero raw,
confirmed, probable and held knives, directly visible proximal perimeter,
and negative long-shaft evidence independent of knife brightness. Geometry and
zero evidence must persist for at least 350 ms and 12 consecutive frames. Only
`NO_CONFIRMED_TRACKS` may be ignored. A confirmed/expired knife seen earlier in
the process permanently blocks the zero exception; a rejected one-frame
candidate resets the warmup rather than becoming a permanent known knife.
Short gems/horns require directly visible negative radial continuation; masked
continuation remains unknown. This is conservative visual evidence, not proof
against every previously unseen skin.

After an accepted measurement, additional commissioning shots require every
horizon in the measured median +/- at least 150 ms envelope to be SAFE. Horizons
are spaced by at most 5 ms, with angular padding for gaps between samples.
Missing/stale samples, reversal, excessive residual/acceleration, or a hidden
future collision sector block the permit. Normal autoplay still requires three
accepted samples, bounded spread, matching resolution and recent measurement.

Command, movement, impact and confirmation observations use host monotonic
time. Device PTS is used only for source ordering. Accepted local profiles
include confirmation timing; profile writes refuse to overwrite existing files.
Commissioning is limited to three actual shots and stops without switching into
normal autoplay. The backend has no ad/menu/BACK/relaunch commands.

Autoplay now uses a read-only foreground worker at 100 ms polling intervals,
without hierarchy dumps. A fresh cached package/activity check immediately
before input avoids blocking vision on the measured 180–246 ms ADB query.
The existing hierarchy observer remains available unchanged.

### Live outcome

- Final preview: 21 TRUE_ZERO observations, zero real taps.
- Real commissioning: one ADB gameplay tap, then `CONFIRMATION_INTERRUPTED`.
- The target detector lost the target and state became UNKNOWN approximately
  375 ms after the host command. Input stopped; the sample was rejected.
- No movement/impact timing was accepted. Samples 0, accepted 0, rejected 1.
- A subsequent read-only review showed one attached knife and game counter 1.
  This confirms the visible outcome, not an automatically accepted timing sample.
  No collision/failure was observed in those reviewed frames.
- The post-calibration dry run and 3/5-shot autoplay pilots were **not run**:
  calibration remained invalid. No further real input was sent after the stop.
- Real-run source rate: 60.01 FPS; total-runtime consumer rate 32.46 FPS
  includes capture startup. Processing P95: 30.44 ms.
- Final regression suite: 121 tests passed. Original recording: 6628 frames,
  2209 PLAYING, 4419 UNKNOWN, 20 transitions, no short PLAYING sequences.
  Full headless throughput 56.60 FPS; PLAYING pipeline 37.02 FPS.
  Angular precision 100%, recall 95.83%, unchanged from the reviewed benchmark.
- Second recording: 1708 frames, 423 PLAYING, 1285 UNKNOWN; uncertainty 55.08%.
- Isolated detector comparison against the previous commit measured roughly
  0.29 ms extra for negative evidence. The earlier 51 -> 36.5 FPS offline
  difference is not explained by this addition; its full cause is unproven.

Current status: **NEEDS_WORK**. Acquisition must retain enough read-only event
evidence to diagnose target-detector interruptions and delayed input before
another live commissioning attempt. Do not relax PLAYING or fabricate timing.

### Commands

Use your explicitly selected serial and installed tool paths in these environment
variables; no device endpoint or pairing information belongs in source control.
Choose new artifact paths for each run.

```powershell
.\.venv\Scripts\python.exe tools/run_autoplay.py --serial $env:TREASURE_SERIAL --adb $env:TREASURE_ADB --scrcpy $env:TREASURE_SCRCPY --duration 120 --enable-input --autoplay --calibrate --max-shots 3 --calibration-output debug/runtime-live/flight-calibration.json --log debug/runtime-live/commissioning.jsonl --report debug/runtime-live/commissioning.json --review-dir debug/runtime-live/commissioning-frames
```

The user must open normal gameplay and stop touching the screen before real
commissioning. A failure/UNKNOWN interruption latches a stop. With a valid profile,
first run prediction only; input flags must be absent:

```powershell
.\.venv\Scripts\python.exe tools/run_autoplay.py --serial $env:TREASURE_SERIAL --adb $env:TREASURE_ADB --scrcpy $env:TREASURE_SCRCPY --duration 60 --calibration debug/runtime-live/flight-calibration.json --log debug/runtime-live/prediction.jsonl --report debug/runtime-live/prediction.json
```

Only after reviewing plausible calibrated predictions may a separate explicitly
enabled pilot use `--max-shots 3`. A second pilot of five is allowed only after
3/3 automatic confirmations without failure. Do not run either pilot now with
the invalid profile from this attempted commissioning.

## Previous checkpoint (68df99e, before commissioning implementation)

The following records describe the earlier checkpoint, not the current zero
exception.

### Safety corrections at that checkpoint

The previous calibration branch allowed taps using an unmeasured 650 ms flight
horizon. That bypass is removed. Missing or expired flight measurements block
both autoplay and calibration refinement. Two input flags are still required;
only the restricted gameplay tap backend exists. App package and activity are
checked independently by the engine and again immediately before input.

Rotation now reports velocity at the newest sample rather than the midpoint
of an accelerating window. Nonfinite tracks and masked angles fail closed.
Confirmation requires observed projectile movement, a new knife birth, all
previous tracks, and two subsequent stable observations. Losing gameplay or
tracking while a shot is pending latches a stop: recycled tracker IDs after a
reset cannot confirm the previous shot.

### Earlier evidence collected on 2026-10-07

- Baseline: `3c2250ff894fb04cc43992fb6f589047ed2b5c20` on local and remote main.
- Unit/regression suite: 96 tests passed, including 25 autoplay tests.
- Original recording: 6628 frames, 2209 PLAYING, 4419 UNKNOWN, 20 transitions.
  Latest headless PLAYING pipeline measurement: 36.50 FPS; below the previous
  approximately 51 FPS baseline and needing an isolated performance comparison.
- Dense benchmark: 100% angular precision, 95.83% recall; 0 false positives.
- Second recording: 1708 frames, 423 PLAYING; count uncertainty 55.08%.
- Live read-only run: 65.015 seconds, 927 PLAYING and 2438 UNKNOWN frames;
  consumer rate 51.76 FPS. Count uncertainty in PLAYING was 50.16%.
- Live decisions: 3337 WAIT_UNKNOWN, 20 WAIT_CALIBRATION, 4 WAIT_UNCERTAIN,
  4 WAIT_ROTATION, 0 WOULD_FIRE, 0 Android input actions.
- Launch-shaft geometry measured in three second-recording frames: angles
  179.88, 180.19 and 180.19 degrees. Nominal impact angle 180 is configurable.

These results validate observation and blocking, **not safe shot timing**.
The live run did not reach a calibrated prediction and cannot validate firing.

### Remaining work reported at the earlier checkpoint

Implement initial calibration acquisition with an explicitly bounded,
independently justified calibration-shot safety procedure. There are no real
command-to-impact samples yet. Do not manufacture a JSON seed from assumed
timing or mocked tests. `--calibrate` currently only refines a recent measured
profile and writes to a new `--calibration-output` path; it cannot acquire the
initial profile. Calibration timestamps measure host command to visually
observed movement/impact and include capture latency, not pure physical input
or device-to-host latency.

The 28-degree geometry margin is a provisional conservative setting, not a
measured collision threshold. Dense/occluded sectors remain blocked. Targets
with no confirmed knife tracks cannot provide track-based rotation evidence.
Short visual gameplay losses during a shot currently abort confirmation;
projectile/impact sequences must be reviewed before any real pilot.

After initial timing acquisition, repeat a calibrated prediction-only review
and then a bounded pilot of at most ten taps, with per-shot visual confirmation.
No ad, Continue, Restart, BACK, or relaunch actions belong to this milestone.

Local telemetry and recordings are under ignored `debug/runtime-live/` paths.
