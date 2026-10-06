"""Offline video inspector. No device connection or gameplay actions."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys
from time import perf_counter

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.states.game_state import GameState, StateStabilizer, classify_game_state
from src.vision.target_detector import detect_target

SAVE_DIR = ROOT / "debug" / "frames"


def annotate(frame, timestamp, target, result, state):
    view = frame.copy()
    if target:
        x, y, radius = target
        cv2.circle(view, (x, y), radius, (255, 255, 255), 3)
        cv2.circle(view, (x, y), 7, (255, 255, 255), -1)
    display = cv2.resize(view, (round(view.shape[1] * 820 / view.shape[0]), 820))
    labels = [f"TIME={timestamp:.2f}s STATE={state.value}",
              f"TARGET={target[:2] if target else 'none'} RADIUS={target[2] if target else '-'}",
              f"GAME_SCORE={result.score:.3f} RAW={result.state.value}",
              "FAIL=" + (','.join(result.failed_checks[:2]) or 'none')
              + (f' (+{len(result.failed_checks) - 2})' if len(result.failed_checks) > 2 else '')]
    cv2.rectangle(display, (0, 0), (display.shape[1], 100), (0, 0, 0), -1)
    for i, label in enumerate(labels):
        cv2.putText(display, label, (6, 20 + i * 23), cv2.FONT_HERSHEY_SIMPLEX,
                    .40, (255, 255, 255), 1, cv2.LINE_AA)
    return display


def seek_to_time(cap, seconds):
    """Seek by sequential timestamps, avoiding inaccurate VFR random seeks.

    Offline scrubbing costs a short decode from the beginning. The next read
    is just after the requested time. Never use nominal frame/FPS as time.
    """
    if not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
        return False
    while cap.grab():
        if cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 >= max(0, seconds):
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', nargs='?', type=Path,
                        default=ROOT / 'debug' / 'treasure-sample-2.mp4')
    parser.add_argument('--headless', action='store_true', help='Analyze every frame without a window')
    parser.add_argument('--report', type=Path, help='Write JSON summary (headless only)')
    parser.add_argument('--csv', type=Path, help='Write per-frame diagnostics (headless only)')
    args = parser.parse_args(argv)
    if (args.report or args.csv) and not args.headless:
        parser.error('--report and --csv require --headless')
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f'Could not open: {args.video}')
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = (int(cap.get(prop)) for prop in
                     (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0:
        cap.release()
        raise SystemExit('Video has invalid FPS')
    duration = total / fps
    print(f'VIDEO={args.video}\nRESOLUTION={width}x{height}\nFPS={fps:.3f}\nFRAMES={total}\nDURATION={duration:.2f}s')
    if not args.headless:
        print('SPACE=pause/play A=back 1s D=forward 1s S=save raw frame Q=quit')
    stabilizer = StateStabilizer()
    counts, raw_counts, failures = Counter(), Counter(), Counter()
    segments = []
    frame = None
    paused = False
    analyzed = 0
    csv_file = None
    started = perf_counter()
    try:
        if args.csv:
            args.csv.parent.mkdir(parents=True, exist_ok=True)
            csv_file = args.csv.open('w', newline='', encoding='utf-8')
            writer = csv.writer(csv_file)
            writer.writerow(['frame', 'time', 'state', 'raw_state', 'target', 'score', 'failed_checks'])
        while True:
            if not paused or frame is None:
                ok, frame = cap.read()
                if not ok:
                    break
                index = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
                timestamp = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                # Always use fresh evidence; never reuse a pre-seek circle.
                target = detect_target(frame)
                result = classify_game_state(frame, target)
                state = stabilizer.update(result)
                analyzed += 1
                counts[state.value] += 1
                raw_counts[result.state.value] += 1
                failures.update(result.failed_checks)
                if segments:
                    segments[-1]['end_s'] = timestamp
                if not segments or segments[-1]['state'] != state.value:
                    segments.append(dict(state=state.value, start_frame=index,
                                         end_frame=index, start_s=timestamp, end_s=timestamp + 1 / fps))
                else:
                    segments[-1].update(end_frame=index, end_s=timestamp + 1 / fps)
                if csv_file:
                    writer.writerow([index, f'{timestamp:.6f}', state.value, result.state.value,
                                     target, f'{result.score:.3f}', ','.join(result.failed_checks)])
            if args.headless:
                continue
            cv2.imshow('Treasure Master - Offline Inspector', annotate(frame, timestamp, target, result, state))
            key = cv2.waitKey(0 if paused else max(1, round(1000 / fps))) & 0xFF
            if key == ord('q'):
                break
            if key == 32:
                paused = not paused
            elif key in (ord('a'), ord('d')):
                seek_time = min(duration - .1, max(0, timestamp + (-1 if key == ord('a') else 1)))
                if seek_to_time(cap, seek_time):
                    frame = None
                    stabilizer.reset()
            elif key == ord('s'):
                SAVE_DIR.mkdir(parents=True, exist_ok=True)
                output = SAVE_DIR / f'frame_{index:06d}_{timestamp:08.2f}.png'
                print(f'SAVED={output}' if cv2.imwrite(str(output), frame) else f'SAVE_FAILED={output}')
    finally:
        elapsed = perf_counter() - started
        cap.release()
        if csv_file:
            csv_file.close()
        if not args.headless:
            cv2.destroyAllWindows()
    short = [s for s in segments if s['state'] == 'PLAYING' and s['end_s'] - s['start_s'] < .20]
    summary = dict(resolution=[width, height], fps=fps, duration_s=duration,
                   expected_frames=total, frames_analyzed=analyzed,
                   complete=bool(args.headless and analyzed == total),
                   state_counts={s.value: counts[s.value] for s in GameState},
                   raw_state_counts=dict(raw_counts), failed_check_counts=dict(failures),
                   state_transitions=max(0, len(segments) - 1),
                   processing_fps=analyzed / elapsed if elapsed else 0,
                   last_decoded_timestamp_s=timestamp if analyzed else None,
                   short_playing_sequences=short, segments=segments)
    print('STATE_COUNTS=' + json.dumps(summary['state_counts']))
    print(f"FRAMES_ANALYZED={analyzed}\nSTATE_TRANSITIONS={summary['state_transitions']}\nPROCESSING_FPS={summary['processing_fps']:.2f}\nSHORT_PLAYING_SEQUENCES={len(short)}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    if args.headless and analyzed != total:
        raise SystemExit(f'Incomplete decode: {analyzed}/{total} frames')
    return summary


if __name__ == '__main__':
    main()
