import importlib.util
import multiprocessing
from fractions import Fraction
from pathlib import Path
from queue import Queue
from threading import Event, Thread
import socket
import struct
import sys
import time
import unittest
from unittest.mock import Mock, patch
import numpy as np

from src.android.device import AdbSession, CurrentApp, parse_current_app
from src.android.live_buffer import LiveFrameBuffer
from src.android.scrcpy_source import recv_exact, read_header, server_arguments, ScrcpyFrameSource, SESSION, CONFIG, MAX_PAYLOAD
from src.android.ui_probe import parse_ui, find_candidates, UiSnapshot, UiProbe
from src.android.ui_worker import UiPollingWorker, ReadOnlyHierarchyClient, poll_ui, _put_latest
from src.android.frame_source import FramePacket
from src.runtime.capture_metrics import CaptureMetrics, distribution
from src.runtime.live_observer import EvidenceCatalogue, observe, safe_identifier
from src.runtime.bot_runtime import BotRuntime
from src.states.runtime_state import RuntimeState


class ByteSocket:
    def __init__(self,data): self.data=bytearray(data)
    def recv(self,size):
        chunk=self.data[:min(size,3)]
        del self.data[:len(chunk)]
        return bytes(chunk)


class StaticProbe:
    def read(self): return UiSnapshot(CurrentApp('game','.Main'),(),time.monotonic())


class StaticFactory:
    def __call__(self): return StaticProbe()


class BufferTests(unittest.TestCase):
    def test_bounded_copy_replace_duplicate_order_and_eof(self):
        b=LiveFrameBuffer()
        image=np.zeros((4,4,3),np.uint8)
        for i in range(100): self.assertTrue(b.publish(image,float(i),float(i)))
        image[:]=255
        self.assertFalse(b.publish(image,99.,100.))
        self.assertFalse(b.publish(image,98.,100.))
        self.assertEqual((b.frames,b.replaced,b.duplicate_pts,b.out_of_order_pts),(100,99,1,1))
        b.finish('source_disconnected')
        packet=b.read()
        self.assertEqual(packet.sequence_number,99)
        self.assertFalse(packet.frame.any())
        self.assertIsNone(b.read())
        self.assertEqual(b.error,'source_disconnected')
        self.assertFalse(b.publish(image,101.,101.))

    def test_waiter_wakes_on_shutdown(self):
        b=LiveFrameBuffer()
        done=Event()
        thread=Thread(target=lambda:(b.read(timeout=5),done.set()))
        thread.start()
        b.close()
        self.assertTrue(done.wait(1))
        thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_invalid_frames_rejected_and_stats_bounded(self):
        b=LiveFrameBuffer()
        for image,pts in ((np.zeros((2,2),np.uint8),0.),(np.zeros((2,2,3)),0.),(np.zeros((2,2,3),np.uint8),float('nan'))):
            with self.assertRaises(ValueError): b.publish(image,pts,0.)
        m=CaptureMetrics()
        for i in range(13000): m.produced(float(i),float(i))
        self.assertEqual(len(m.intervals),12000)


class ProtocolTests(unittest.TestCase):
    def test_exact_fragmented_headers_and_size_limits(self):
        self.assertEqual(read_header(ByteSocket(struct.pack('>III',1<<31,1080,2340))),('SESSION',1080,2340))
        self.assertEqual(read_header(ByteSocket(struct.pack('>QI',CONFIG,15))),('CONFIG',0,15))
        self.assertEqual(read_header(ByteSocket(struct.pack('>QI',(1<<61)|123,15))),('FRAME',123,15))
        for data in (struct.pack('>QI',12,MAX_PAYLOAD+1),struct.pack('>III',1<<31,0,2340)):
            with self.assertRaises(ValueError): read_header(ByteSocket(data))
        with self.assertRaises(EOFError): recv_exact(ByteSocket(b'xx'),3)

    def test_idle_timeout_preserves_partial_packet(self):
        connection=Mock()
        connection.recv.side_effect=[b'a',socket.timeout(),b'bc']
        self.assertEqual(recv_exact(connection,3,Event()),b'abc')
        stop=Event(); stop.set()
        connection.recv.side_effect=socket.timeout()
        with self.assertRaises(EOFError): recv_exact(connection,1,stop)

    def test_server_is_video_only_and_cannot_wake_or_control(self):
        args=server_arguments('/data/local/tmp/test.jar',123)
        for option in ('control=false','audio=false','power_on=false','max_size=0','cleanup=false'):
            self.assertIn(option,args)
        self.assertNotIn('control=true',args)
        with self.assertRaises(ValueError): server_arguments('test',123,max_fps=0)

    def test_shutdown_only_removes_own_capture_resources(self):
        with patch.dict(sys.modules,{'av':Mock()}):
            source=ScrcpyFrameSource('mock-device','scrcpy.exe')
        source._port=4567
        source._uploaded=True
        source._process=Mock()
        source._process.poll.return_value=None
        source._query=Mock()
        source.close()
        self.assertEqual(source._query.call_args_list,
                         [unittest.mock.call(['forward','--remove','tcp:4567']),
                          unittest.mock.call(['shell','rm',source._remote])])
        self.assertTrue(source._remote.startswith('/data/local/tmp/treasure-observer-'))
        source._process.terminate.assert_called_once_with()
        source._process.wait.assert_called_once_with(timeout=3)
        source.close()
        self.assertEqual(source._query.call_count,2)

    def test_decoder_shutdown_failure_still_cleans_forward_and_helper(self):
        with patch.dict(sys.modules,{'av':Mock()}):
            source=ScrcpyFrameSource('mock-device','scrcpy.exe')
        source._thread=Mock()
        source._thread.is_alive.return_value=True
        source._port=1234
        source._uploaded=True
        source._query=Mock()
        with self.assertRaisesRegex(RuntimeError,'Decoder did not shut down'):
            source.close()
        self.assertEqual(source._query.call_count,2)
        self.assertIsNone(source._port)
        self.assertFalse(source._uploaded)
        self.assertTrue(source.exhausted)

    @unittest.skipUnless(importlib.util.find_spec('av'),'Optional PyAV not installed')
    def test_actual_h264_decode_and_clean_eof(self):
        import av
        encoder=av.CodecContext.create('libx264','w')
        encoder.width=encoder.height=64
        encoder.pix_fmt='yuv420p'
        encoder.time_base=Fraction(1,60)
        encoder.options={'preset':'ultrafast','tune':'zerolatency'}
        wire=struct.pack('>III',1<<31,64,64)
        for i in range(3):
            frame=av.VideoFrame.from_ndarray(np.full((64,64,3),i*50,np.uint8),format='bgr24')
            frame.pts=i
            for packet in encoder.encode(frame):
                payload=bytes(packet)
                wire+=struct.pack('>QI',i*16667,len(payload))+payload
        source=ScrcpyFrameSource('mock-device','scrcpy.exe')
        source._socket=ByteSocket(wire)
        source._decode()
        self.assertEqual(source.buffer.frames,3)
        self.assertEqual(source.buffer.error,'source_disconnected')
        packet=source.read()
        self.assertEqual(packet.frame.shape,(64,64,3))
        self.assertAlmostEqual(packet.timestamp,.033334,places=6)
        self.assertEqual(source.buffer.replaced,2)


class ParserAndWorkerTests(unittest.TestCase):
    def test_missing_enabled_fails_closed_and_parent_inheritance(self):
        for attribute,expected in (('enabled="true"',True),('enabled="false"',False),('',False)):
            nodes=parse_ui(f'<hierarchy><node text="Close" {attribute} clickable="true" bounds="[0,0][10,10]"/></hierarchy>')
            self.assertEqual(find_candidates(nodes,'CLOSE')[0].actionable_evidence,expected)
        for parent in ('enabled="false"',''):
            nodes=parse_ui(f'<hierarchy><node {parent}><node text="Done" enabled="true" clickable="true" bounds="[0,0][10,10]"/></node></hierarchy>')
            self.assertFalse(find_candidates(nodes,'CLOSE')[0].actionable_evidence)
        for label in ('X','×'):
            nodes=parse_ui(f'<hierarchy><node text="{label}" enabled="true" clickable="true" bounds="[0,0][10,10]"/></hierarchy>')
            self.assertFalse(find_candidates(nodes,'CLOSE')[0].actionable_evidence)

    def test_read_only_client_only_queries(self):
        session=Mock()
        session.get_current_app.return_value=CurrentApp('game','.Main')
        sdk=Mock()
        sdk.jsonrpc.dumpWindowHierarchy.return_value='<hierarchy/>'
        result=UiProbe(ReadOnlyHierarchyClient(session,sdk)).read()
        self.assertIsNone(result.error)
        sdk.jsonrpc.dumpWindowHierarchy.assert_called_once_with(False,50,http_timeout=2.)
        self.assertEqual({c[0] for c in sdk.mock_calls},{'jsonrpc.dumpWindowHierarchy'})

    def test_latest_snapshot_no_backlog_and_probe_failure(self):
        queue=Queue(maxsize=1)
        _put_latest(queue,'first'); _put_latest(queue,'second')
        self.assertEqual(queue.get_nowait(),'second')
        stop=Mock()
        stop.is_set.side_effect=[False,True]
        factory=Mock(side_effect=RuntimeError('private endpoint must not be logged'))
        poll_ui(factory,queue,stop,.1)
        snapshot,elapsed,probes,success,failure,total,timing=queue.get_nowait()
        self.assertEqual((probes,success,failure),(1,0,1))
        self.assertEqual(snapshot.error,'ui_worker_failed')
        self.assertEqual(snapshot.nodes,())

    def test_background_process_delivers_and_shuts_down(self):
        worker=UiPollingWorker(StaticFactory(),interval=.1).start()
        try:
            deadline=time.monotonic()+8
            snapshot=None
            while snapshot is None and time.monotonic()<deadline:
                snapshot=worker.read()
                if snapshot is None: time.sleep(.02)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.app.package,'game')
            self.assertGreaterEqual(worker.success,1)
        finally: worker.close()
        self.assertFalse(worker._process.is_alive())

    def test_android16_foreground_query(self):
        runner=Mock(return_value=Mock(stdout=b'mCurrentFocus=Window{ game/.Main }'))
        session=AdbSession('mock',runner=runner)
        self.assertEqual(session.get_current_app(),CurrentApp('game','.Main'))
        self.assertEqual(runner.call_args.args[0][-3:],['shell','dumpsys','window'])

    def test_virtual_display_null_focus_does_not_hide_physical_focus(self):
        output='Display: mDisplayId=17\nmCurrentFocus=null\nDisplay: mDisplayId=0\nmCurrentFocus=Window{ game/.Main }\nmTopFocusedDisplayId=0'
        self.assertEqual(parse_current_app(output),CurrentApp('game','.Main'))
        self.assertIsNone(parse_current_app(output.replace('mTopFocusedDisplayId=0','mTopFocusedDisplayId=17')).package)
        output='Display: mDisplayId=17\nmCurrentFocus=Window{ game/.Main }\nDisplay: mDisplayId=0\nmCurrentFocus=null'
        self.assertIsNone(parse_current_app(output).package)


class ObservationTests(unittest.TestCase):
    def test_benchmark_calculations(self):
        m=CaptureMetrics()
        for i in range(3): m.produced(i*.02,10+i*.02)
        m.consumed(10.,10.01); m.consumed(10.,12.)
        summary=m.summary(.1,2)
        self.assertAlmostEqual(summary['source_fps'],50.)
        self.assertAlmostEqual(summary['consumer_fps'],20.)
        self.assertAlmostEqual(summary['interframe']['mean_ms'],20.)
        self.assertEqual(summary['stale_frames'],1)
        self.assertIsNone(summary['end_to_end_latency_ms'])
        self.assertIsNone(distribution([])['p95_ms'])

    def test_observer_stop_disconnect_and_input_unreachable(self):
        now=[0.]
        class Source:
            exhausted=False
            def read(self):
                now[0]+=.1
                self.exhausted=True
                return None
            def close(self): self.closed=True
        source=Source()
        with patch('subprocess.run') as io:
            result=observe(source,duration=1.,clock=lambda:now[0])
            io.assert_not_called()
        self.assertEqual(result['stopped_reason'],'source_eof')
        self.assertEqual(result['watchdog_status_counts'],{'STOP':1})
        self.assertTrue(source.closed)
        self.assertFalse(result['android_input_enabled'])

    def test_stale_ui_and_observation_mode_never_plan_recovery(self):
        runtime=BotRuntime(expected_package='game',observation_only=True,clock=lambda:10.)
        packet=FramePacket(np.zeros((30,30,3),np.uint8),1.,1,10.)
        snapshot=UiSnapshot(CurrentApp('game','AdActivity'),(),0.)
        with patch('src.runtime.bot_runtime.plan_recovery') as planner:
            result=runtime.process(packet,snapshot)
            planner.assert_not_called()
        self.assertEqual(result['runtime_state'],'UNKNOWN')
        self.assertIsNone(result['current_android_package'])
        self.assertIn(result['planned_action'],('WAIT','RECHECK_UI','STOP'))

    def test_no_fresh_frames_recommend_stop_without_io(self):
        now=[0.]
        class Source:
            exhausted=False
            def read(self):
                now[0]+=.5
                return None
            def close(self): self.closed=True
        source=Source()
        with patch('subprocess.run') as io:
            result=observe(source,duration=3.,clock=lambda:now[0])
            io.assert_not_called()
        self.assertGreater(result['watchdog_status_counts']['STOP'],0)
        self.assertEqual(set(result['watchdog_status_counts']),{'WAIT','STOP'})
        self.assertEqual(result['consumed_frames'],0)
        self.assertTrue(source.closed)

    def test_known_game_activity_required_before_valid_knife_processing(self):
        from src.states.game_state import GameState
        runtime=BotRuntime(expected_package='game',expected_game_activity='.Game',
                           observation_only=True,clock=lambda:10.)
        runtime.stabilizer.update=Mock(return_value=GameState.PLAYING)
        packet=FramePacket(np.zeros((30,30,3),np.uint8),1.,1,10.)
        snapshot=UiSnapshot(CurrentApp('game','.UnverifiedAd'),(),10.)
        with patch('src.runtime.bot_runtime.detect_target',return_value=(15,15,5)), \
             patch('src.runtime.bot_runtime.classify_game_state',return_value=Mock(score=1.)), \
             patch('src.runtime.bot_runtime.detect_knives') as knives:
            result=runtime.process(packet,snapshot)
            self.assertEqual(knives.call_args.kwargs['state'],GameState.UNKNOWN)
        self.assertEqual(result['game_state'],'PLAYING')
        self.assertEqual(result['runtime_state'],'UNKNOWN')
        self.assertEqual(result['knife_angles_deg'],[])
        self.assertTrue(result['knife_count_uncertain'])

    def test_matching_game_activity_accepts_valid_gameplay_evidence(self):
        from src.states.game_state import GameState
        runtime=BotRuntime(expected_package='game',expected_game_activity='.Game',
                           observation_only=True,clock=lambda:10.)
        runtime.stabilizer.update=Mock(return_value=GameState.PLAYING)
        runtime.tracker.update=Mock(return_value=Mock(valid=True,count=1,angles_deg=[12.],
                                                     diagnostics={'count_uncertain':False}))
        packet=FramePacket(np.zeros((30,30,3),np.uint8),1.,1,10.)
        snapshot=UiSnapshot(CurrentApp('game','.Game'),(),10.)
        with patch('src.runtime.bot_runtime.detect_target',return_value=(15,15,5)), \
             patch('src.runtime.bot_runtime.classify_game_state',return_value=Mock(score=1.)), \
             patch('src.runtime.bot_runtime.detect_knives') as knives:
            result=runtime.process(packet,snapshot)
            self.assertEqual(knives.call_args.kwargs['state'],GameState.PLAYING)
        self.assertEqual(result['runtime_state'],'PLAYING')
        self.assertEqual(result['knife_angles_deg'],[12.])
        self.assertFalse(result['knife_count_uncertain'])

    def test_evidence_never_auto_verifies_and_identifiers_redacted(self):
        catalogue=EvidenceCatalogue()
        for i in range(3): catalogue.observe(UiSnapshot(CurrentApp('game','.Main'),(),float(i)),(1080,2340))
        entry=next(iter(catalogue.entries.values()))
        self.assertEqual(entry['observation_episodes'],1)
        self.assertEqual(entry['stage'],'OBSERVED_ONCE')
        self.assertFalse(entry['verified'])
        self.assertIsNone(safe_identifier('private-host:port'))
        self.assertIsNone(safe_identifier('1.2.3.4'))
        self.assertEqual(safe_identifier('com.gimica.treasuremaster'),'com.gimica.treasuremaster')
        self.assertEqual(safe_identifier('.Main'),'.Main')

    def test_catalogue_preserves_enabled_evidence_changes(self):
        catalogue=EvidenceCatalogue()
        for i,enabled in enumerate(('false','true')):
            nodes=parse_ui(f'<hierarchy><node text="Close" enabled="{enabled}" clickable="true" bounds="[900,10][1000,100]"/></hierarchy>')
            catalogue.observe(UiSnapshot(CurrentApp('game','.Ad'),nodes,float(i)),(1080,2340))
        self.assertEqual(len(catalogue.entries),2)
        self.assertEqual({e['unambiguous_close'] for e in catalogue.entries.values()},{0,1})
        self.assertTrue(all(not e['verified'] for e in catalogue.entries.values()))


if __name__=='__main__': unittest.main()
