# Autoplay MVP validation status

The autoplay MVP is **not commissioned**. Do not treat a passing unit suite,
mocked taps, or the presence of `--autoplay` as proof of working live autoplay.

## Safety corrections

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

## Evidence collected on 2026-10-07

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

## Remaining commissioning work

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
