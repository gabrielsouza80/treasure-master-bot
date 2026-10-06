"""Bounded ad recovery proposals, never authorization or execution."""
from src.android.input_controller import ActionKind, ActionPlan
from src.states.runtime_state import RuntimeState


def plan_recovery(state, snapshot, *, now, entered_at, sequence, resolution,
                  expected_package=None, watchdog=None):
    if watchdog and watchdog.recommendation != 'WAIT':
        kind = ActionKind.STOP if watchdog.recommendation == 'STOP' else ActionKind.RECHECK_UI
        return ActionPlan(kind)
    if state != RuntimeState.ADVERTISEMENT:
        return ActionPlan(ActionKind.WAIT)
    dwell = now - entered_at
    if dwell < 5:
        return ActionPlan(ActionKind.WAIT)
    width, height = resolution
    if (snapshot and not snapshot.error and 0 <= now - snapshot.observed_at <= 2
            and snapshot.app.package == expected_package and width > 1 and height > 1):
        candidates = [c for c in snapshot.find_close_candidates() if c.actionable_evidence
                      and c.node.package == expected_package
                      and 0 <= c.node.bounds[0] < c.node.bounds[2] <= width
                      and 0 <= c.node.bounds[1] < c.node.bounds[3] <= height]
        # Multiple plausible controls are ambiguous; don't choose an arbitrary one.
        if len(candidates) == 1:
            x1,y1,x2,y2 = candidates[0].node.bounds
            return ActionPlan(ActionKind.CLOSE, ((x1+x2-1)/2/(width-1), (y1+y2-1)/2/(height-1)),
                              frame_sequence=sequence)
    # Future verified OpenCV fallback belongs here; no generic X template today.
    if dwell >= 90 and expected_package:
        return ActionPlan(ActionKind.RELAUNCH, package=expected_package, frame_sequence=sequence)
    if dwell >= 45:
        return ActionPlan(ActionKind.BACK, frame_sequence=sequence)
    return ActionPlan(ActionKind.RECHECK_UI)
