"""Independent collision margin; UNKNOWN never permits a shot."""
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from src.vision.knife_detector import normalize_angle, circular_distance


class ShotSafety(str,Enum):
    SAFE='SAFE'
    UNSAFE='UNSAFE'
    UNKNOWN='UNKNOWN'


@dataclass(frozen=True)
class ShotPrediction:
    decision: ShotSafety
    reason: str
    nearest_predicted_distance_deg: float | None = None
    required_margin_deg: float | None = None
    prediction_error_margin: float = 0.
    timing_margin: float = 0.
    predicted_angles: tuple = ()


def predict_shot(angles, rotation, *, timestamp, horizon_s, timing_error_s,
                 impact_angle_deg=180., geometry_margin_deg=28., uncertain=True,
                 impact_sector_visible=False,occluded_angles=()):
    values=(timestamp,horizon_s,timing_error_s,impact_angle_deg,geometry_margin_deg)
    if (not all(isfinite(v) for v in values) or not 0<horizon_s<=1
            or not 0<=timing_error_s<=.25 or not 12<=geometry_margin_deg<=60):
        return ShotPrediction(ShotSafety.UNKNOWN,'INVALID_TIMING_OR_MARGIN')
    if (uncertain or not impact_sector_visible or not angles or not rotation.valid
            or not rotation.stable or rotation.reversing
            or not 0<=timestamp-rotation.timestamp<=.08):
        return ShotPrediction(ShotSafety.UNKNOWN,'INCOMPLETE_OR_STALE_EVIDENCE')
    motion=(rotation.angular_velocity_deg_s,rotation.angular_acceleration_deg_s2,
            rotation.velocity_error_deg_s,rotation.residual_deg)
    if (not all(isfinite(v) for v in motion) or not all(isfinite(a) for a in angles)
            or not all(isfinite(a) for a in occluded_angles)
            or rotation.velocity_error_deg_s<0 or rotation.residual_deg<0
            or not isfinite(rotation.confidence) or rotation.confidence<.6):
        return ShotPrediction(ShotSafety.UNKNOWN,'NONFINITE_MOTION')
    omega=rotation.angular_velocity_deg_s
    acceleration=rotation.angular_acceleration_deg_s2
    displacement=omega*horizon_s+.5*acceleration*horizon_s*horizon_s
    predicted=tuple(normalize_angle(a+displacement) for a in angles)
    # Unmodeled acceleration and detector error are separate from knife widths.
    error=4.+rotation.residual_deg+rotation.velocity_error_deg_s*horizon_s+150*horizon_s*horizon_s
    timing=(abs(omega)+abs(acceleration)*horizon_s)*timing_error_s
    margin=geometry_margin_deg+error+timing
    if any(circular_distance(normalize_angle(a+displacement),impact_angle_deg)<=margin+2
           for a in occluded_angles):
        return ShotPrediction(ShotSafety.UNKNOWN,'OCCLUDED_FUTURE_COLLISION_SECTOR',
                              required_margin_deg=margin,prediction_error_margin=error,timing_margin=timing)
    nearest=min(circular_distance(a,impact_angle_deg) for a in predicted)
    return ShotPrediction(ShotSafety.SAFE if nearest>margin else ShotSafety.UNSAFE,
                          'CLEAR' if nearest>margin else 'COLLISION_ENVELOPE',nearest,margin,error,timing,predicted)
