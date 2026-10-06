"""Benchmark a selected device without any Android application interaction."""
import argparse
import json
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.android.device import AdbSession
from src.android.frame_source import AdbScreenshotSource
from src.android.scrcpy_source import ScrcpyFrameSource
from src.android.ui_worker import LiveUiFactory, UiPollingWorker
from src.runtime.live_observer import observe


def main(observer=False):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial',required=True)
    parser.add_argument('--adb',required=True)
    parser.add_argument('--scrcpy',type=Path)
    parser.add_argument('--transport',choices=('scrcpy','adb'),default='scrcpy')
    parser.add_argument('--duration',type=float,default=60.)
    parser.add_argument('--vision',action='store_true',default=observer)
    parser.add_argument('--ui',action='store_true',default=observer)
    parser.add_argument('--package',default='com.gimica.treasuremaster')
    parser.add_argument('--game-activity',default='com.unity3d.player.UnityPlayerActivity')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--log',type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+',args.package):
        parser.error('Expected package must be an Android package identifier')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.$]*',args.game_activity):
        parser.error('Expected gameplay activity must be a class identifier')
    if not 1<=args.duration<=600:
        parser.error('--duration must be 1..600 seconds')
    for path in (args.report,args.log):
        if path and (not path.resolve().is_relative_to((ROOT/'debug/runtime-live').resolve()) or path.exists()):
            parser.error('Output must be a new file under ignored debug/runtime-live/')
    if args.transport=='scrcpy' and not args.scrcpy:
        parser.error('--scrcpy is required for version-matched streaming')
    print('READ_ONLY_MODE=YES\nANDROID_INPUT_ENABLED=NO\nSCRCPY_CONTROL_ENABLED=NO',flush=True)
    source=None
    ui=None
    try:
        session=AdbSession(args.serial,adb=args.adb,timeout=10. if args.transport=='adb' else 2.)
        # Verify identity/package installation with read-only queries, without
        # printing the selected serial or auto-connecting/selecting another device.
        manufacturer=session._query(['shell','getprop','ro.product.manufacturer']).decode().strip().casefold()
        if manufacturer!='samsung':
            raise RuntimeError('Explicitly selected device is not Samsung')
        if not session._query(['shell','pm','path',args.package]).strip().startswith(b'package:'):
            raise RuntimeError('Expected game package is not installed')
        if args.transport=='scrcpy':
            source=ScrcpyFrameSource(args.serial,args.scrcpy,adb=args.adb).start()
        else:
            source=AdbScreenshotSource(session)
        if args.ui:
            ui=UiPollingWorker(LiveUiFactory(args.serial,args.adb)).start()
        result=observe(source,duration=args.duration,ui=ui,vision=args.vision,
                       expected_package=args.package,expected_game_activity=args.game_activity,log=args.log)
        if args.transport=='adb':
            result['age_definition']='adb_screenshot_request_start_to_host_consumer; not end-to-end latency'
    except KeyboardInterrupt:
        print('OBSERVER_STOPPED=HOST_INTERRUPT')
        return
    except Exception:
        print('OBSERVATION_FAILED=read_only_capture_or_ui_error; details intentionally redacted')
        raise SystemExit(1) from None
    finally:
        if source and not source.exhausted:
            source.close()
        if ui and not ui.closed:
            ui.close()
    print(json.dumps(result,indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        with args.report.open('x',encoding='utf-8') as output:
            json.dump(result,output,indent=2)
    if result['stopped_reason']!='duration_complete' or not result['consumed_frames']:
        raise SystemExit(2)


if __name__=='__main__': main()
