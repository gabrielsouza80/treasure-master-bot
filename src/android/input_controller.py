"""Decision/guard boundary. This milestone has no executable input backend."""
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from src.states.runtime_state import RuntimeState


class ActionKind(str, Enum):
    WAIT = 'WAIT'
    RECHECK_UI = 'RECHECK_UI'
    STOP = 'STOP'
    GAMEPLAY_TAP = 'WOULD_FIRE'
    CLOSE = 'WOULD_CLICK_CLOSE'
    BACK = 'WOULD_PRESS_BACK'
    RELAUNCH = 'WOULD_RELAUNCH_GAME'


@dataclass(frozen=True)
class ActionPlan:
    kind: ActionKind
    point: tuple[float, float] | None = None
    package: str | None = None
    authorized: bool = False
    frame_sequence: int | None = None


@dataclass(frozen=True)
class GuardContext:
    state: RuntimeState
    frame_sequence: int
    fresh: bool = False
    foreground_verified: bool = False
    confidence: float = 0.
    knife_count_uncertain: bool = True
    recovery_verified: bool = False


def scale_point(point, width, height):
    """Normalized device coordinates, endpoints inclusive; no window offsets."""
    if width < 1 or height < 1 or len(point) != 2:
        raise ValueError('Invalid coordinates or resolution')
    if not all(isfinite(v) and 0 <= v <= 1 for v in point):
        raise ValueError('Coordinates must be finite and normalized')
    return round(point[0] * (width - 1)), round(point[1] * (height - 1))


class ActionGuard:
    def __init__(self, *, allowed_zones=(), forbidden_zones=()):
        for zone in (*allowed_zones, *forbidden_zones):
            if (len(zone) != 4 or not all(isfinite(v) and 0 <= v <= 1 for v in zone)
                    or zone[0] >= zone[2] or zone[1] >= zone[3]):
                raise ValueError('Invalid normalized zone')
        self.allowed_zones = tuple(allowed_zones)
        self.forbidden_zones = tuple(forbidden_zones)

    def evaluate(self, plan, context):
        if plan.kind in (ActionKind.WAIT, ActionKind.RECHECK_UI, ActionKind.STOP):
            return True, 'observation_only'
        if not plan.authorized:
            return False, 'no_explicit_authorization'
        if not context.fresh or plan.frame_sequence != context.frame_sequence:
            return False, 'stale_evidence'
        if not context.foreground_verified:
            return False, 'foreground_unverified'
        if plan.point is not None:
            try:
                scale_point(plan.point, 1, 1)  # Validate independently of device size.
            except (ValueError, TypeError):
                return False, 'invalid_coordinates'
            x, y = plan.point
            if any(a <= x <= c and b <= y <= d for a,b,c,d in self.forbidden_zones):
                return False, 'forbidden_zone'
        if plan.kind == ActionKind.GAMEPLAY_TAP:
            if context.state != RuntimeState.PLAYING:
                return False, 'not_playing'
            if (not isfinite(context.confidence) or context.confidence < 1.
                    or context.knife_count_uncertain):
                return False, 'uncertain_vision'
            if plan.point is None or not any(a <= plan.point[0] <= c and b <= plan.point[1] <= d
                                             for a,b,c,d in self.allowed_zones):
                return False, 'outside_launch_zone'
        elif plan.kind in (ActionKind.CLOSE, ActionKind.BACK, ActionKind.RELAUNCH):
            if not context.recovery_verified or context.state != RuntimeState.ADVERTISEMENT:
                return False, 'recovery_unverified'
            if plan.kind == ActionKind.CLOSE and plan.point is None:
                return False, 'missing_close_coordinates'
            if plan.kind == ActionKind.RELAUNCH and not plan.package:
                return False, 'missing_package'
        else:
            return False, 'unsupported_action'
        return True, 'guard_passed_dry_run_only'


class InputController:
    """Hard dry-run gate: no transport exists, even for an approved plan."""
    def __init__(self, guard=None, *, dry_run=True):
        if dry_run is not True:
            raise ValueError('Real Android input is unavailable in this milestone')
        self.guard = guard or ActionGuard()

    @property
    def dry_run(self):
        return True

    def submit(self, plan, context):
        allowed, reason = self.guard.evaluate(plan, context)
        return {'planned_action': plan.kind.value, 'action_allowed': allowed,
                'action_block_reason': reason, 'execution': 'DRY_RUN' if allowed else 'BLOCKED'}

    def tap(self, point, context, *, authorized=False):
        return self.submit(ActionPlan(ActionKind.GAMEPLAY_TAP, point,
                           authorized=authorized, frame_sequence=context.frame_sequence), context)

    def back(self, context, *, authorized=False):
        return self.submit(ActionPlan(ActionKind.BACK, authorized=authorized,
                           frame_sequence=context.frame_sequence), context)

    def launch_package(self, package, context, *, authorized=False):
        return self.submit(ActionPlan(ActionKind.RELAUNCH, package=package,
                           authorized=authorized, frame_sequence=context.frame_sequence), context)
