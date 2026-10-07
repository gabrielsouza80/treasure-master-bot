"""Narrow clean-zero exception and measured all-horizon commissioning.

Zero candidates alone are never TRUE_ZERO. Negative root evidence must cover
the entire proximal perimeter, independent of the detector's brightness gate.
Any previously confirmed knife in this run prevents a zero permit: a vanished or
expired knife cannot be forgotten just because a tracker reset its IDs.
"""
from dataclasses import dataclass
from math import hypot, isfinite, ceil

from src.prediction.safe_shot import predict_shot, ShotSafety, ShotPrediction
from src.vision.knife_detector import circular_distance


@dataclass(frozen=True)
class CleanTargetEvidence:
    status: str = 'UNKNOWN_ZERO'
    reasons: tuple = ()
    consecutive_frames: int = 0
    stable_seconds: float = 0.

    @property
    def true_zero(self): return self.status == 'TRUE_ZERO'


class CleanTargetObserver:
    def __init__(self,expected_app,impact_angle=180.):
        self.expected_app=expected_app
        self.impact_angle=impact_angle
        self.knife_seen=False
        self.reset_window()
        self.evidence=CleanTargetEvidence()

    def reset_window(self):
        self.anchor=None
        self.started=None
        self.last_time=None
        self.last_sequence=None
        self.frames=0

    def update(self,record,tracked,raw,packet,snapshot,now,tip):
        reasons=[]
        diagnostics=(tracked.diagnostics or {}) if tracked else {}
        if (tracked and tracked.count
                or diagnostics.get('expired_confirmed_tracks')):
            self.knife_seen=True
        if self.knife_seen: reasons.append('PRIOR_KNIFE_EVIDENCE')
        if (record.get('runtime_state')!='PLAYING' or record.get('watchdog_status')!='WAIT'
                or snapshot is None or snapshot.error or snapshot.app!=self.expected_app
                or not 0<=now-snapshot.observed_at<=.75): reasons.append('NON_GAMEPLAY_OR_UI')
        if (not isfinite(now) or not 0<=now-packet.received_at<=.08
                or self.last_time is not None and not 0<packet.received_at-self.last_time<=.10
                or self.last_sequence is not None and packet.sequence_number<=self.last_sequence):
            reasons.append('STALE_OR_DISCONTINUOUS_FRAME')
        target=record.get('target')
        if target is not None and (len(target)!=3 or not all(isfinite(v) for v in target) or target[2]<=0):
            target=None
        if target is None or not raw or not raw.valid or not tracked or not tracked.valid:
            reasons.append('INVALID_DETECTION')
        if raw and raw.count: reasons.append('RAW_KNIFE')
        if tracked and (tracked.count or tracked.probable_knives or tracked.occluded_tracks):
            reasons.append('TRACK_PRESENT')
        uncertainty=diagnostics.get('count_uncertain_reasons')
        if (uncertainty is None or any(reason!='NO_CONFIRMED_TRACKS' for reason in uncertainty)
                or record.get('knife_count_uncertain',True) and not uncertainty):
            reasons.append('OTHER_UNCERTAINTY')
        if diagnostics.get('reset'): reasons.append('TRACKER_RESET')
        quality=raw.diagnostics if raw else {}
        if (quality.get('zero_evidence_version')!=1 or quality.get('zero_perimeter_visible') is not True
                or not isfinite(quality.get('zero_root_max_support',float('nan')))
                or quality.get('zero_root_max_support',1.)>=.50):
            reasons.append('PERIMETER_UNKNOWN_OR_RADIAL_STRUCTURE')
        occluded=quality.get('hud_occluded_angles',())
        if (not all(isfinite(a) for a in occluded)
                or any(circular_distance(a,self.impact_angle)<25 for a in occluded)):
            reasons.append('IMPACT_OCCLUDED')
        if tip is None or target is not None and not (target[1]+2.4*target[2]<=tip<=.84*packet.frame.shape[0]):
            reasons.append('NO_WAITING_PROJECTILE')
        if target is not None and self.anchor is not None:
            if (hypot(target[0]-self.anchor[0],target[1]-self.anchor[1])>.035*self.anchor[2]
                    or abs(target[2]/self.anchor[2]-1)>.06): reasons.append('TARGET_UNSTABLE')
        if reasons:
            self.reset_window()
            self.evidence=CleanTargetEvidence(reasons=tuple(reasons))
            return self.evidence
        if self.started is None:
            self.started=packet.received_at
            self.anchor=tuple(target)
        self.last_time=packet.received_at
        self.last_sequence=packet.sequence_number
        self.frames+=1
        elapsed=packet.received_at-self.started
        ready=self.frames>=12 and elapsed>=.35
        self.evidence=CleanTargetEvidence('TRUE_ZERO' if ready else 'UNKNOWN_ZERO',
                                          () if ready else ('ZERO_WARMUP',),self.frames,elapsed)
        return self.evidence


def predict_commissioning(angles,rotation,calibration,*,timestamp,impact_angle_deg=180.,occluded_angles=()):
    """Every 5 ms horizon must pass, with inter-sample angular padding.

    The interval is derived only from accepted, fresh measurements. The
    additional 150 ms allowance is uncertainty, never a substitute flight time.
    """
    if (not calibration.has_recent_samples or not rotation.valid or not rotation.stable
            or rotation.reversing or rotation.residual_deg>1.
            or abs(rotation.angular_acceleration_deg_s2)>300):
        return ShotPrediction(ShotSafety.UNKNOWN,'NO_MEASURED_COMMISSIONING_ENVELOPE'),None
    allowance=max(.15,calibration.spread_s+.10)
    lower=max(.02,calibration.median_s-allowance)
    upper=calibration.median_s+allowance
    if upper>.65:
        return ShotPrediction(ShotSafety.UNKNOWN,'COMMISSIONING_ENVELOPE_TOO_WIDE'),None
    steps=max(1,ceil((upper-lower)/.005))
    spacing=(upper-lower)/steps
    speed=abs(rotation.angular_velocity_deg_s)+abs(rotation.angular_acceleration_deg_s2)*upper
    padding=speed*spacing/2
    predictions=[predict_shot(angles,rotation,timestamp=timestamp,horizon_s=lower+i*spacing,
                             timing_error_s=allowance,impact_angle_deg=impact_angle_deg,
                             geometry_margin_deg=28.+padding,uncertain=False,impact_sector_visible=True,
                             occluded_angles=occluded_angles) for i in range(steps+1)]
    interval=dict(lower_s=lower,upper_s=upper,horizons=steps+1,inter_sample_padding_deg=padding)
    for prediction in predictions:
        if prediction.decision!=ShotSafety.SAFE:return prediction,interval
    worst=min(predictions,key=lambda p:p.nearest_predicted_distance_deg-p.required_margin_deg)
    return worst,interval
