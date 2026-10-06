import unittest
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
import cv2
from src.android.device import AdbSession, CurrentApp, parse_current_app
from src.android.frame_source import LatestFrameSource, FramePacket, AdbScreenshotSource, VideoFrameSource
from src.android.input_controller import ActionGuard, InputController, ActionPlan, ActionKind, GuardContext, scale_point
from src.android.ui_probe import UiSnapshot, UiProbe, parse_ui, find_candidates
from src.runtime.watchdog import Watchdog
from src.runtime.bot_runtime import BotRuntime, JsonlTelemetry
from src.recovery.planner import plan_recovery
from src.states.runtime_state import RuntimeState, infer_runtime_state
from src.states.game_state import GameState


def snapshot(label='Close', **attrs):
    values = {'text':label, 'package':'game', 'enabled':'true', 'clickable':'true',
              'bounds':'[900,10][1000,100]', **attrs}
    xml = '<hierarchy><node ' + ' '.join(f'{k}="{v}"' for k,v in values.items()) + '/></hierarchy>'
    return UiSnapshot(CurrentApp('game','AdActivity'),parse_ui(xml),10.)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.guard = ActionGuard(allowed_zones=((.4,.8,.6,1.),), forbidden_zones=((0,0,.1,.1),))
        self.context = GuardContext(RuntimeState.PLAYING,7,True,True,1.,False)
        self.plan = ActionPlan(ActionKind.GAMEPLAY_TAP,(.5,.9),authorized=True,frame_sequence=7)

    def test_dry_run_has_no_device_io_even_when_allowed(self):
        with patch('subprocess.run') as io:
            controller = InputController(self.guard)
            result = controller.submit(self.plan,self.context)
            self.assertTrue(result['action_allowed'])
            self.assertEqual(result['execution'],'DRY_RUN')
            controller.back(replace(self.context,state=RuntimeState.ADVERTISEMENT,recovery_verified=True),authorized=True)
            controller.launch_package('game',replace(self.context,state=RuntimeState.ADVERTISEMENT,recovery_verified=True),authorized=True)
            io.assert_not_called()
        with self.assertRaises(ValueError): InputController(dry_run=False)

    def test_nonplaying_and_uncertainty_block(self):
        for state in (RuntimeState.UNKNOWN,RuntimeState.ADVERTISEMENT,RuntimeState.STALLED):
            self.assertEqual(self.guard.evaluate(self.plan,replace(self.context,state=state)),(False,'not_playing'))
        for context in (replace(self.context,fresh=False),replace(self.context,foreground_verified=False),
                        replace(self.context,knife_count_uncertain=True),replace(self.context,confidence=float('nan'))):
            self.assertFalse(self.guard.evaluate(self.plan,context)[0])

    def test_authorization_sequence_and_zones(self):
        for plan in (replace(self.plan,authorized=False),replace(self.plan,frame_sequence=6),
                     replace(self.plan,point=(.05,.05)),replace(self.plan,point=(.9,.9)),
                     replace(self.plan,point=(float('nan'),.9))):
            self.assertFalse(self.guard.evaluate(plan,self.context)[0])
        self.assertFalse(ActionGuard().evaluate(self.plan,self.context)[0])

    def test_scaling(self):
        self.assertEqual(scale_point((0,0),1080,2340),(0,0))
        self.assertEqual(scale_point((1,1),1080,2340),(1079,2339))
        self.assertEqual(scale_point((.5,.5),101,201),(50,100))
        for p in ((-1,0),(0,1.1),(float('inf'),0)):
            with self.assertRaises(ValueError): scale_point(p,100,200)


class UiTests(unittest.TestCase):
    def test_labels_and_negative(self):
        for label in ('Close','close','Done','Skip','No thanks','Fechar'):
            self.assertTrue(snapshot(label).find_close_candidates()[0].actionable_evidence)
        self.assertEqual(len(snapshot('Continue').find_continue_candidates()),1)
        self.assertEqual(len(snapshot('Restart').find_restart_candidates()),1)
        self.assertEqual(snapshot('Play now').find_close_candidates(),())
        for label in ('X','×'):
            self.assertFalse(snapshot(label,**{'resource-id':'game:id/ad_close'}).find_close_candidates()[0].actionable_evidence)

    def test_invisible_parent_malformed_and_disabled(self):
        xml='<hierarchy><node visible-to-user="false"><node text="Close" clickable="true" enabled="true" bounds="[0,0][10,10]"/></node></hierarchy>'
        self.assertFalse(find_candidates(parse_ui(xml),'CLOSE')[0].actionable_evidence)
        self.assertFalse(snapshot(enabled='false').find_close_candidates()[0].actionable_evidence)
        for xml in ('<broken', '<!DOCTYPE x><hierarchy/>'):
            with self.assertRaises(ValueError): parse_ui(xml)

    def test_probe_readonly_and_app_change(self):
        client = Mock()
        client.app_current.return_value={'package':'game','activity':'AdActivity'}
        client.dump_hierarchy.return_value='<hierarchy/>'
        probe=UiProbe(client,clock=lambda:10.)
        self.assertIsNone(probe.read().error)
        self.assertEqual({c[0] for c in client.mock_calls},{'app_current','dump_hierarchy'})
        client.app_current.side_effect=[{'package':'game'},{'package':'other'}]
        self.assertEqual(probe.read().error,'app_changed_during_probe')

    def test_ad_requires_verified_activity_and_package(self):
        s=snapshot()
        self.assertEqual(infer_runtime_state(GameState.UNKNOWN,s,expected_package='game'),RuntimeState.UNKNOWN)
        self.assertEqual(infer_runtime_state(GameState.UNKNOWN,s,expected_package='game',verified_ad_activities=('AdActivity',)),RuntimeState.ADVERTISEMENT)
        self.assertEqual(infer_runtime_state(GameState.PLAYING,s,expected_package='game',verified_ad_activities=('AdActivity',)),RuntimeState.UNKNOWN)
        self.assertEqual(infer_runtime_state(GameState.PLAYING,s,expected_package='other'),RuntimeState.UNKNOWN)

    def test_recovery_is_plan_not_authorization(self):
        def plan(s=snapshot(),state=RuntimeState.ADVERTISEMENT,now=10.,entered_at=0.):
            return plan_recovery(state,s,now=now,entered_at=entered_at,sequence=7,resolution=(1080,2340),expected_package='game')
        action=plan()
        self.assertEqual(action.kind,ActionKind.CLOSE)
        self.assertFalse(action.authorized)
        self.assertEqual(plan(snapshot('X')).kind,ActionKind.RECHECK_UI)
        self.assertEqual(plan(snapshot(**{'bounds':'[900,10][2000,100]'})).kind,ActionKind.RECHECK_UI)
        self.assertEqual(plan(state=RuntimeState.UNKNOWN,now=100.).kind,ActionKind.WAIT)
        self.assertEqual(plan(now=50.).kind,ActionKind.BACK)
        self.assertEqual(plan(now=100.).kind,ActionKind.RELAUNCH)
        self.assertEqual(plan(entered_at=8.).kind,ActionKind.WAIT)


class WatchdogTests(unittest.TestCase):
    def test_no_frames_and_frozen_pts(self):
        w=Watchdog(started_at=0.)
        self.assertEqual(w.check(2.).recommendation,'STOP')
        w=Watchdog(started_at=0.)
        w.observe(1,1.,0.,RuntimeState.PLAYING,True)
        fresh,result=w.observe(2,1.,2.,RuntimeState.PLAYING,True)
        self.assertFalse(fresh)
        self.assertEqual(result.recommendation,'STOP')

    def test_unknown_target_and_repeated_intents(self):
        w=Watchdog(started_at=0.)
        w.observe(0,0.,0.,RuntimeState.UNKNOWN,True)
        w.observe(1,1.,19.,RuntimeState.UNKNOWN,False)
        self.assertEqual(w.observe(2,2.,20.,RuntimeState.UNKNOWN,False)[1].recommendation,'RECHECK_UI')
        w=Watchdog(started_at=0.)
        for i in range(3):
            _,result=w.observe(i,float(i),float(i),RuntimeState.ADVERTISEMENT,False,intent='WOULD_CLICK_CLOSE')
        self.assertEqual(result.reason,'repeated_intent_without_progress')
        self.assertNotIn(result.recommendation,('TAP','BACK','RELAUNCH'))

    def test_unchanged_ui_progress_and_invalid_clock(self):
        w=Watchdog(started_at=0.)
        w.observe(0,0.,0.,RuntimeState.ADVERTISEMENT,False,ui_signature='one')
        self.assertEqual(w.observe(1,1.,46.,RuntimeState.ADVERTISEMENT,False,ui_signature='one')[1].reason,'unchanged_ui')
        self.assertEqual(w.observe(2,2.,47.,RuntimeState.ADVERTISEMENT,False,ui_signature='two')[1].reason,'healthy')
        self.assertEqual(w.check(float('nan')).recommendation,'STOP')
        with self.assertRaises(ValueError): Watchdog(started_at=0.,stale_seconds=0.)


class FrameTests(unittest.TestCase):
    def test_telemetry_is_bounded_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runtime.jsonl'
            sink=JsonlTelemetry(path,limit=1)
            try:
                sink.write({'runtime_state':'UNKNOWN'})
                sink.write({'runtime_state':'PLAYING'})
            finally: sink.close()
            self.assertEqual(len(path.read_text().splitlines()),1)
            with self.assertRaises(FileExistsError): JsonlTelemetry(path)

    def test_latest_drops_backlog_copies_and_drains_once(self):
        source=LatestFrameSource()
        image=np.zeros((20,20,3),np.uint8)
        source.publish(image,0.,0.)
        source.publish(image,1.,1.)
        image[:]=255
        packet=source.read()
        self.assertEqual(packet.sequence_number,1)
        self.assertFalse(packet.frame.any())
        self.assertIsNone(source.read())
        source.close()
        self.assertFalse(source.publish(image,2.,2.))

    def test_adb_queries_and_screenshot(self):
        runner=Mock(return_value=Mock(stdout=b'Physical size: 1080x2340\nOverride size: 540x1170'))
        session=AdbSession('test-device',runner=runner)
        self.assertEqual(session.get_resolution(),(540,1170))
        self.assertEqual(runner.call_args.args[0],['adb','-s','test-device','shell','wm','size'])
        image=np.zeros((10,20,3),np.uint8)
        _,encoded=cv2.imencode('.png',image)
        runner.return_value.stdout=encoded.tobytes()
        packet=AdbScreenshotSource(session,clock=lambda:1.).read()
        self.assertEqual(packet.frame.shape,(10,20,3))
        self.assertEqual(runner.call_args.args[0][-3:],['exec-out','screencap','-p'])
        self.assertIsNone(parse_current_app('historical game/.Old').package)
        self.assertEqual(parse_current_app('mCurrentFocus=Window{ game/.Main }'),CurrentApp('game','.Main'))

    def test_stale_runtime_never_calls_vision(self):
        runtime=BotRuntime(clock=lambda:10.)
        packet=FramePacket(np.zeros((10,10,3),np.uint8),0.,0,0.)
        with patch('src.runtime.bot_runtime.detect_target') as detector:
            result=runtime.process(packet)
            detector.assert_not_called()
        self.assertEqual(result['runtime_state'],'STALLED')
        self.assertEqual(result['game_state'],'UNKNOWN')

    def test_runtime_foreground_duplicate_and_gap_revoke_playing(self):
        clock = [0.]
        runtime = BotRuntime(expected_package='game', clock=lambda:clock[0])
        image = np.zeros((20,20,3),np.uint8)
        state_result = Mock(state=GameState.PLAYING, score=1.)
        tracked = Mock(valid=True,count=2,diagnostics={'count_uncertain':False})
        with patch('src.runtime.bot_runtime.detect_target',return_value=(10,10,5)), \
             patch('src.runtime.bot_runtime.classify_game_state',return_value=state_result), \
             patch('src.runtime.bot_runtime.detect_knives') as knives, \
             patch.object(runtime.tracker,'update',return_value=tracked):
            for i in range(6):
                clock[0]=i*.02
                packet=FramePacket(image,clock[0],i,clock[0])
                result=runtime.process(packet,replace(snapshot(),observed_at=clock[0],app=CurrentApp('game','.Main')))
            self.assertEqual(result['runtime_state'],'PLAYING')
            clock[0]=.12
            result=runtime.process(FramePacket(image,.12,6,.12),replace(snapshot(),observed_at=.12,app=CurrentApp('other','.Main')))
            self.assertEqual(result['runtime_state'],'UNKNOWN')
            self.assertEqual(result['knife_count'],0)
            self.assertTrue(result['knife_count_uncertain'])
            self.assertEqual(knives.call_args.kwargs['state'],GameState.UNKNOWN)
            # Reusing sequence/PTS must skip vision entirely.
            with patch('src.runtime.bot_runtime.detect_target') as target:
                result=runtime.process(packet)
                target.assert_not_called()
            self.assertEqual(result['runtime_state'],'STALLED')
            clock[0]=1.
            result=runtime.process(FramePacket(image,1.,7,1.),replace(snapshot(),observed_at=1.,app=CurrentApp('game','.Main')))
            self.assertEqual(result['game_state'],'UNKNOWN')

    def test_current_focus_null_does_not_use_old_focused_app(self):
        self.assertIsNone(parse_current_app('mCurrentFocus=null\nmFocusedApp=game/.Old').package)
        self.assertIsNone(parse_current_app('mFocusedApp=game/.Old\nmCurrentFocus=null').package)
        self.assertIsNone(parse_current_app('mFocusedApp=game/.Old').package)

    def test_offline_real_frames(self):
        path=Path('debug/treasure-sample-2.mp4')
        if not path.exists(): self.skipTest('Local ignored video unavailable')
        source=VideoFrameSource(path)
        runtime=BotRuntime()
        try:
            for i in range(8):
                record=runtime.process(source.read())
                self.assertEqual(record['frame_sequence'],i)
                self.assertEqual(record['game_state'],'UNKNOWN')
                self.assertEqual(record['execution'],'DRY_RUN')
        finally: source.close()


if __name__ == '__main__': unittest.main()
