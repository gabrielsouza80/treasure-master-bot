"""One outstanding shot, no recovery actions, latched confirmation failure."""
from collections import Counter
from dataclasses import asdict,dataclass
from math import hypot
from src.android.device import CurrentApp
from src.android.gameplay_input import TapPermit,CalibrationTapPermit
from src.prediction.commissioning import CleanTargetObserver,predict_commissioning
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
    def __init__(self,calibration,*,calibrating=False,impact_angle=180.,
                 expected_app=CurrentApp('com.gimica.treasuremaster',
                                         'com.unity3d.player.UnityPlayerActivity')):
        self.calibration=calibration
        self.calibrating=calibrating
        self.impact_angle=impact_angle
        self.expected_app=expected_app
        self.prediction_horizon_s=None
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
        self.last_wait_detail=None
        self.clean_target=CleanTargetObserver(expected_app,impact_angle)
        self.last_clean_evidence=None
        self.last_commissioning_interval=None
        self.commissioning_state='WAIT_FOR_CLEAN_TARGET' if calibrating else None

    def check_pending_timeout(self,now):
        """A missing frame must not postpone the confirmation deadline."""
        pending=self.pending
        if pending and now-pending.commanded_at>1.2:
            self.reject_pending('SHOT_CONFIRMATION_FAILED')
            return True
        return False

    def reject_pending(self,reason):
        self.failed=True
        self.metrics['SHOTS_UNCONFIRMED']+=1
        if self.calibrating:
            self.metrics['CALIBRATION_REJECTED']+=1
            self.commissioning_state='PAUSED_REJECTED'
        self.shots[-1]['confirmation_result']=reason
        self.pending=None

    def observe_confirmation(self,record,tracked,packet,tip,now):
        if self.check_pending_timeout(now):return
        pending=self.pending
        if not pending:return
        if packet.timestamp<=pending.source_time or packet.received_at<=pending.commanded_at:return
        if (pending.baseline_tip is not None and tip is not None
                and tip<pending.baseline_tip-.12*pending.target[2] and pending.movement_at is None):
            pending.movement_at=packet.received_at
            self.shots[-1]['first_movement_ms']=(pending.movement_at-pending.commanded_at)*1000
            if self.calibrating:self.commissioning_state='MEASURE_IMPACT'
        if record['runtime_state']!='PLAYING' or not tracked or not tracked.valid:
            # Track IDs are reset outside gameplay; never match a later target's
            # recycled identifiers against this outstanding shot.
            self.reject_pending('CONFIRMATION_INTERRUPTED')
            return
        target=record['target']
        if (target is None or hypot(target[0]-pending.target[0],target[1]-pending.target[1])>.06*pending.target[2]
                or abs(target[2]/pending.target[2]-1)>.10):
            self.reject_pending('TARGET_CHANGED_DURING_SHOT')
            return
        births=[k for k in tracked.probable_knives if k.identifier not in pending.identifiers]
        if len(births)>1:
            self.reject_pending('AMBIGUOUS_NEW_TRACKS')
            return
        for k in births:
            if (k.identifier not in pending.identifiers and circular_distance(k.angle_deg,self.impact_angle)<15
                    and pending.movement_at is not None and pending.impact_at is None):
                pending.impact_at=packet.received_at; pending.candidate_id=k.identifier
                self.shots[-1]['impact_observation_ms']=(pending.impact_at-pending.commanded_at)*1000
                if self.calibrating:self.commissioning_state='CONFIRM_NEW_KNIFE'
        observed=tracked.confirmed_observed
        ids={k.identifier for k in observed}
        valid=(pending.movement_at is not None and pending.impact_at is not None
               and pending.candidate_id in ids and pending.identifiers.issubset(ids)
               and len(observed)==pending.count+1 and not record['knife_count_uncertain'])
        pending.confirmation_frames=pending.confirmation_frames+1 if valid else 0
        if pending.confirmation_frames>=2:
            self.metrics['SHOTS_CONFIRMED']+=1
            self.shots[-1].update(confirmation_result='CONFIRMED',
                                  confirmation_latency_ms=(packet.received_at-pending.commanded_at)*1000,
                                  first_movement_ms=(pending.movement_at-pending.commanded_at)*1000,
                                  movement_to_impact_ms=(pending.impact_at-pending.movement_at)*1000,
                                  impact_observation_ms=(pending.impact_at-pending.commanded_at)*1000)
            if self.calibrating:
                accepted=self.calibration.add(pending.commanded_at,pending.movement_at,pending.impact_at,packet.received_at)
                self.shots[-1]['calibration_sample_accepted']=accepted
                self.metrics['CALIBRATION_ACCEPTED' if accepted else 'CALIBRATION_REJECTED']+=1
                if not accepted:self.failed=True
                self.commissioning_state=('PAUSED_REJECTED' if not accepted else
                                          'COMPLETE' if self.calibration.valid else 'STORE_SAMPLE')
            self.pending=None
            self.stable_since=None

    def decide(self,record,tracked,raw,rotation,packet,snapshot,now,tip=None):
        self.last_prediction=None
        self.last_rotation=rotation
        self.prediction_horizon_s=None
        self.last_wait_detail=None
        self.last_commissioning_interval=None
        self.last_clean_evidence=None
        def wait(reason):
            self.metrics[reason]+=1
            self.last_decision=reason
            return TapPermit(False,now,packet.sequence_number,reason)
        if self.failed:return wait('SHOT_CONFIRMATION_FAILED')
        if self.pending:return wait('WAIT_PENDING_SHOT')
        if self.calibrating:
            if self.calibration.valid:return wait('CALIBRATION_COMPLETE')
            if self.metrics['REAL_SHOTS_SENT']>=3:return wait('COMMISSIONING_SHOT_LIMIT')
            self.last_clean_evidence=self.clean_target.update(record,tracked,raw,packet,snapshot,now,tip)
            if self.last_clean_evidence.true_zero:
                self.commissioning_state='COMMISSION_SHOT_ALLOWED'
                self.last_decision='WOULD_COMMISSION_TRUE_ZERO'
                self.metrics[self.last_decision]+=1
                return CalibrationTapPermit(True,packet.received_at,packet.sequence_number,
                                             'COMMISSION_TRUE_ZERO','TRUE_ZERO')
        if (record['runtime_state']!='PLAYING' or record['watchdog_status']!='WAIT'
                or snapshot is None or snapshot.error or not 0<=now-snapshot.observed_at<=.75
                or snapshot.app != self.expected_app
                or not 0<=now-packet.received_at<=.08):
            self.stable_since=None
            self.last_count=None
            self.last_target=None
            return wait('WAIT_UNKNOWN')
        target=record['target']
        if not target or not tracked or not tracked.valid or not raw or not raw.valid:
            self.stable_since=None
            return wait('WAIT_UNKNOWN')
        if tip is None:
            self.last_wait_detail='NO_WAITING_PROJECTILE'
            return wait('WAIT_UNKNOWN')
        unstable=(self.last_target is None or hypot(target[0]-self.last_target[0],target[1]-self.last_target[1])>.035*target[2]
                  or abs(target[2]/self.last_target[2]-1)>.06)
        self.last_target=target
        if unstable:self.stable_since=now
        if self.stable_since is None:self.stable_since=now
        if now-self.stable_since<.25:
            self.last_wait_detail='TARGET_STABILIZATION'
            return wait('WAIT_UNKNOWN')
        if record['knife_count_uncertain']:
            self.stable_since=None
            return wait('WAIT_UNCERTAIN')
        count=tracked.count
        if self.last_count is not None and abs(count-self.last_count)>=3:
            self.failed=True
            return wait('UNEXPECTED_COUNT_JUMP')
        self.last_count=count
        if not rotation.valid or not rotation.stable or rotation.reversing:return wait('WAIT_ROTATION')
        # Calibration mode may refine measured timing, never bypass its gate.
        # An unmeasured commissioning horizon cannot justify a real tap.
        if not self.calibration.valid and not (self.calibrating and self.calibration.has_recent_samples):
            self.commissioning_state='WAIT_FOR_CLEAN_TARGET' if self.calibrating else None
            return wait('WAIT_CALIBRATION')
        visible=not any(circular_distance(a,self.impact_angle)<25
                        for a in raw.diagnostics.get('hud_occluded_angles',()))
        if not visible:return wait('WAIT_UNCERTAIN')
        self.prediction_horizon_s=self.calibration.median_s+.04
        if self.calibrating:
            prediction,self.last_commissioning_interval=predict_commissioning(
                tracked.angles_deg,rotation,self.calibration,timestamp=packet.timestamp,
                impact_angle_deg=self.impact_angle,occluded_angles=raw.diagnostics.get('hud_occluded_angles',()))
        else:
            prediction=predict_shot(tracked.angles_deg,rotation,timestamp=packet.timestamp,
                                horizon_s=self.prediction_horizon_s,timing_error_s=self.calibration.timing_error_s,
                                impact_angle_deg=self.impact_angle,uncertain=False,impact_sector_visible=True,
                                occluded_angles=raw.diagnostics.get('hud_occluded_angles',()))
        self.last_prediction=prediction; self.last_rotation=rotation
        if prediction.decision!=ShotSafety.SAFE:return wait('WAIT_UNSAFE' if prediction.decision==ShotSafety.UNSAFE else 'WAIT_UNKNOWN')
        self.metrics['WOULD_FIRE']+=1
        self.last_decision='WOULD_FIRE'
        if self.calibrating:
            self.commissioning_state='COMMISSION_SHOT_ALLOWED'
            return CalibrationTapPermit(True,packet.received_at,packet.sequence_number,
                                         'COMMISSION_MEASURED_ENVELOPE','MEASURED_ENVELOPE')
        return TapPermit(True,packet.received_at,packet.sequence_number,'SAFE')

    def fired(self,record,tracked,packet,tip,command_at):
        if self.pending or self.failed:raise RuntimeError('Outstanding shot or latched failure')
        self.pending=PendingShot(command_at,packet.timestamp,tracked.count,
                                 {k.identifier for k in tracked.knives},tuple(record['target']),tip)
        self.metrics['REAL_SHOTS_SENT']+=1
        if self.calibrating:self.commissioning_state='MEASURE_MOVEMENT'
        self.shots.append(dict(shot_number=len(self.shots)+1,decision_timestamp=command_at,
                               input_transport='ADB',impact_prediction_dt_ms=self.prediction_horizon_s*1000 if self.prediction_horizon_s else None,
                               rotation_velocity_deg_s=self.last_rotation.angular_velocity_deg_s if self.last_rotation.valid else None,
                               predicted_nearest_distance_deg=self.last_prediction.nearest_predicted_distance_deg if self.last_prediction else None,
                               collision_margin_deg=self.last_prediction.required_margin_deg if self.last_prediction else None,
                               commissioning=self.calibrating,commissioning_interval=self.last_commissioning_interval,
                               confirmation_result='PENDING'))
