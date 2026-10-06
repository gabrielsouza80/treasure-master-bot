"""One outstanding shot, no recovery actions, latched confirmation failure."""
from collections import Counter
from dataclasses import asdict,dataclass
from math import hypot
from src.android.gameplay_input import TapPermit
from src.prediction.safe_shot import predict_shot,ShotSafety
from src.vision.knife_detector import circular_distance


@dataclass
class PendingShot:
    commanded_at: float
    source_time: float
    count: int
    identifiers: set
    target: tuple
    baseline_tip: float | None
    movement_at: float | None = None
    impact_at: float | None = None
    candidate_id: int | None = None
    confirmation_frames: int = 0


class AutoplayEngine:
    def __init__(self,calibration,*,calibrating=False,impact_angle=180.):
        self.calibration=calibration
        self.calibrating=calibrating
        self.impact_angle=impact_angle
        self.pending=None
        self.failed=False
        self.stable_since=None
        self.last_target=None
        self.last_count=None
        self.metrics=Counter()
        self.shots=[]
        self.last_prediction=None
        self.last_rotation=None
        self.last_decision=None

    def observe_confirmation(self,record,tracked,packet,tip,now):
        pending=self.pending
        if not pending:return
        if now-pending.commanded_at>1.2:
            self.failed=True; self.metrics['SHOTS_UNCONFIRMED']+=1
            self.shots[-1].update(confirmation_result='SHOT_CONFIRMATION_FAILED')
            self.pending=None
            return
        if packet.timestamp<=pending.source_time or packet.received_at<=pending.commanded_at:return
        if (pending.baseline_tip is not None and tip is not None
                and tip<pending.baseline_tip-.12*pending.target[2] and pending.movement_at is None):
            pending.movement_at=packet.received_at
        if record['runtime_state']!='PLAYING' or not tracked or not tracked.valid:return
        target=record['target']
        if (target is None or hypot(target[0]-pending.target[0],target[1]-pending.target[1])>.06*pending.target[2]
                or abs(target[2]/pending.target[2]-1)>.10):return
        for k in tracked.probable_knives:
            if (k.identifier not in pending.identifiers and circular_distance(k.angle_deg,self.impact_angle)<15
                    and pending.movement_at is not None and pending.impact_at is None):
                pending.impact_at=packet.received_at; pending.candidate_id=k.identifier
        observed=tracked.confirmed_observed
        ids={k.identifier for k in observed}
        valid=(pending.movement_at is not None and pending.impact_at is not None
               and pending.candidate_id in ids and pending.identifiers.issubset(ids)
               and len(observed)==pending.count+1 and not record['knife_count_uncertain'])
        pending.confirmation_frames=pending.confirmation_frames+1 if valid else 0
        if pending.confirmation_frames>=2:
            self.metrics['SHOTS_CONFIRMED']+=1
            self.shots[-1].update(confirmation_result='CONFIRMED',
                                  confirmation_latency_ms=(now-pending.commanded_at)*1000,
                                  impact_observation_ms=(pending.impact_at-pending.commanded_at)*1000)
            if self.calibrating:
                accepted=self.calibration.add(pending.commanded_at,pending.movement_at,pending.impact_at)
                self.shots[-1]['calibration_sample_accepted']=accepted
            self.pending=None
            self.stable_since=None

    def decide(self,record,tracked,raw,rotation,packet,snapshot,now,tip=None):
        self.last_prediction=None
        self.last_rotation=rotation
        def wait(reason):
            self.metrics[reason]+=1
            self.last_decision=reason
            return TapPermit(False,now,packet.sequence_number,reason)
        if self.failed:return wait('SHOT_CONFIRMATION_FAILED')
        if self.pending:return wait('WAIT_PENDING_SHOT')
        if (record['runtime_state']!='PLAYING' or record['watchdog_status']!='WAIT'
                or snapshot is None or snapshot.error or not 0<=now-snapshot.observed_at<=.75
                or not 0<=now-packet.received_at<=.08):
            self.stable_since=None
            self.last_count=None
            self.last_target=None
            return wait('WAIT_UNKNOWN')
        target=record['target']
        if not target or not tracked or not raw or not raw.valid:
            self.stable_since=None
            return wait('WAIT_UNKNOWN')
        if tip is None:return wait('WAIT_UNKNOWN')
        unstable=(self.last_target is None or hypot(target[0]-self.last_target[0],target[1]-self.last_target[1])>.035*target[2]
                  or abs(target[2]/self.last_target[2]-1)>.06)
        self.last_target=target
        if unstable:self.stable_since=now
        if self.stable_since is None:self.stable_since=now
        if now-self.stable_since<.25:return wait('WAIT_UNKNOWN')
        if record['knife_count_uncertain']:
            self.stable_since=None
            return wait('WAIT_UNCERTAIN')
        count=tracked.count
        if self.last_count is not None and abs(count-self.last_count)>=3:
            self.failed=True
            return wait('UNEXPECTED_COUNT_JUMP')
        self.last_count=count
        if not rotation.valid or not rotation.stable or rotation.reversing:return wait('WAIT_ROTATION')
        if not self.calibration.valid and not self.calibrating:return wait('WAIT_CALIBRATION')
        visible=not any(circular_distance(a,self.impact_angle)<25
                        for a in raw.diagnostics.get('hud_occluded_angles',()))
        if not visible:return wait('WAIT_UNCERTAIN')
        if self.calibrating and not self.calibration.valid:
            # Bootstrap is NOT a flight calibration: require an open swept arc
            # across the entire bounded commissioning horizon, very slow motion.
            if abs(rotation.angular_velocity_deg_s)>90:return wait('WAIT_ROTATION')
            upper=min(.65,max(s['total_s'] for s in self.calibration.samples)+.15) if self.calibration.samples else .65
            horizons=[.05+i*(upper-.05)/12 for i in range(13)]
            predictions=[predict_shot(tracked.angles_deg,rotation,timestamp=packet.timestamp,
                                      horizon_s=h,timing_error_s=.10,impact_angle_deg=self.impact_angle,
                                      uncertain=False,impact_sector_visible=True,
                                      occluded_angles=raw.diagnostics.get('hud_occluded_angles',())) for h in horizons]
            prediction=min(predictions,key=lambda p:(p.nearest_predicted_distance_deg or 0)-(p.required_margin_deg or 180))
            self.last_prediction=prediction
            if any(p.decision!=ShotSafety.SAFE for p in predictions):return wait('WAIT_UNSAFE')
        else:
            prediction=predict_shot(tracked.angles_deg,rotation,timestamp=packet.timestamp,
                                    horizon_s=self.calibration.median_s+.04,timing_error_s=self.calibration.timing_error_s,
                                    impact_angle_deg=self.impact_angle,uncertain=False,impact_sector_visible=True,
                                    occluded_angles=raw.diagnostics.get('hud_occluded_angles',()))
        self.last_prediction=prediction; self.last_rotation=rotation
        if prediction.decision!=ShotSafety.SAFE:return wait('WAIT_UNSAFE' if prediction.decision==ShotSafety.UNSAFE else 'WAIT_UNKNOWN')
        self.metrics['WOULD_FIRE']+=1
        self.last_decision='WOULD_FIRE'
        return TapPermit(True,packet.received_at,packet.sequence_number,'SAFE')

    def fired(self,record,tracked,packet,tip,command_at):
        if self.pending or self.failed:raise RuntimeError('Outstanding shot or latched failure')
        self.pending=PendingShot(command_at,packet.timestamp,tracked.count,
                                 {k.identifier for k in tracked.knives},tuple(record['target']),tip)
        self.metrics['REAL_SHOTS_SENT']+=1
        self.shots.append(dict(shot_number=len(self.shots)+1,decision_timestamp=command_at,
                               input_transport='ADB',impact_prediction_dt_ms=(self.calibration.median_s or .65)*1000,
                               rotation_velocity_deg_s=self.last_rotation.angular_velocity_deg_s,
                               predicted_nearest_distance_deg=self.last_prediction.nearest_predicted_distance_deg,
                               collision_margin_deg=self.last_prediction.required_margin_deg,
                               confirmation_result='PENDING'))
