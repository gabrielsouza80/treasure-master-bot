"""Transport-independent BGR packets. Polling never makes an old frame fresh."""
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Protocol

import cv2
import numpy as np


@dataclass(frozen=True)
class FramePacket:
    frame: np.ndarray
    timestamp: float  # Source presentation time, seconds; video uses decoded PTS.
    sequence_number: int
    received_at: float  # Host monotonic clock, separate from video PTS.


class FrameSource(Protocol):
    exhausted: bool
    def read(self) -> FramePacket | None: ...
    def close(self) -> None: ...


class VideoFrameSource:
    def __init__(self, path, *, clock=monotonic):
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            self._cap.release()
            raise RuntimeError('Offline video could not be opened')
        self.expected_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._clock, self._sequence, self.exhausted = clock, 0, False

    def read(self):
        if self.exhausted:
            return None
        ok, frame = self._cap.read()
        if not ok:
            self.exhausted = True
            return None
        packet = FramePacket(frame, self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000,
                             self._sequence, self._clock())
        self._sequence += 1
        return packet

    def close(self):
        self.exhausted = True
        self._cap.release()


class AdbScreenshotSource:
    """Slow observation fallback, not a claimed real-time scrcpy replacement."""
    def __init__(self, session, *, clock=monotonic):
        self.session, self._clock = session, clock
        self._sequence, self.exhausted = 0, False

    def read(self):
        if self.exhausted:
            return None
        started = self._clock()
        frame = cv2.imdecode(np.frombuffer(self.session.screenshot_png(), np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError('Invalid ADB screenshot')
        packet = FramePacket(frame, started, self._sequence, started)
        self._sequence += 1
        return packet

    def close(self):
        self.exhausted = True


class LatestFrameSource:
    """One-slot adapter for an existing decoder's frame callback.

    No scrcpy protocol/deployment code. Producer supplies source PTS and capture
    receipt time; consumer drains at most once and drops backlog. A copy protects
    against decoders recycling buffers. Disconnection must call close().
    """
    def __init__(self):
        self._lock, self._packet, self._sequence = Lock(), None, 0
        self.exhausted = False

    def publish(self, frame, timestamp, received_at):
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size
                or not np.isfinite([timestamp, received_at]).all()):
            raise ValueError('Expected BGR frame and finite timestamps')
        with self._lock:
            if self.exhausted:
                return False
            self._packet = FramePacket(frame.copy(), float(timestamp), self._sequence, float(received_at))
            self._sequence += 1
        return True

    def read(self):
        with self._lock:
            packet, self._packet = self._packet, None
            return packet

    def close(self):
        with self._lock:
            self.exhausted, self._packet = True, None
