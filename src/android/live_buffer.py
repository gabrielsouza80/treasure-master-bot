"""Bounded decoded-frame handoff; no polling can manufacture freshness."""
from threading import Condition
import numpy as np
from src.android.frame_source import FramePacket
from src.runtime.capture_metrics import CaptureMetrics


class LiveFrameBuffer:
    def __init__(self):
        self._condition = Condition()
        self._packet = None
        self.exhausted = False
        self.error = None
        self.frames = self.replaced = self.duplicate_pts = self.out_of_order_pts = 0
        self._last_pts = None
        self.metrics = CaptureMetrics()

    def publish(self, frame, timestamp, received_at):
        if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3
                or frame.shape[2]!=3 or not frame.size or not np.isfinite([timestamp,received_at]).all()):
            raise ValueError('Expected BGR frame with finite timestamps')
        with self._condition:
            if self.exhausted:
                return False
            if self._last_pts is not None and timestamp <= self._last_pts:
                if timestamp == self._last_pts:
                    self.duplicate_pts += 1
                else:
                    self.out_of_order_pts += 1
                return False
            self._last_pts = timestamp
            self.replaced += self._packet is not None
            self._packet = FramePacket(frame.copy(), timestamp, self.frames, received_at)
            self.metrics.produced(timestamp,received_at)
            self.frames += 1
            self._condition.notify_all()
            return True

    def read(self, timeout=0.):
        with self._condition:
            self._condition.wait_for(lambda:self._packet is not None or self.exhausted, timeout=timeout)
            packet, self._packet = self._packet, None
            return packet

    def finish(self, error=None):
        with self._condition:
            self.error, self.exhausted = error, True
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._packet = None
            self.exhausted = True
            self._condition.notify_all()
