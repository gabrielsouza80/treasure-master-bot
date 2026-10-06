# Read-only live observation

## Device infrastructure operations (before first use)

The capture source uses the official **scrcpy 4.1 executable and its bundled
scrcpy-server**, not an old fork. It checks the executable version; the official
server also rejects mismatched version arguments. It uploads a uniquely named
capture JAR under `/data/local/tmp`, creates its own temporary ADB forward, and
starts that JAR with `app_process`. Only video is enabled: `audio=false`,
`control=false`, `power_on=false`, `cleanup=false`. No control socket or window
exists. Full source geometry is retained (`max_size=0`). Shutdown closes video,
stops the host capture process, removes only its own forward and temporary JAR.
These operations do not launch/stop game apps, wake the screen, inject input,
change game settings or interact with ads.

UI inspection runs in a separate bounded host worker process. uiautomator2 3.7.0
may upload `/data/local/tmp/u2.jar` and start its accessibility RPC server through
`app_process` on the device. The adapter calls only `dumpWindowHierarchy`, with
bounded HTTP timeout, and ADB dumpsys foreground queries. No click/touch/press,
app_start/app_stop, watchers or input methods are called. The SDK client remains
inside that worker; the observer receives only immutable snapshots. Startup or
RPC failure invalidates UI evidence. Host worker shutdown is bounded and may
terminate the **host worker**, never an Android app.

This initialization is authorized for read-only hierarchy inspection in this
milestone. It is capture/accessibility infrastructure, not gameplay interaction.

## Transport support evidence

Reviewed official tag v4.1, commit
`2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0`, Apache-2.0:

- https://github.com/Genymobile/scrcpy/blob/v4.1/doc/develop.md
- https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/device/Streamer.java
- https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/device/DesktopConnection.java
- https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/Options.java

The small original reader follows this version's video-only codec/session/packet
metadata. It is deliberately version-specific, never a guessed generic protocol.
PyAV handles H.264 decoding. Packet lengths and session dimensions are bounded;
EOF or decoder failure ends observation with a diagnostic. An idle socket
retains partial data and waits; the watchdog reports missing fresh frames
without fabricating freshness or restarting anything.

| Option | Evidence / compatibility | Dependencies and maintenance | Geometry / control |
| --- | --- | --- | --- |
| Official 4.1 video socket + PyAV | Actual version-matched server metadata inspected | Optional PyAV 18.0.0 (current Python 3.11-compatible release); reader requires audit on version change | Native pixels; control socket disabled |
| CLI recording into decoder pipe | REJECT: recorder.c prefixes filename with `file:`; stdout/socket output is not a supported assumption | No invented pipe support | CLI `--no-control` is supported for ordinary observation/recording |
| Python scrcpy library | Previously reviewed client bundles older server; 4.1 compatibility not verified | Reject mismatched server/fork | Unverified |
| Windows window capture | Host render/capture path, not source PTS | Additional platform/window/DPI mapping required; not chosen | Scaling/occlusion/letterboxing possible; CLI must use `--no-control` |
| ADB PNG screenshot | Existing read-only diagnostic fallback | Existing ADB/OpenCV; measured separately | Native screenshot; no input |

Primary recording evidence:
https://github.com/Genymobile/scrcpy/blob/v4.1/app/src/recorder.c
https://github.com/Genymobile/scrcpy/blob/v4.1/doc/recording.md

## Time and privacy semantics

Source timestamps are decoded presentation timestamps from device packets.
`received_at` is host monotonic time at complete packet reception, before decode.
Receipt-to-consumption age includes decoding and host scheduling/queue delay,
**not device encoding or wireless transport delay**. Without clock mapping,
end-to-end latency is unknown, never inferred from throughput or first-frame
offset. Source rate, host interarrival jitter and consumer rate are separate.
One decoded frame slot drops replaced frames; it cannot accumulate backlog.
This Windows/Python 3.11 run exposes approximately 15–16 ms host monotonic
clock steps. Small host ages/jitter are consequently quantized, not sub-ms
latency measurements. PTS retains the server's microsecond resolution.

Logs and optional recordings belong under ignored `debug/runtime-live/`.
No serial, wireless endpoint, pairing code, raw hierarchy or UI text is written
by default. Package/activity identifiers, node/candidate counts and coarse bounds
categories are observation evidence. A close/Continue/Restart label does not
establish an ad or game menu. No signature is automatically VERIFIED.

Install the optional live dependencies without changing offline requirements:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-live.txt
.\.venv\Scripts\python.exe tools/run_live_observer.py --serial '<selected-device>' --adb '<adb-executable>' --scrcpy '<scrcpy-4.1-executable>' --duration 60 --report debug/runtime-live/observation.json --log debug/runtime-live/observation.jsonl
.\.venv\Scripts\python.exe tools/benchmark_live_capture.py --serial '<selected-device>' --adb '<adb-executable>' --transport adb --duration 30 --report debug/runtime-live/adb-baseline.json
```

Explicit selection is mandatory. Output paths must be new files inside the
ignored directory. Duration is bounded to 1..600 seconds; JSONL output is capped
at 40,000 records and reports truncation. Timing samples retain the most recent
12,000 frames (UI samples: 1,000); counts/rates cover the complete observation.
There is no flag enabling input. Ctrl+C stops host observation.
The live CLI additionally requires fresh foreground evidence for the known
gameplay activity (`com.unity3d.player.UnityPlayerActivity`, configurable via
`--game-activity`) before valid knife processing. Expected package alone is
insufficient when an ad SDK runs inside the game's package. This tightens runtime
gating without changing the visual PLAYING classifier or offline behavior.

For ADB screenshots, `received_at` is the request start; age includes the PNG
request/transfer/decode. This differs from streaming packet receipt and is
reported as a fallback service-time measurement, not comparable end-to-end
latency. PyAV 18.0.0 is optional because current 19.x requires Python >=3.12.
The diagnostic ADB screenshot timeout is bounded to ten seconds; the default
two-second session timeout was insufficient for full-resolution wireless PNGs.

Android 16's `dumpsys window windows` did not expose focus fields on this S24.
The full window dump does. During capture, scrcpy's virtual display appears
before physical display 0 and has null focus. Foreground parsing is scoped to
physical display 0 and rejects a conflicting top-focused display; it never
selects an arbitrary non-null window from another display. Missing node enabled
attributes remain unknown/disabled evidence, including parent inheritance.

## Measured validation (2026-10-06)

The user manually played for a 600-second observation on Android 16 at native
1080x2340. scrcpy control and all Android interaction were disabled throughout.

| Metric | Measured result |
| --- | --- |
| Produced / consumed frames | 30,855 / 30,354 |
| Source / consumer rate | 51.49 / 50.59 FPS |
| Replaced frames | 501 (bounded latest-frame handoff) |
| Duplicate / out-of-order PTS / stale consumed frames | 0 / 0 / 0 |
| Visual PLAYING / UNKNOWN | 5,887 / 24,467 |
| Target detection rate, including unclassified circles | 32.39% |
| Tracked knife count range | 0..11; completeness uncertain in all PLAYING frames |
| Vision service rate | 133.44 FPS on the latest 12,000 mixed-state frames; not PLAYING-only throughput |
| UI probes / success / failure | 420 / 419 / 1 (foreground changed during query) |
| UI mean / p95 | 421.50 / 563.00 ms |
| Own Python CPU | 348.55% across cores; excludes UI worker and phone |

On the latest 12,000 timing samples: receipt-to-consumption age mean 13.51 ms,
p95 31 ms, maximum 78 ms; observation completion age mean 21.39 ms, p95 32 ms,
maximum 79 ms. Host interarrival mean 19.58 ms, p50 16 ms, p95 32 ms, p99 93 ms,
maximum 5,593 ms. Content/static gaps triggered 28 STOP heartbeat recommendations;
these did not fabricate frames or execute actions. End-to-end latency remains
unmeasured. The 501 dropped frames reflect replacement, not an accumulating queue.

The diagnostic ADB fallback measured 16 screenshots in 31.625 seconds: 0.506
FPS, mean request-to-consumption 1,976.56 ms, p95 2,074.50 ms, maximum 2,157 ms;
all 16 exceeded the one-second freshness threshold. Own Python CPU was 2.37%.
Interarrival mean 1,981.20 ms, p95 2,080 ms. It is unsuitable for gameplay capture.

The final activity guard was additionally exercised in a separate 30-second
non-gameplay window: 1,063 UNKNOWN frames, 35.43 consumer FPS, 18/18 successful
UI probes, no duplicates/order errors/stale frames and no valid knife output.
This window is not evidence of gameplay recall. The ten-minute measurements
precede that additional conservative guard; six frames lacked runtime foreground
confirmation and eleven visual PLAYING frames had cached ad activity evidence.

The original offline regression retained 6,628 frames, 2,209 PLAYING, 4,419
UNKNOWN, 20 transitions, 51.63 PLAYING pipeline FPS and benchmark recall 95.83% /
precision 100% (37 certain annotated frames; not held-out accuracy).
