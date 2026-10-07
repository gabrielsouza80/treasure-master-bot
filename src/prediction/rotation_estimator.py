"""Recent confirmed ID motion, clockwise-positive; no texture assumptions."""
from collections import deque
from dataclasses import dataclass
from math import isfinite
import numpy as np
from src.vision.knife_detector import signed_angle_delta


@dataclass(frozen=True)
class RotationEstimate:
    valid: bool = False
    timestamp: float = 0.
    angular_velocity_deg_s: float = 0.
    angular_acceleration_deg_s2: float = 0.
    direction: str = 'UNKNOWN'
    confidence: float = 0.
    sample_count: int = 0
    residual_deg: float = 0.
    velocity_error_deg_s: float = 0.
    reversing: bool = False
    stable: bool = False
    reason: str = 'INSUFFICIENT_MOTION'


class RotationEstimator:
    def __init__(self, window=.30):
        if not .15 <= window <= .5: raise ValueError('Invalid rotation window')
        self.window=window
        self.reset()

    def reset(self):
        self.previous={}
        self.last_time=None
        self.samples=deque(maxlen=32)
        self.phase=0.
        self.last_estimate=RotationEstimate()

    def update(self, tracked, timestamp):
        if not isfinite(timestamp) or not tracked or not tracked.valid:
            self.reset()
            return RotationEstimate(reason='INVALID_TRACKING')
        current={k.identifier:k.angle_deg for k in tracked.confirmed_observed}
        if not all(isfinite(angle) for angle in current.values()):
            self.reset()
            return RotationEstimate(timestamp=timestamp,reason='NONFINITE_TRACK')
        old,previous_time=self.previous,self.last_time
        self.previous,self.last_time=current,timestamp
        if previous_time is None: return RotationEstimate(timestamp=timestamp)
        dt=timestamp-previous_time
        if not 0 < dt <= .10:
            self.samples.clear(); self.phase=0.
            self.last_estimate=RotationEstimate(timestamp=timestamp,reason='STALE_MOTION')
            return self.last_estimate
        steps=[signed_angle_delta(a,old[i]) for i,a in current.items() if i in old]
        if not steps:
            self.samples.clear(); self.phase=0.
            self.last_estimate=RotationEstimate(timestamp=timestamp,reason='NO_COMMON_OBSERVED_ID')
            return self.last_estimate
        step=float(np.median(steps))
        if max(abs(d-step) for d in steps)>3 or abs(step)>30:
            self.samples.clear()
            self.last_estimate=RotationEstimate(timestamp=timestamp,reason='INCOHERENT_MOTION')
            return self.last_estimate
        instantaneous=step/dt
        baseline=self.last_estimate.angular_velocity_deg_s
        reversal=abs(baseline)>35 and abs(instantaneous)>35 and baseline*instantaneous<0
        change=self.last_estimate.valid and abs(instantaneous-baseline)>max(120,.8*abs(baseline))
        if reversal or change:
            self.samples.clear(); self.phase=0.
            self.last_estimate=RotationEstimate(timestamp=timestamp,reversing=reversal,
                                                reason='REVERSAL' if reversal else 'VELOCITY_CHANGE')
            return self.last_estimate
        self.phase+=step
        self.samples.append((timestamp,self.phase))
        while self.samples and timestamp-self.samples[0][0]>self.window:
            self.samples.popleft()
        if len(self.samples)<6 or timestamp-self.samples[0][0]<.12:
            return RotationEstimate(timestamp=timestamp,reason='WARMUP')
        t=np.array([s[0]-timestamp for s in self.samples])
        angles=np.array([s[1] for s in self.samples])
        omega,intercept=np.polyfit(t,angles,1)
        fitted=omega*t+intercept
        acceleration=0.
        if len(t)>=8:
            quadratic,omega,intercept=np.polyfit(t,angles,2)
            acceleration=float(2*quadratic)
            fitted=quadratic*t*t+omega*t+intercept
        # t=0 is the newest observation: predict with current velocity,
        # rather than the midpoint velocity of an accelerating window.
        residual=float(np.max(np.abs(angles-fitted)))
        valid=bool(residual<=2.5 and abs(omega)<=600 and abs(acceleration)<=600)
        reason='OK' if valid else 'ACCELERATION_OR_RESIDUAL'
        self.last_estimate=RotationEstimate(valid,timestamp,float(omega),acceleration,
                                            'CW' if omega>5 else 'CCW' if omega<-5 else 'STOPPED',
                                            max(0.,1-residual/5) if valid else 0.,len(t),residual,
                                            max(10.,2*residual/(t[-1]-t[0])),False,valid,reason)
        return self.last_estimate
