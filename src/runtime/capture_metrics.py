"""Bounded timing samples. Host receipt age is NOT device-to-host latency."""
from collections import deque
import numpy as np


def distribution(values):
    if not values:
        return dict(mean_ms=None,p50_ms=None,p95_ms=None,p99_ms=None,max_ms=None)
    a=np.asarray(values,dtype=float)*1000
    return dict(mean_ms=float(a.mean()),p50_ms=float(np.percentile(a,50)),
                p95_ms=float(np.percentile(a,95)),p99_ms=float(np.percentile(a,99)),max_ms=float(a.max()))


class CaptureMetrics:
    def __init__(self):
        self.frames=0
        self.first_pts=self.last_pts=self.first_received=self.last_received=None
        self.intervals=deque(maxlen=12000)
        self.ages=deque(maxlen=12000)
        self.stale=0

    def produced(self, pts, received):
        if self.first_pts is None:
            self.first_pts, self.first_received=pts,received
        if self.last_received is not None:
            self.intervals.append(received-self.last_received)
        self.last_pts,self.last_received=pts,received
        self.frames+=1

    def consumed(self, received, now):
        age=now-received
        self.ages.append(age)
        self.stale += age<0 or age>1.

    def summary(self, duration, consumed):
        span=(self.last_pts-self.first_pts) if self.frames>1 else 0.
        return dict(capture_frames=self.frames,capture_duration_s=duration,
                    source_fps=(self.frames-1)/span if span>0 else None,
                    consumer_fps=consumed/duration if duration>0 else None,
                    interframe=distribution(self.intervals),host_frame_age=distribution(self.ages),
                    stale_frames=self.stale,samples_retained=len(self.intervals),
                    end_to_end_latency_ms=None,
                    age_definition='packet_receipt_to_host_consumer; device clock not mapped')
