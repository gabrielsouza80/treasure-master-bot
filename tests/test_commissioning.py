"""Commissioning negatives validate reasons, not merely a blocked boolean."""
from dataclasses import replace
import unittest
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
import numpy as np

from src.android.device import CurrentApp
from src.android.frame_source import FramePacket
from src.android.gameplay_input import GameplayInput,TapPermit,CalibrationTapPermit
from src.android.ui_probe import UiSnapshot
from src.android.ui_worker import ForegroundProbe
from src.prediction.commissioning import CleanTargetObserver,predict_commissioning
from src.prediction.flight_calibration import FlightCalibration
from src.prediction.safe_shot import ShotPrediction,ShotSafety
from src.runtime.autoplay import AutoplayEngine
from src.vision.knife_detector import KnifeDetectionResult
from src.vision.knife_detector import detect_knives
from src.states.game_state import GameState
from tests.test_knife_detection import synthetic
from src.vision.knife_tracker import KnifeTrackingResult,TrackedKnife
from tests.test_autoplay import rotation,tracks,calibration

APP=CurrentApp('com.gimica.treasuremaster','com.unity3d.player.UnityPlayerActivity')


def clean_inputs(t=10.,sequence=0):
    frame=np.zeros((2340,1080,3),np.uint8)
    record=dict(runtime_state='PLAYING',watchdog_status='WAIT',target=(540,866,265),knife_count_uncertain=True)
    raw=KnifeDetectionResult(True,(),dict(zero_evidence_version=1,zero_perimeter_visible=True,
                                         zero_root_max_support=.2,hud_occluded_angles=[90.]))
    tracked=KnifeTrackingResult(True,(),dict(count_uncertain=True,count_uncertain_reasons=['NO_CONFIRMED_TRACKS']))
    return record,tracked,raw,FramePacket(frame,t,sequence,t),UiSnapshot(APP,(),t)


class CleanZeroTests(unittest.TestCase):
    def test_real_negative_quality_rejects_dark_knife_and_clipped_perimeter(self):
        frame,target=synthetic()
        empty=detect_knives(frame,target,state=GameState.PLAYING)
        self.assertTrue(empty.diagnostics['zero_perimeter_visible'])
        self.assertLess(empty.diagnostics['zero_root_max_support'],.50)
        frame,target=synthetic((180,),color=(50,50,50))
        dark=detect_knives(frame,target,state=GameState.PLAYING)
        self.assertEqual(dark.count,0)  # Normal luminance gate misses this dark shaft.
        self.assertGreaterEqual(dark.diagnostics['zero_root_max_support'],.50)
        clipped=detect_knives(frame,(15,15,90),state=GameState.PLAYING)
        self.assertFalse(clipped.diagnostics['zero_perimeter_visible'])

    def warmed(self,observer=None,change=None):
        observer=observer or CleanTargetObserver(APP)
        for i in range(20):
            args=list(clean_inputs(10+i*.02,i))
            if change:change(args)
            evidence=observer.update(*args,10+i*.02,1800.)
        return observer,evidence

    def test_true_zero_requires_consecutive_frames_and_duration(self):
        observer=CleanTargetObserver(APP)
        args=clean_inputs()
        self.assertFalse(observer.update(*args,10.,1800.).true_zero)
        _,result=self.warmed(observer)
        self.assertTrue(result.true_zero,result)
        self.assertGreaterEqual(result.consecutive_frames,12)
        self.assertGreaterEqual(result.stable_seconds,.35)

    def test_invalid_detector_is_unknown_zero(self):
        _,r=self.warmed(change=lambda a:a.__setitem__(2,KnifeDetectionResult(False)))
        self.assertFalse(r.true_zero)
        self.assertIn('INVALID_DETECTION',r.reasons)

    def test_missing_quality_occluded_perimeter_and_dark_root_structure(self):
        for quality in ({},dict(zero_evidence_version=1,zero_perimeter_visible=False,zero_root_max_support=0.),
                        dict(zero_evidence_version=1,zero_perimeter_visible=True,zero_root_max_support=.8)):
            _,r=self.warmed(change=lambda a:a.__setitem__(2,KnifeDetectionResult(True,(),quality)))
            self.assertFalse(r.true_zero)
            self.assertIn('PERIMETER_UNKNOWN_OR_RADIAL_STRUCTURE',r.reasons)

    def test_probable_and_held_tracks_block(self):
        knife=TrackedKnife(1,0.,1.,False)
        for tracked in (KnifeTrackingResult(True,(),{'count_uncertain_reasons':['NO_CONFIRMED_TRACKS']},(knife,)),
                        KnifeTrackingResult(True,(knife,),{'count_uncertain_reasons':['HELD_TRACK']})):
            _,r=self.warmed(change=lambda a:a.__setitem__(1,tracked))
            self.assertFalse(r.true_zero)
            self.assertIn('TRACK_PRESENT',r.reasons)

    def test_expired_history_survives_tracking_reset(self):
        observer=CleanTargetObserver(APP)
        args=list(clean_inputs())
        args[1]=KnifeTrackingResult(True,(),{'expired_confirmed_tracks':[{'identifier':1}],
                                             'count_uncertain_reasons':['EXPIRED_TRACK']})
        observer.update(*args,10.,1800.)
        _,r=self.warmed(observer)
        self.assertFalse(r.true_zero)
        self.assertIn('PRIOR_KNIFE_EVIDENCE',r.reasons)

    def test_rejected_one_frame_candidate_does_not_latch_known_knife_history(self):
        observer=CleanTargetObserver(APP)
        args=list(clean_inputs())
        args[1]=KnifeTrackingResult(True,(),{'count_uncertain_reasons':['PENDING_CANDIDATE']},
                                   (TrackedKnife(1,90.,.6,True),))
        self.assertFalse(observer.update(*args,10.,1800.).true_zero)
        _,result=self.warmed(observer)
        self.assertTrue(result.true_zero,result)

    def test_close_decorative_gem_needs_visible_outer_negative_evidence(self):
        import cv2
        frame,target=synthetic()
        cv2.fillConvexPoly(frame,np.array([[335,300],[365,320],[335,340],[325,320]]),(220,80,180))
        result=detect_knives(frame,target,state=GameState.PLAYING)
        self.assertEqual(result.count,0)
        self.assertTrue(result.diagnostics['zero_perimeter_visible'])
        self.assertLess(result.diagnostics['zero_root_max_support'],.50)

    def test_every_other_uncertainty_blocks(self):
        for reason in ('PENDING_CANDIDATE','COUNT_DISAGREEMENT','EXPIRED_TRACK','TARGET_UNSTABLE','OTHER'):
            _,r=self.warmed(change=lambda a:a[1].diagnostics.update(count_uncertain_reasons=['NO_CONFIRMED_TRACKS',reason]))
            self.assertFalse(r.true_zero)
            self.assertIn('OTHER_UNCERTAINTY',r.reasons)

    def test_uncertainty_without_explanation_is_not_the_zero_exception(self):
        _,result=self.warmed(change=lambda a:a[1].diagnostics.update(count_uncertain_reasons=[]))
        self.assertFalse(result.true_zero)
        self.assertIn('OTHER_UNCERTAINTY',result.reasons)

    def test_anchor_drift_resets_stability_and_duplicate_does_not_warm(self):
        observer,_=self.warmed()
        args=list(clean_inputs(10.4,21));args[0]['target']=(555,866,265)
        r=observer.update(*args,10.4,1800.)
        self.assertIn('TARGET_UNSTABLE',r.reasons)
        self.assertFalse(r.true_zero)
        observer=CleanTargetObserver(APP)
        for _ in range(30):r=observer.update(*clean_inputs(),10.,1800.)
        self.assertFalse(r.true_zero)

    def test_invalid_geometry_and_occlusion_are_unknown(self):
        for target in ((540,866,0),(float('nan'),866,265)):
            _,result=self.warmed(change=lambda a:a[0].update(target=target))
            self.assertFalse(result.true_zero)
            self.assertIn('INVALID_DETECTION',result.reasons)
        _,result=self.warmed(change=lambda a:a[2].diagnostics.update(hud_occluded_angles=[float('nan')]))
        self.assertFalse(result.true_zero)
        self.assertIn('IMPACT_OCCLUDED',result.reasons)


class CommissioningEngineTests(unittest.TestCase):
    def commission(self):
        engine=AutoplayEngine(FlightCalibration(),calibrating=True)
        for i in range(20):
            record,tracked,raw,p,s=clean_inputs(10+i*.02,i)
            permit=engine.decide(record,tracked,raw,rotation(timestamp=p.timestamp),p,s,p.received_at,tip=1800.)
        return engine,record,tracked,raw,p,s,permit

    def test_first_shot_has_special_permit_without_any_timing_prediction(self):
        engine,record,tr,raw,p,s,permit=self.commission()
        self.assertIsInstance(permit,CalibrationTapPermit)
        self.assertTrue(permit.allowed)
        self.assertEqual(permit.purpose,'TRUE_ZERO')
        self.assertIsNone(engine.last_prediction)
        self.assertIsNone(engine.prediction_horizon_s)
        self.assertEqual(engine.calibration.samples,[])
        normal=AutoplayEngine(FlightCalibration())
        self.assertFalse(normal.decide(record,tr,raw,rotation(),p,s,p.received_at,tip=1800.).allowed)

    def test_zero_shot_waits_for_movement_birth_and_stable_confirmation(self):
        engine,record,tr,raw,p,s,permit=self.commission()
        command=p.received_at
        engine.fired(record,tr,p,1800.,command)
        self.assertFalse(engine.decide(record,tr,raw,rotation(),p,s,command,tip=1800.).allowed)
        def observe(dt,result,tip,uncertain):
            packet=replace(p,timestamp=p.timestamp+dt,received_at=command+dt,sequence_number=p.sequence_number+round(dt*1000))
            record['knife_count_uncertain']=uncertain
            engine.observe_confirmation(record,result,packet,tip,command+dt)
        observe(.05,tr,1700.,True)
        birth=TrackedKnife(1,180.,1.,True)
        observe(.15,KnifeTrackingResult(True,(),{},(birth,)),None,True)
        self.assertEqual(len(engine.calibration.samples),0)
        stable=KnifeTrackingResult(True,(birth,),{'count_uncertain':False})
        observe(.17,stable,None,False)
        self.assertEqual(len(engine.calibration.samples),0)
        observe(.19,stable,None,False)
        self.assertEqual(len(engine.calibration.samples),1)
        self.assertFalse(engine.calibration.valid)
        self.assertEqual(engine.metrics['CALIBRATION_ACCEPTED'],1)
        self.assertAlmostEqual(engine.calibration.samples[0]['total_s'],.15)
        self.assertAlmostEqual(engine.calibration.samples[0]['confirmation_s'],.19)

    def test_no_movement_rejects_sample_and_timeout_latches(self):
        engine,record,tr,raw,p,s,_=self.commission()
        engine.fired(record,tr,p,1800.,p.received_at)
        birth=TrackedKnife(1,180.,1.,True)
        new=replace(p,timestamp=p.timestamp+.15,received_at=p.received_at+.15)
        engine.observe_confirmation(record,KnifeTrackingResult(True,(),{},(birth,)),new,1800.,new.received_at)
        self.assertEqual(engine.calibration.samples,[])
        engine.check_pending_timeout(p.received_at+1.21)
        self.assertTrue(engine.failed)
        self.assertEqual(engine.metrics['CALIBRATION_REJECTED'],1)

    def test_multiple_new_tracks_are_ambiguous(self):
        engine,record,tr,raw,p,s,_=self.commission()
        engine.fired(record,tr,p,1800.,p.received_at)
        new=replace(p,timestamp=p.timestamp+.15,received_at=p.received_at+.15)
        candidates=(TrackedKnife(1,180.,1.,True),TrackedKnife(2,190.,1.,True))
        engine.observe_confirmation(record,KnifeTrackingResult(True,(),{},candidates),new,1700.,new.received_at)
        self.assertTrue(engine.failed)
        self.assertEqual(engine.metrics['CALIBRATION_REJECTED'],1)
        self.assertEqual(engine.calibration.samples,[])

    def test_valid_profile_stops_commissioning_instead_of_switching_to_autoplay(self):
        engine=AutoplayEngine(calibration(),calibrating=True)
        record,tr,raw,p,s=clean_inputs()
        self.assertEqual(engine.decide(record,tr,raw,rotation(),p,s,10.,tip=1800.).reason,'CALIBRATION_COMPLETE')

    def test_any_unsafe_horizon_blocks_despite_clear_endpoints(self):
        c=FlightCalibration();c.add(0.,.04,.15)
        calls=[]
        def predictor(*args,**kwargs):
            calls.append(kwargs['horizon_s'])
            decision=ShotSafety.UNSAFE if .1<kwargs['horizon_s']<.11 else ShotSafety.SAFE
            return ShotPrediction(decision,'test',150.,40.)
        with patch('src.prediction.commissioning.predict_shot',side_effect=predictor):
            result,interval=predict_commissioning([0.],rotation(),c,timestamp=10.)
        self.assertEqual(result.decision,ShotSafety.UNSAFE)
        self.assertGreater(len(calls),40)
        self.assertAlmostEqual(calls[0],interval['lower_s'])
        self.assertAlmostEqual(calls[-1],interval['upper_s'])

    def test_empty_or_stale_samples_never_create_an_envelope(self):
        for c in (FlightCalibration(),FlightCalibration([{'total_s':.15}],0.)):
            result,interval=predict_commissioning([0.],rotation(),c,timestamp=10.)
            self.assertEqual(result.decision,ShotSafety.UNKNOWN)
            self.assertIsNone(interval)


class CommissioningInputTests(unittest.TestCase):
    def test_cli_rejects_more_than_three_before_device_io(self):
        from tools import run_autoplay
        argv=['run_autoplay','--serial','mock','--adb','adb','--scrcpy','scrcpy',
              '--calibrate','--enable-input','--autoplay','--max-shots','4',
              '--calibration-output','debug/runtime-live/new-profile.json',
              '--log','debug/runtime-live/new-log.jsonl','--report','debug/runtime-live/new-report.json']
        with patch('sys.argv',argv),patch.object(run_autoplay,'AdbSession') as session,\
                patch('sys.stderr',new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit) as error:run_autoplay.main()
            self.assertEqual(error.exception.code,2)
            self.assertIn('limited to 3 shots',stderr.getvalue())
            session.assert_not_called()

    def test_profile_roundtrip_retains_confirmation_and_refuses_overwrite(self):
        c=FlightCalibration()
        for t in (.14,.15,.16):self.assertTrue(c.add(0.,.04,t,.20))
        with TemporaryDirectory() as directory:
            path=Path(directory)/'profile.json'
            c.save(path,(1080,2340))
            original=path.read_bytes()
            loaded=FlightCalibration.load(path,(1080,2340))
            self.assertTrue(loaded.valid)
            self.assertEqual(loaded.samples,c.samples)
            with self.assertRaises(FileExistsError):c.save(path,(1080,2340))
            self.assertEqual(path.read_bytes(),original)
            with self.assertRaisesRegex(ValueError,'geometry mismatch'):FlightCalibration.load(path,(720,1560))

    def test_foreground_probe_is_read_only_and_timestamps_before_query(self):
        session=Mock()
        session.get_current_app.return_value=APP
        probe=ForegroundProbe(session,clock=lambda:10.)
        snapshot=probe.read()
        self.assertEqual(snapshot.app,APP)
        self.assertEqual(snapshot.observed_at,10.)
        self.assertEqual(snapshot.nodes,())
        self.assertEqual(session.mock_calls,[unittest.mock.call.get_current_app()])
        session.get_current_app.side_effect=RuntimeError('private endpoint')
        failed=probe.read()
        self.assertEqual(failed.error,'foreground_query_failed')
        self.assertEqual(failed.app,CurrentApp())

    def test_only_explicit_mode_accepts_typed_permit_and_dry_run_never_taps(self):
        permit=CalibrationTapPermit(True,10.,1,'COMMISSION_TRUE_ZERO','TRUE_ZERO')
        runner=Mock()
        for enabled,commissioning in ((False,True),(True,False)):
            backend=GameplayInput('mock','adb',enable_input=enabled,autoplay=enabled,commissioning=commissioning,
                                  max_shots=3,clock=lambda:10.,popen=runner,foreground=lambda:True)
            self.assertEqual(backend.fire(permit,(1080,2340)),'DRY_RUN' if not enabled else 'BLOCKED')
        runner.assert_not_called()
        normal=GameplayInput('mock','adb',enable_input=True,autoplay=True,clock=lambda:10.,
                             popen=runner,foreground=lambda:True)
        forged=CalibrationTapPermit(True,10.,1,'SAFE','TRUE_ZERO')
        self.assertEqual(normal.fire(forged,(1080,2340)),'BLOCKED')
        runner.assert_not_called()
        with self.assertRaises(ValueError):GameplayInput('mock','adb',commissioning=True,max_shots=4)

    def test_string_reason_cannot_forge_calibration_permit_and_three_shot_limit(self):
        process=Mock();process.poll.return_value=0
        runner=Mock(return_value=process)
        io=GameplayInput('mock','adb',enable_input=True,autoplay=True,commissioning=True,max_shots=3,
                         clock=lambda:10.,popen=runner,foreground=lambda:True)
        self.assertEqual(io.fire(TapPermit(True,10.,1,'COMMISSION_TRUE_ZERO'),(1080,2340)),'BLOCKED')
        for i in range(1,4):
            self.assertEqual(io.fire(CalibrationTapPermit(True,10.,i,'COMMISSION_TRUE_ZERO','TRUE_ZERO'),(1080,2340)),'SENT')
            io.poll();io.acknowledge_confirmed(i)
        self.assertEqual(io.fire(CalibrationTapPermit(True,10.,4,'COMMISSION_TRUE_ZERO','TRUE_ZERO'),(1080,2340)),'BLOCKED')
        self.assertEqual(runner.call_count,3)


class RecordedCleanTargetTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1]/'debug/treasure-sample-2.mp4').exists(),
                         'Local recording unavailable')
    def test_original_clean_gameplay_has_positive_zero_evidence(self):
        import cv2
        from src.runtime.bot_runtime import BotRuntime
        path=Path(__file__).resolve().parents[1]/'debug/treasure-sample-2.mp4'
        capture=cv2.VideoCapture(str(path))
        current=[0.]
        runtime=BotRuntime(clock=lambda:current[0],expected_package=APP.package,
                           expected_game_activity=APP.activity,observation_only=True)
        observer=CleanTargetObserver(APP)
        positives=0
        try:
            for _ in range(397):self.assertTrue(capture.grab())
            for index in range(397,480):
                ok,frame=capture.read()
                self.assertTrue(ok)
                current[0]=capture.get(cv2.CAP_PROP_POS_MSEC)/1000
                packet=FramePacket(frame,current[0],index,current[0])
                snapshot=UiSnapshot(APP,(),current[0])  # Offline foreground fixture only.
                record=runtime.process(packet,snapshot)
                from src.vision.projectile import projectile_tip
                evidence=observer.update(record,runtime.last_tracking,runtime.last_raw,packet,
                                         snapshot,current[0],projectile_tip(frame,record['target']))
                if evidence.true_zero:
                    positives+=1
                    self.assertEqual(runtime.last_raw.count,0)
                    self.assertEqual(runtime.last_tracking.count,0)
                    self.assertEqual(record['runtime_state'],'PLAYING')
            self.assertGreater(positives,0)
        finally:capture.release()


if __name__=='__main__':unittest.main()
