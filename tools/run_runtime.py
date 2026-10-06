"""Safe runtime observer. No input execution flags or Android action backend."""
import argparse
from collections import Counter
from pathlib import Path
import sys
from time import monotonic, perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.android.device import AdbSession
from src.android.frame_source import VideoFrameSource, AdbScreenshotSource
from src.android.ui_probe import UiSnapshot
from src.runtime.bot_runtime import BotRuntime, JsonlTelemetry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('video','adb'), default='video')
    parser.add_argument('--video', type=Path, default=ROOT/'debug/treasure-sample-2.mp4')
    parser.add_argument('--serial')
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--package')
    parser.add_argument('--max-frames', type=int, default=0)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if args.max_frames < 0:
        parser.error('--max-frames must be nonnegative')
    if args.log and not args.log.resolve().is_relative_to((ROOT/'debug/runtime').resolve()):
        parser.error('Runtime logs must remain under ignored debug/runtime/')
    if args.source == 'adb' and (not args.serial or not args.package):
        parser.error('ADB observation requires explicit --serial and --package')
    source = None
    sink = JsonlTelemetry(args.log) if args.log else None
    runtime = BotRuntime(expected_package=args.package if args.source == 'adb' else None)
    analyzed, counts = 0, Counter()
    started = perf_counter()
    try:
        if args.source == 'video':
            source = VideoFrameSource(args.video)
        else:
            session = AdbSession(args.serial, adb=args.adb)
            source = AdbScreenshotSource(session)
        limit = args.max_frames or (300 if args.source == 'adb' else 0)
        while not limit or analyzed < limit:
            # Foreground query precedes screenshot; query latency is included.
            observed_at = monotonic()
            snapshot = (UiSnapshot(session.get_current_app(), (), observed_at)
                        if args.source == 'adb' else None)
            packet = source.read()
            if packet is None:
                break
            record = runtime.process(packet, snapshot)
            if sink:
                sink.write(record)
            counts[record['knife_count']] += record['game_state'] == 'PLAYING'
            analyzed += 1
        if args.source == 'video' and source.exhausted and analyzed != source.expected_frames:
            raise RuntimeError(f'Incomplete offline decode: {analyzed}/{source.expected_frames}')
    finally:
        if source:
            source.close()
        if sink:
            sink.close()
    elapsed = perf_counter() - started
    print(dict(frames=analyzed, state_counts=dict(runtime.counts), playing_knife_counts=dict(counts),
               processing_fps=round(analyzed/elapsed,2),
               runtime_overhead_ms=round(1000*runtime.overhead_seconds/max(1,analyzed),4),
               android_actions_executed=False, dry_run=True))


if __name__ == '__main__':
    main()
