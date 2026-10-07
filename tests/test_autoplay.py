import time
import io
import unittest
from unittest.mock import Mock,patch
import numpy as np
from src.android.frame_source import FramePacket
from src.android.device import CurrentApp
from src.android.gameplay_input import GameplayInput,TapPermit
from src.android.ui_probe import UiSnapshot
from src.prediction.flight_calibration import FlightCalibration
from src.prediction.rotation_estimator import RotationEstimator,RotationEstimate
from src.prediction.safe_shot import predict_shot,ShotSafety
from src.runtime.autoplay import AutoplayEngine
from src.vision.knife_detector import KnifeDetectionResult
from src.vision.knife_tracker import KnifeTracker,KnifeTrackingResult,TrackedKnife
from tests.test_knife_detection import observation
from src.states.game_state import GameState


def tracks(angle=0.):
    return KnifeTrackingResult(True,(TrackedKnife(1,angle%360,1.,True),),{'count_uncertain':False})


def rotation(omega=60.,timestamp=10.):
    return RotationEstimate(True,timestamp,omega,0.,'CW' if omega>=0 else 'CCW',1.,10,0.,10.,False,True,'OK')


def calibration():
    c=FlightCalibration()
    for delay in (.14,.15,.16):assert c.add(0.,.04,delay)
    return c


class UncertaintyTests(unittest.TestCase):
    def test_unused_hud_mask_not_uncertain(self):
        tracker=KnifeTracker()
        raw=KnifeDetectionResult(True,observation(0,90).candidates,{'hud_occluded_angles':[180.]})
        for i in range(4):result=tracker.update(raw,(250,320,90),i*.02,state=GameState.PLAYING)
        self.assertFalse(result.diagnostics['count_uncertain'])
        self.assertEqual(result.diagnostics['count_uncertain_reasons'],[])

    def test_missing_occluded_track_and_expiry_are_uncertain(self):
        tracker=KnifeTracker()
        for i in range(3):tracker.update(observation(0,90,180),(250,320,90),i*.02,state=GameState.PLAYING)
        raw=KnifeDetectionResult(True,observation(0,90).candidates,{'hud_occluded_angles':[180.]})
        result=tracker.update(raw,(250,320,90),.06,state=GameState.PLAYING)
        self.assertIn('OCCLUDED_TRACK',result.diagnostics['count_uncertain_reasons'])
        for i in range(4,40): result=tracker.update(raw,(250,320,90),i*.02,state=GameState.PLAYING)
        self.assertIn('COUNT_DISAGREEMENT',result.diagnostics['count_uncertain_reasons'])


class RotationTests(unittest.TestCase):
    def uniform(self,omega,start=350.):
        estimator=RotationEstimator()
        for i in range(16):result=estimator.update(tracks(start+omega*i*.02),i*.02)
        self.assertTrue(result.valid,result)
        self.assertAlmostEqual(result.angular_velocity_deg_s,omega,places=5)
        return estimator,result

    def test_clockwise_and_359_to_1(self):
        _,r=self.uniform(100.,359.)
        self.assertEqual(r.direction,'CW')

    def test_counterclockwise_wrap(self):
        _,r=self.uniform(-100.,1.)
        self.assertEqual(r.direction,'CCW')

    def test_reversal_invalidates_then_rewarms(self):
        estimator,_=self.uniform(100.,0.)
        r=estimator.update(tracks(27.),.32)
        self.assertFalse(r.valid)
        self.assertTrue(r.reversing)
        for i in range(1,12):r=estimator.update(tracks(27-3*i),.32+i*.02)
        self.assertTrue(r.valid,r)
        self.assertAlmostEqual(r.angular_velocity_deg_s,-150.,places=5)

    def test_major_velocity_change_and_gap(self):
        estimator,_=self.uniform(100.,0.)
        r=estimator.update(tracks(38.),.32)
        self.assertFalse(r.valid)
        self.assertEqual(r.reason,'VELOCITY_CHANGE')
        self.assertFalse(estimator.update(tracks(45.),.8).valid)

    def test_acceleration_uses_current_velocity(self):
        estimator=RotationEstimator()
        for i in range(16):
            t=i*.02
            result=estimator.update(tracks(20+60*t+50*t*t),t)
        self.assertTrue(result.valid,result)
        self.assertAlmostEqual(result.angular_velocity_deg_s,90.,places=5)
        self.assertAlmostEqual(result.angular_acceleration_deg_s2,100.,places=5)

    def test_nonfinite_track_invalidates_without_crashing(self):
        estimator,_=self.uniform(100.)
        self.assertFalse(estimator.update(tracks(float('nan')),.32).valid)

    def test_excessive_acceleration_is_not_predictable(self):
        estimator=RotationEstimator()
        for i in range(16):
            t=i*.02
            result=estimator.update(tracks(20+60*t+400*t*t),t)
        self.assertFalse(result.valid)
        self.assertFalse(result.stable)


class PredictionTests(unittest.TestCase):
    def test_safe_unsafe_and_timing_envelope(self):
        kwargs=dict(timestamp=10.,horizon_s=.2,timing_error_s=.1,uncertain=False,impact_sector_visible=True)
        self.assertEqual(predict_shot([0.],rotation(),**kwargs).decision,ShotSafety.SAFE)
        self.assertEqual(predict_shot([168.],rotation(),**kwargs).decision,ShotSafety.UNSAFE)
        self.assertEqual(predict_shot([0.],rotation(),timestamp=10.,horizon_s=.2,timing_error_s=.3,
                                      uncertain=False,impact_sector_visible=True).decision,ShotSafety.UNKNOWN)

    def test_unknown_stale_reversal_uncertain_missing_sector(self):
        for kwargs in (dict(uncertain=True,impact_sector_visible=True),dict(uncertain=False,impact_sector_visible=False)):
            self.assertEqual(predict_shot([0.],rotation(),timestamp=10.,horizon_s=.2,timing_error_s=.1,**kwargs).decision,ShotSafety.UNKNOWN)
        self.assertEqual(predict_shot([0.],rotation(timestamp=9.),timestamp=10.,horizon_s=.2,
                                     timing_error_s=.1,uncertain=False,impact_sector_visible=True).decision,ShotSafety.UNKNOWN)
        self.assertEqual(predict_shot([0.],rotation(),timestamp=10.,horizon_s=.2,timing_error_s=.1,
                                     uncertain=False,impact_sector_visible=True,occluded_angles=[168.]).decision,ShotSafety.UNKNOWN)

    def test_flight_samples_validate_and_expire(self):
        c=calibration()
        self.assertTrue(c.valid)
        self.assertAlmostEqual(c.median_s,.15)
        self.assertFalse(c.add(0.,.2,.1))
        c.measured_at=time.time()-601
        self.assertFalse(c.valid)

    def test_nonfinite_occlusion_cannot_look_clear(self):
        result=predict_shot([0.],rotation(),timestamp=10.,horizon_s=.2,timing_error_s=.1,
                            uncertain=False,impact_sector_visible=True,occluded_angles=[float('nan')])
        self.assertEqual(result.decision,ShotSafety.UNKNOWN)


class CliTests(unittest.TestCase):
    def test_real_calibration_without_seed_fails_before_capture_or_input(self):
        from tools import run_autoplay
        args=['run_autoplay','--serial','mock','--adb','adb','--scrcpy','scrcpy',
              '--enable-input','--autoplay','--calibrate',
              '--calibration','debug/runtime-live/test-missing-seed.json',
              '--calibration-output','debug/runtime-live/test-new-profile.json',
              '--log','debug/runtime-live/test-new-log.jsonl',
              '--report','debug/runtime-live/test-new-report.json']
        with patch('sys.argv',args),patch.object(run_autoplay,'AdbSession') as session,\
                patch.object(run_autoplay,'ScrcpyFrameSource') as source,\
                patch.object(run_autoplay,'GameplayInput') as backend,\
                patch.object(run_autoplay.Path,'exists',return_value=False),\
                patch('sys.stdout'),patch('sys.stderr',new_callable=io.StringIO) as stderr:
            session.return_value.get_resolution.return_value=(1080,2340)
            with self.assertRaises(SystemExit) as error:run_autoplay.main()
            self.assertEqual(error.exception.code,2)
            self.assertIn('Valid recent observed flight calibration required',stderr.getvalue())
            source.assert_not_called()
            backend.assert_not_called()


class InputTests(unittest.TestCase):
    def test_default_dry_run_and_both_flags_required(self):
        runner=Mock()
        io=GameplayInput('mock','adb',clock=lambda:10.,popen=runner)
        self.assertEqual(io.fire(TapPermit(True,10.,1,'SAFE'),(1080,2340)),'DRY_RUN')
        runner.assert_not_called()
        for enable,auto in ((True,False),(False,True)):
            with self.assertRaises(ValueError): GameplayInput('mock','adb',enable_input=enable,autoplay=auto)

    def test_only_one_gameplay_tap_until_visual_ack(self):
        process=Mock();process.poll.return_value=0
        runner=Mock(return_value=process)
        io=GameplayInput('mock','adb',enable_input=True,autoplay=True,clock=lambda:10.,popen=runner,foreground=lambda:True)
        self.assertEqual(io.fire(TapPermit(True,10.,1,'SAFE'),(1080,2340)),'SENT')
        self.assertEqual(runner.call_args.args[0][-5:],['shell','input','tap','540','1824'])
        self.assertEqual(io.poll(),'DELIVERED')
        self.assertEqual(io.fire(TapPermit(True,10.,2,'SAFE'),(1080,2340)),'BLOCKED')
        self.assertEqual(runner.call_count,1)
        io.acknowledge_confirmed(1)
        self.assertFalse(io.outstanding)
        for name in ('back','swipe','launch_package','close_ad','restart','continue_game'):
            self.assertFalse(hasattr(io,name))

    def test_mismatch_stale_and_zone_block(self):
        runner=Mock()
        io=GameplayInput('mock','adb',enable_input=True,autoplay=True,clock=lambda:10.,popen=runner,foreground=lambda:False)
        for p,point in ((TapPermit(True,9.,1,'SAFE'),(.5,.78)),(TapPermit(True,10.,1,'SAFE'),(.1,.1)),
                        (TapPermit(True,10.,1,'SAFE'),(.5,.78))):
            self.assertEqual(io.fire(p,(1080,2340),point),'BLOCKED')
        runner.assert_not_called()


class EngineTests(unittest.TestCase):
    def fixture(self):
        engine=AutoplayEngine(calibration())
        record=dict(runtime_state='PLAYING',watchdog_status='WAIT',target=(540,866,265),knife_count_uncertain=False)
        packet=FramePacket(np.zeros((2,2,3),np.uint8),10.,1,10.)
        snapshot=UiSnapshot(CurrentApp('com.gimica.treasuremaster','com.unity3d.player.UnityPlayerActivity'),(),10.)
        engine.last_target=record['target'];engine.stable_since=9.
        raw=KnifeDetectionResult(True,observation(0.).candidates,{})
        return engine,record,packet,snapshot,raw

    def test_positive_gate_and_negative_states_uncertainty(self):
        engine,record,p,s,raw=self.fixture()
        self.assertTrue(engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.).allowed)
        for state in ('UNKNOWN','ADVERTISEMENT','CONTINUE','GAME_OVER','STALLED'):
            engine,record,p,s,raw=self.fixture();record['runtime_state']=state
            self.assertFalse(engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.).allowed)
        engine,record,p,s,raw=self.fixture();record['knife_count_uncertain']=True
        self.assertFalse(engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.).allowed)

    def test_pending_blocks_and_timeout_latches_stop(self):
        engine,record,p,s,raw=self.fixture()
        engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.)
        engine.fired(record,tracks(),p,1800.,10.)
        self.assertFalse(engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.).allowed)
        engine.observe_confirmation(record,tracks(),p,1700.,11.3)
        self.assertTrue(engine.failed)
        self.assertEqual(engine.metrics['SHOTS_UNCONFIRMED'],1)
        self.assertFalse(engine.decide(record,tracks(),raw,rotation(),p,s,11.3,tip=1800.).allowed)

    def test_command_return_or_count_alone_not_confirmation(self):
        engine,record,p,s,raw=self.fixture()
        engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.)
        engine.fired(record,tracks(),p,1800.,10.)
        p=FramePacket(p.frame,10.1,2,10.1)
        more=KnifeTrackingResult(True,(TrackedKnife(1,0.,1.,True),TrackedKnife(2,180.,1.,True)),{})
        engine.observe_confirmation(record,more,p,1800.,10.1)
        self.assertIsNotNone(engine.pending)
        self.assertEqual(engine.metrics['SHOTS_CONFIRMED'],0)

    def test_calibration_mode_cannot_bypass_missing_measurements(self):
        for calibrating in (False,True):
            engine,record,p,s,raw=self.fixture()
            engine.calibration=FlightCalibration()
            engine.calibrating=calibrating
            permit=engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.)
            self.assertFalse(permit.allowed)
            self.assertEqual(permit.reason,'WAIT_CALIBRATION')

    def test_app_mismatch_and_invalid_tracks_block_independently(self):
        for app in (CurrentApp('browser','activity'),CurrentApp('com.gimica.treasuremaster','adActivity')):
            engine,record,p,s,raw=self.fixture()
            snapshot=UiSnapshot(app,(),10.)
            self.assertFalse(engine.decide(record,tracks(),raw,rotation(),p,snapshot,10.,tip=1800.).allowed)
        engine,record,p,s,raw=self.fixture()
        self.assertFalse(engine.decide(record,KnifeTrackingResult(False),raw,rotation(),p,s,10.,tip=1800.).allowed)

    def test_movement_new_birth_and_two_stable_frames_confirm(self):
        engine,record,p,s,raw=self.fixture()
        engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.)
        engine.fired(record,tracks(),p,1800.,10.)
        existing=TrackedKnife(1,12.,1.,True)
        birth=TrackedKnife(2,180.,1.,True)
        def observe(t,result,tip):
            packet=FramePacket(p.frame,t,round(t*100),t)
            engine.observe_confirmation(record,result,packet,tip,t)
        observe(10.05,tracks(),1700.)
        observe(10.15,KnifeTrackingResult(True,(existing,),{},(birth,)),None)
        stable=KnifeTrackingResult(True,(existing,birth),{})
        observe(10.17,stable,None)
        self.assertIsNotNone(engine.pending)
        observe(10.19,stable,None)
        self.assertIsNone(engine.pending)
        self.assertFalse(engine.failed)
        self.assertEqual(engine.metrics['SHOTS_CONFIRMED'],1)
        self.assertAlmostEqual(engine.shots[-1]['confirmation_latency_ms'],190.)
        self.assertAlmostEqual(engine.shots[-1]['impact_prediction_dt_ms'],190.)

    def test_tracking_reset_cannot_confirm_with_recycled_identifiers(self):
        engine,record,p,s,raw=self.fixture()
        engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.)
        engine.fired(record,tracks(),p,1800.,10.)
        record['runtime_state']='UNKNOWN'
        packet=FramePacket(p.frame,10.05,2,10.05)
        engine.observe_confirmation(record,KnifeTrackingResult(False),packet,None,10.05)
        self.assertTrue(engine.failed)
        self.assertIsNone(engine.pending)
        self.assertEqual(engine.metrics['SHOTS_CONFIRMED'],0)
        self.assertEqual(engine.shots[-1]['confirmation_result'],'CONFIRMATION_INTERRUPTED')
        record['runtime_state']='PLAYING'
        self.assertFalse(engine.decide(record,tracks(),raw,rotation(timestamp=10.05),packet,s,10.05,tip=1800.).allowed)

    def test_confirmation_deadline_runs_without_fresh_frames(self):
        engine,record,p,s,raw=self.fixture()
        engine.decide(record,tracks(),raw,rotation(),p,s,10.,tip=1800.)
        engine.fired(record,tracks(),p,1800.,10.)
        self.assertFalse(engine.check_pending_timeout(11.19))
        self.assertTrue(engine.check_pending_timeout(11.21))
        self.assertTrue(engine.failed)
        self.assertEqual(engine.metrics['SHOTS_UNCONFIRMED'],1)
        self.assertFalse(engine.check_pending_timeout(11.5))
        self.assertEqual(engine.metrics['SHOTS_UNCONFIRMED'],1)


if __name__=='__main__':unittest.main()
