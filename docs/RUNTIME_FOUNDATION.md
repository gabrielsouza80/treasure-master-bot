# Observation-only runtime

`python tools/run_runtime.py` observes the same offline video as the existing
inspector. It runs the unchanged target/state/knife/tracking implementations.
`--max-frames 100` limits observation; default video processing reaches EOF.
Optional `--log debug/runtime/session.jsonl` creates an exclusive, bounded JSONL
file (existing files are never overwritten). Device serials, raw UI text/XML,
images and pairing data are excluded from telemetry. Telemetry includes source
time, sequence, visual/runtime state, target validity, knife uncertainty,
foreground package, planned action/guard result and watchdog recommendation.

## Boundaries and safety

FrameSource -> target -> existing conservative game state -> knives/tracker ->
runtime decision -> ActionGuard -> InputController. No detector owns Android I/O.
InputController is permanently dry-run for this milestone and has **no executable
input transport**. Passing `dry_run=False` raises an error. Even explicitly
authorized, guard-approved actions return a DRY_RUN record without device calls.
No CLI flag enables real input. Launch zones default empty; forbidden zones win.
Gameplay requires PLAYING, fresh frame-bound authorization, foreground evidence,
full state score, and certain knife evidence. These are necessary gates, not a
collision predictor or approval to enable future firing.

Offline PLAYING does not pretend foreground has been checked. Live context adds
a fresh verified expected package, never weakens image evidence. Stale packets
are rejected before vision and clear stabilization/tracking. UI snapshots expire
after two seconds. Known ad activity names must be explicitly verified/configured;
default allowlist is empty. Package changes/words alone yield UNKNOWN. Reserved
GAME_OVER/CONTINUE states need future verified signatures.

## Frame-source comparison and decision

| Option | Latency/FPS/jitter | Complexity / maintenance | Decision |
| --- | --- | --- | --- |
| Python scrcpy client | Callback stream can avoid screenshot round trips; actual machine FPS unmeasured | Reviewed py-scrcpy-client deploys server 2.4; 4.1 compatibility not established; adds decoder dependencies | Do not integrate this client now |
| Official scrcpy stream + maintained decoder | Streaming is the preferred low-latency production direction; latest-frame queue prevents backlog; measure actual receipt age/jitter | Official 4.1 video/control separation and raw stream documented; deployment/decoder integration still required | Preferred next transport milestone; no new video protocol implemented |
| ADB exec-out screencap PNG | Extra process, PNG encoding and wireless round trips; no real-time FPS claim | Existing ADB, OpenCV decoding, bounded timeout; read-only | Implemented observation fallback |
| Windows scrcpy window capture | Additional render/capture delay; occlusion/DPI/letterboxing complicate geometry and freshness | Requires reliable client-area capture mapping, Windows-specific integration | Not selected |

`LatestFrameSource` is the transport-independent one-slot callback adapter for a
future maintained decoder. Producer supplies real source PTS and host receipt
time; re-polling does not invent freshness. Buffers are copied to avoid recycling
and old queued frames are dropped. No scrcpy server/protocol fork was added.
`VideoFrameSource` preserves decoded PTS; `AdbScreenshotSource` marks timestamp
before the screenshot query, so slow captures are conservatively stale.

Read-only live fallback example (explicit device/package, no app launch):
`python tools/run_runtime.py --source adb --serial DEVICE --package com.gimica.treasuremaster --max-frames 10`
ADB must be on PATH or supplied with `--adb`. No automatic device discovery,
pairing, connect, launch, BACK, taps or hierarchy server installation occurs.
Foreground can change during acquisition; this observational adapter does not
constitute authorization to enable live actions. Streaming high-FPS validation
and independent UI polling remain future work. Do not claim ADB fallback reaches
the offline vision FPS.

## UI inspection and recovery

Optional host dependency: `python -m pip install -r requirements-android.txt`.
Verified release uiautomator2 3.7.0 supports Python 3.11. Pin records the reviewed
release, separate from lightweight offline requirements. UiProbe accepts an
already initialized client and calls only app_current/dump_hierarchy. A fresh
uiautomator2 connection may push a JAR/start a device service; we deliberately
do not initialize it on a phone in this milestone.

Probe reads app before/after hierarchy; changes invalidate the observation.
Parser rejects malformed/DTD XML, invalid bounds, disabled/hidden controls and
inherited disabled/hidden parents. Exact text/content-description/resource-id
matches provide candidates; configurable vocabulary accommodates more languages.
X/× remain ambiguous even with a suggestive resource ID. Multiple close matches
are not arbitrarily selected. Continue/Restart candidates do not establish game
menus by themselves and never execute.

Verified ADVERTISEMENT -> wait >=5s -> one fresh valid hierarchy close candidate
-> WOULD_CLICK_CLOSE. Without it, future verified OpenCV close evidence would be
considered (not implemented); >=45s yields WOULD_PRESS_BACK; >=90s yields
WOULD_RELAUNCH_GAME. Every proposal remains unauthorized and blocked. No chain is
executed. Repeated proposals trigger RECHECK_UI rather than retries. UNKNOWN
only waits/rechecks; it does not become AD because visual evidence disappeared.

Watchdog separates source PTS from host monotonic time: stale frames/frozen PTS
produce STOP, prolonged UNKNOWN/lost target/unchanged UI/repeated proposals
produce RECHECK_UI. It produces no coordinates and performs no actions. UI probes
are outside the vision processing call so a future slow accessibility worker
need not block the fast loop. Performance overhead excludes image analysis;
full offline wall-clock throughput includes decoding.

## Validation (2026-10-06)

- 48 unittest tests passed, including all 29 pre-existing tests. Compileall,
  dependency consistency and Git whitespace checks passed.
- Original full inspector: 6628/6628 frames, 2209 PLAYING / 4419 UNKNOWN,
  20 transitions, no PLAYING segments shorter than 0.2 seconds.
- PLAYING pipeline 52.84 FPS; full inspector including decode 77.47 FPS.
- Runtime: same states and stable knife counts on every frame, 76.75 FPS
  including decoding/telemetry; measured orchestration overhead 0.0517 ms/frame.
- Local annotation benchmark: 37 certain frames (2 uncertain excluded), angular
  recall 95.83%, precision 100%, 7 misses, 0 false positives. This is one-recording
  regression evidence, not held-out live/ad/skin accuracy.
- Optional uiautomator2 3.7.0 installed; read-only probe tested with mocked client.
  ADB devices returned no connected device. Live probe/stream FPS were not tested.
  No device input, app launch, BACK, restart or ad close was executed.

## Existing commands

The original `tools/inspect_video.py`, keyboard controls, headless CSV/reports
and `tools/knife_benchmark.py` remain unchanged. No reference repository, captured
video, image, runtime telemetry or environment files belong in Git.
