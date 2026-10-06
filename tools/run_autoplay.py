"""Bounded gameplay-only commissioning/autoplay. Default: no input."""
import argparse
from collections import Counter,deque
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import monotonic,perf_counter
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.android.device import AdbSession,CurrentApp
from src.android.gameplay_input import GameplayInput
from src.android.scrcpy_source import ScrcpyFrameSource
from src.android.ui_worker import LiveUiFactory,UiPollingWorker
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
    parser.add_argument('--max-shots',type=int,default=10)
    parser.add_argument('--enable-input',action='store_true')
    parser.add_argument('--autoplay',action='store_true')
    parser.add_argument('--calibrate',action='store_true')
    parser.add_argument('--calibration',type=Path)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.enable_input!=args.autoplay:parser.error('Both --enable-input and --autoplay required')
    if not 1<=args.duration<=(300 if args.enable_input else 600):parser.error('Invalid bounded duration')
    if not 1<=args.max_shots<=20:parser.error('--max-shots must be 1..20')
    base=(ROOT/'debug/runtime-live').resolve()
    for path in (args.log,args.report,args.calibration):
        if path and not path.resolve().is_relative_to(base):parser.error('Artifacts must be inside ignored debug/runtime-live/')
    for path in (args.log,args.report):
        if path.exists():parser.error('Refusing to overwrite telemetry')
    if args.calibrate and args.calibration and args.calibration.exists():parser.error('Refusing to overwrite calibration')
    print(f'AUTOPLAY_MODE={"CALIBRATION" if args.calibrate else "AUTOPLAY" if args.enable_input else "DRY_RUN"}\n'
          f'ANDROID_INPUT_ENABLED={"YES" if args.enable_input else "NO"}\n'
          'ADS_AUTOMATION=NO\nRESTART_AUTOMATION=NO\nCONTINUE_AUTOMATION=NO',flush=True)
    session=AdbSession(args.serial,adb=args.adb)
    resolution=session.get_resolution()
    calibration=(FlightCalibration.load(args.calibration,resolution)
                 if args.calibration and args.calibration.exists() else FlightCalibration())
    if args.enable_input and not args.calibrate and not calibration.valid:
        parser.error('Valid recent observed flight calibration required for real autoplay')
    source=ui=backend=sink=None
    runtime=BotRuntime(expected_package=PACKAGE,expected_game_activity=ACTIVITY,observation_only=True)
    rotation=RotationEstimator()
    engine=AutoplayEngine(calibration,calibrating=args.calibrate)
    durations=deque(maxlen=12000)
    states=Counter()
    stop='DURATION_LIMIT'
    started=monotonic()
    try:
        source=ScrcpyFrameSource(args.serial,args.scrcpy,adb=args.adb).start()
        ui=UiPollingWorker(LiveUiFactory(args.serial,args.adb)).start()
        backend=GameplayInput(args.serial,args.adb,enable_input=args.enable_input,autoplay=args.autoplay,
                              max_shots=args.max_shots,
                              foreground=lambda:session.get_current_app()==CurrentApp(PACKAGE,ACTIVITY))
        sink=JsonlTelemetry(args.log,limit=40000)
        while monotonic()-started<args.duration:
            delivery=backend.poll()
            if delivery=='FAILED':stop='INPUT_DELIVERY_FAILED';break
            packet=source.read()
            if packet is None:
                if source.exhausted:stop='SOURCE_DISCONNECTED';break
                verdict=runtime.check_without_frame()
                if verdict.recommendation=='STOP':
                    engine.stable_since=None
                    rotation.reset()
                continue
            before=perf_counter()
            snapshot=ui.read()
            record=runtime.process(packet,snapshot)
            states[record['game_state']]+=1
            tracked,raw=runtime.last_tracking,runtime.last_raw
            estimate=rotation.update(tracked,packet.timestamp)
            tip=projectile_tip(packet.frame,record['target']) if record['runtime_state']=='PLAYING' or engine.pending else None
            now=monotonic()
            confirmed_before=engine.metrics['SHOTS_CONFIRMED']
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
            record.update(rotation=asdict(estimate),decision=engine.last_decision,execution=execution,
                          prediction=asdict(engine.last_prediction) if engine.last_prediction else None,
                          host_timestamp=now,frame_age_ms=(now-packet.received_at)*1000)
            sink.write(record)
            if engine.failed:stop='SHOT_CONFIRMATION_FAILED';break
            if args.calibrate and calibration.valid:stop='CALIBRATION_COMPLETE';break
            if backend.sent>=args.max_shots and engine.pending is None:stop='SHOT_LIMIT';break
    except KeyboardInterrupt:stop='HOST_INTERRUPT'
    finally:
        elapsed=monotonic()-started
        if backend:backend.close()
        if source:source.close()
        if ui:ui.close()
        if sink:sink.close()
    if engine.pending:
        engine.metrics['SHOTS_UNCONFIRMED']+=1
        engine.shots[-1]['confirmation_result']='STOPPED_WITHOUT_CONFIRMATION'
    if args.calibrate and args.calibration and calibration.samples:
        args.calibration.parent.mkdir(parents=True,exist_ok=True)
        calibration.save(args.calibration,resolution)
    summary=dict(duration_s=elapsed,stop_reason=stop,metrics=dict(engine.metrics),shots=engine.shots,
                 calibration=calibration.summary(),state_counts=dict(states),frame_time=distribution(durations),
                 vision_fps=len(durations)/sum(durations) if durations else None,
                 capture_frames=source.buffer.frames if source else 0,
                 consumer_fps=sum(states.values())/elapsed,android_actions_executed=bool(backend and backend.sent),
                 input_transport='ADB',impact_angle_deg=180.,geometry_margin_deg=28.)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('x',encoding='utf-8') as output:json.dump(summary,output,indent=2)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    try:main()
    except Exception:
        print('AUTOPLAY_ERROR=observation_or_input_failed; details_redacted')
        raise SystemExit(2) from None
