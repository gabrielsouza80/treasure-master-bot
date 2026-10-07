"""Bounded gameplay-only commissioning/autoplay. Default: no input."""
import argparse
from collections import Counter,deque
from contextlib import ExitStack
from dataclasses import asdict
import json
from math import isfinite
from pathlib import Path
import sys
from time import monotonic,perf_counter
import numpy as np
import cv2
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.android.device import AdbSession,CurrentApp
from src.android.gameplay_input import GameplayInput
from src.android.scrcpy_source import ScrcpyFrameSource
from src.android.ui_worker import ForegroundFactory,UiPollingWorker
from src.prediction.flight_calibration import FlightCalibration
from src.prediction.rotation_estimator import RotationEstimator
from src.runtime.autoplay import AutoplayEngine
from src.runtime.bot_runtime import BotRuntime,JsonlTelemetry
from src.runtime.capture_metrics import distribution
from src.vision.projectile import projectile_tip

PACKAGE='com.gimica.treasuremaster'
ACTIVITY='com.unity3d.player.UnityPlayerActivity'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial',required=True)
    parser.add_argument('--adb',required=True)
    parser.add_argument('--scrcpy',required=True)
    parser.add_argument('--duration',type=float,default=120.)
    parser.add_argument('--max-shots',type=int)
    parser.add_argument('--enable-input',action='store_true')
    parser.add_argument('--autoplay',action='store_true')
    parser.add_argument('--calibrate',action='store_true')
    parser.add_argument('--calibration',type=Path)
    parser.add_argument('--calibration-output',type=Path)
    parser.add_argument('--impact-angle-deg',type=float,default=180.)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--review-dir',type=Path)
    args=parser.parse_args()
    if args.max_shots is None:args.max_shots=3 if args.calibrate else 10
    if args.enable_input!=args.autoplay:parser.error('Both --enable-input and --autoplay required')
    if not 1<=args.duration<=(300 if args.enable_input else 600):parser.error('Invalid bounded duration')
    if not 1<=args.max_shots<=20:parser.error('--max-shots must be 1..20')
    if args.calibrate and args.max_shots>3:parser.error('Initial commissioning is limited to 3 shots')
    if not isfinite(args.impact_angle_deg) or not 0<=args.impact_angle_deg<360:
        parser.error('--impact-angle-deg must be finite and in [0,360)')
    base=(ROOT/'debug/runtime-live').resolve()
    for path in (args.log,args.report,args.calibration,args.calibration_output,args.review_dir):
        if path and not path.resolve().is_relative_to(base):parser.error('Artifacts must be inside ignored debug/runtime-live/')
    for path in (args.log,args.report):
        if path.exists():parser.error('Refusing to overwrite telemetry')
    if args.calibrate and not args.calibration_output:
        parser.error('--calibrate requires new --calibration-output')
    if args.calibration_output and args.calibration_output.exists():
        parser.error('Refusing to overwrite calibration output')
    if args.review_dir and args.review_dir.exists():parser.error('Refusing to overwrite review frames')
    print(f'AUTOPLAY_MODE={"CALIBRATION" if args.calibrate else "AUTOPLAY" if args.enable_input else "DRY_RUN"}\n'
          f'ANDROID_INPUT_ENABLED={"YES" if args.enable_input else "NO"}\n'
          f'MODE={"CALIBRATION" if args.calibrate else "AUTOPLAY" if args.enable_input else "DRY_RUN"}\n'
          f'REAL_INPUT_ENABLED={"YES" if args.enable_input else "NO"}\nMAX_SHOTS={args.max_shots}\n'
          'ADS_AUTOMATION=NO\nRESTART_AUTOMATION=NO\nCONTINUE_AUTOMATION=NO',flush=True)
    if args.calibrate and args.enable_input:
        print('USER_ACTION_REQUIRED: Open/return to normal Treasure Master gameplay and STOP TOUCHING THE SCREEN.',flush=True)
    session=AdbSession(args.serial,adb=args.adb)
    resolution=session.get_resolution()
    calibration=(FlightCalibration.load(args.calibration,resolution)
                 if args.calibration and args.calibration.exists() else FlightCalibration())
    if args.enable_input and not args.calibrate and not calibration.valid:
        parser.error('Valid recent observed flight calibration required for real autoplay')
    source=ui=backend=sink=None
    runtime=BotRuntime(expected_package=PACKAGE,expected_game_activity=ACTIVITY,observation_only=True)
    rotation=RotationEstimator()
    engine=AutoplayEngine(calibration,calibrating=args.calibrate,impact_angle=args.impact_angle_deg)
    durations=deque(maxlen=12000)
    states=Counter()
    review=[]
    stop='DURATION_LIMIT'
    started=monotonic()
    try:
        source=ScrcpyFrameSource(args.serial,args.scrcpy,adb=args.adb).start()
        ui=UiPollingWorker(ForegroundFactory(args.serial,args.adb),interval=.1).start()
        def foreground_matches():
            snapshot=ui.read()
            return bool(snapshot and not snapshot.error and snapshot.app==CurrentApp(PACKAGE,ACTIVITY)
                        and 0<=monotonic()-snapshot.observed_at<=.75)
        backend=GameplayInput(args.serial,args.adb,enable_input=args.enable_input,autoplay=args.autoplay,
                              max_shots=args.max_shots,
                              commissioning=args.calibrate,
                              foreground=foreground_matches)
        sink=JsonlTelemetry(args.log,limit=40000)
        while monotonic()-started<args.duration:
            delivery=backend.poll()
            if delivery=='FAILED':stop='INPUT_DELIVERY_FAILED';break
            packet=source.read()
            if packet is None:
                if engine.check_pending_timeout(monotonic()):
                    stop='SHOT_CONFIRMATION_FAILED';break
                if source.exhausted:stop='SOURCE_DISCONNECTED';break
                verdict=runtime.check_without_frame()
                if verdict.recommendation=='STOP':
                    engine.stable_since=None
                    rotation.reset()
                continue
            before=perf_counter()
            if packet.frame.shape[:2]!=(resolution[1],resolution[0]):
                if engine.pending:engine.reject_pending('CAPTURE_GEOMETRY_CHANGED')
                stop='CAPTURE_GEOMETRY_CHANGED';break
            source.buffer.metrics.consumed(packet.received_at,monotonic())
            snapshot=ui.read()
            record=runtime.process(packet,snapshot)
            states[record['game_state']]+=1
            tracked,raw=runtime.last_tracking,runtime.last_raw
            estimate=rotation.update(tracked,packet.timestamp)
            tip=projectile_tip(packet.frame,record['target']) if record['runtime_state']=='PLAYING' or engine.pending else None
            now=monotonic()
            confirmed_before=engine.metrics['SHOTS_CONFIRMED']
            pending_before=engine.pending is not None
            engine.observe_confirmation(record,tracked,packet,tip,now)
            if engine.metrics['SHOTS_CONFIRMED']>confirmed_before:
                backend.acknowledge_confirmed(backend.sent)
            # A transport still running after confirmation also blocks the next tap.
            if backend.outstanding and engine.pending is None and delivery=='DELIVERED' and not engine.failed:
                backend.acknowledge_confirmed(backend.sent)
            permit=engine.decide(record,tracked,raw,estimate,packet,snapshot,now,tip=tip)
            execution=backend.fire(permit,resolution)
            if execution=='SENT':engine.fired(record,tracked,packet,tip,backend.command_at)
            elif execution=='INPUT_FAILED':stop='INPUT_START_FAILED';break
            durations.append(perf_counter()-before)
            record.update(rotation=asdict(estimate),decision=engine.last_decision,
                          wait_detail=engine.last_wait_detail,execution=execution,
                          planned_action='GAMEPLAY_TAP' if permit.allowed else 'WAIT',
                          action_allowed=execution in ('SENT','DRY_RUN'),
                          action_block_reason=(None if execution in ('SENT','DRY_RUN') else
                                               permit.reason if not permit.allowed else 'INPUT_BACKEND_GUARD'),
                          clean_target=asdict(engine.last_clean_evidence) if engine.last_clean_evidence else None,
                          commissioning_state=engine.commissioning_state,
                          commissioning_interval=engine.last_commissioning_interval,
                          input_delivery_status=delivery,
                          zero_quality={k:raw.diagnostics.get(k) for k in
                                        ('zero_perimeter_visible','zero_root_max_support')} if raw else None,
                          foreground_age_ms=(now-snapshot.observed_at)*1000 if snapshot else None,
                          prediction=asdict(engine.last_prediction) if engine.last_prediction else None,
                          host_timestamp=now,frame_age_ms=(now-packet.received_at)*1000)
            sink.write(record)
            if (args.review_dir and len(review)<48 and
                    (not review and record['runtime_state']=='PLAYING' or execution=='SENT' or pending_before or engine.pending)):
                review.append((packet.sequence_number,cv2.resize(packet.frame,(360,780))))
            if engine.failed:stop='SHOT_CONFIRMATION_FAILED';break
            if args.calibrate and engine.metrics['SHOTS_CONFIRMED']>=3:
                stop='CALIBRATION_COMPLETE';break
            if backend.sent>=args.max_shots and engine.pending is None:stop='SHOT_LIMIT';break
    except KeyboardInterrupt:stop='HOST_INTERRUPT'
    finally:
        elapsed=monotonic()-started
        # All host resources close even if one shutdown operation raises.
        with ExitStack() as cleanup:
            for resource in (sink,ui,source,backend):
                if resource:cleanup.callback(resource.close)
    if engine.pending:
        engine.reject_pending('STOPPED_WITHOUT_CONFIRMATION')
    if args.calibrate and args.calibration_output and engine.metrics['CALIBRATION_ACCEPTED']:
        args.calibration_output.parent.mkdir(parents=True,exist_ok=True)
        calibration.save(args.calibration_output,resolution)
    if args.review_dir and review:
        args.review_dir.mkdir(parents=True,exist_ok=False)
        for sequence,frame in review:cv2.imwrite(str(args.review_dir/f'frame-{sequence:06d}.png'),frame)
    summary=dict(duration_s=elapsed,stop_reason=stop,metrics=dict(engine.metrics),shots=engine.shots,
                 calibration=calibration.summary(),state_counts=dict(states),frame_time=distribution(durations),
                 vision_fps=len(durations)/sum(durations) if durations else None,
                 capture_frames=source.buffer.frames if source else 0,
                 consumer_fps=sum(states.values())/elapsed,android_actions_executed=bool(backend and backend.sent),
                 capture=source.buffer.metrics.summary(elapsed,sum(states.values())) if source else None,
                 active_consumer_fps=((sum(states.values())-1)/(source.buffer.metrics.last_received-source.buffer.metrics.first_received)
                                      if source and source.buffer.frames>1
                                      and source.buffer.metrics.last_received>source.buffer.metrics.first_received else None),
                 input_transport='ADB',impact_angle_deg=args.impact_angle_deg,geometry_margin_deg=28.,
                 shot_confirmation=distribution([s['confirmation_latency_ms']/1000 for s in engine.shots
                                                  if s.get('confirmation_result')=='CONFIRMED']),
                 average_wait_between_shots_ms=(float(np.mean(np.diff([s['decision_timestamp'] for s in engine.shots])))*1000
                                                if len(engine.shots)>1 else None),
                 collisions_observed=None,game_over_after_shot=None)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('x',encoding='utf-8') as output:json.dump(summary,output,indent=2)
    print(json.dumps(summary,indent=2))
    if args.calibrate:print(f'FLIGHT_CALIBRATION_VALID={"YES" if calibration.valid else "NO"}')


if __name__=='__main__':
    try:main()
    except Exception:
        print('AUTOPLAY_ERROR=observation_or_input_failed; details_redacted')
        raise SystemExit(2) from None
