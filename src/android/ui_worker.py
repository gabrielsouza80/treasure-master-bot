"""Isolated read-only UI process, one snapshot slot, bounded host shutdown."""
from dataclasses import dataclass
from collections import deque
import logging
import multiprocessing
import os
from queue import Empty, Full
from time import monotonic

from src.android.device import AdbSession, CurrentApp
from src.android.ui_probe import UiProbe, UiSnapshot
from src.runtime.capture_metrics import distribution


class ReadOnlyHierarchyClient:
    def __init__(self, session, device):
        self._session, self._device = session, device

    def app_current(self):
        app=self._session.get_current_app()
        return {'package':app.package,'activity':app.activity}

    def dump_hierarchy(self, compressed=False, pretty=False, max_depth=50):
        return self._device.jsonrpc.dumpWindowHierarchy(compressed,max_depth,http_timeout=2.)


@dataclass(frozen=True)
class LiveUiFactory:
    serial: str
    adb: str

    def __call__(self):
        # Only child process owns the SDK client; no interaction API is exposed
        # to the observer. SDK may push/start u2.jar for accessibility RPC.
        os.environ['ADBUTILS_ADB_PATH']=self.adb
        logging.disable(logging.CRITICAL)
        import uiautomator2
        device=uiautomator2.connect(self.serial)
        return UiProbe(ReadOnlyHierarchyClient(AdbSession(self.serial,adb=self.adb),device))


class ForegroundProbe:
    """Autoplay needs fresh foreground identity, not a slow hierarchy dump."""
    def __init__(self,session,clock=monotonic):
        self.session,self.clock=session,clock

    def read(self):
        started=self.clock()
        try:return UiSnapshot(self.session.get_current_app(),(),started)
        except Exception:return UiSnapshot(CurrentApp(),(),started,'foreground_query_failed')


@dataclass(frozen=True)
class ForegroundFactory:
    serial: str
    adb: str

    def __call__(self):return ForegroundProbe(AdbSession(self.serial,adb=self.adb))


def _put_latest(queue, value):
    try:
        queue.put_nowait(value)
    except Full:
        try:
            queue.get_nowait()
        except Empty:
            return
        try:
            queue.put_nowait(value)
        except Full:
            pass


def poll_ui(factory, queue, stop, interval):
    probe=None
    probes=success=failure=0
    total_seconds=0.
    durations=deque(maxlen=1000)
    while not stop.is_set():
        started=monotonic()
        try:
            if probe is None:
                probe=factory()
            snapshot=probe.read()
        except Exception:
            snapshot=UiSnapshot(CurrentApp(),(),started,'ui_worker_failed')
        elapsed=monotonic()-started
        probes+=1
        success+=snapshot.error is None
        failure+=snapshot.error is not None
        total_seconds+=elapsed
        durations.append(elapsed)
        _put_latest(queue,(snapshot,elapsed,probes,success,failure,total_seconds,distribution(durations)))
        stop.wait(interval)


class UiPollingWorker:
    def __init__(self, factory, *, interval=1., context=None):
        if not .1 <= interval <= 10:
            raise ValueError('Invalid UI polling interval')
        ctx=context or multiprocessing.get_context('spawn')
        self._queue,self._stop=ctx.Queue(maxsize=1),ctx.Event()
        self._process=ctx.Process(target=poll_ui,args=(factory,self._queue,self._stop,interval),daemon=True)
        self.latest=None
        self.last_duration=0.
        self.probes=self.success=self.failure=0
        self.total_seconds=0.
        self.duration_stats=distribution([])
        self.started=False

    def start(self):
        self._process.start()
        self.started=True
        return self

    @property
    def closed(self):
        return self._stop.is_set()

    def read(self):
        try:
            (self.latest,self.last_duration,self.probes,self.success,
             self.failure,self.total_seconds,self.duration_stats)=self._queue.get_nowait()
        except Empty:
            pass
        if self.started and not self._process.is_alive() and not self._stop.is_set():
            self.latest=UiSnapshot(CurrentApp(),(),monotonic(),'ui_worker_exited')
        return self.latest

    def close(self):
        self._stop.set()
        if self.started:
            self._process.join(timeout=3)
            if self._process.is_alive():
                self._process.terminate()  # Host worker only; never force-stop apps.
                self._process.join(timeout=2)
            if self._process.is_alive():
                raise RuntimeError('UI observer did not shut down')
        self._queue.cancel_join_thread()
        self._queue.close()
