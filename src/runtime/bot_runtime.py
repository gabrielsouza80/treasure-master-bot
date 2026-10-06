"""Observation-only orchestration of the existing vision pipeline."""
import json
from collections import Counter
from math import isfinite
from time import monotonic, perf_counter
from src.android.input_controller import InputController, GuardContext, ActionKind, ActionPlan
from src.states.game_state import GameState, StateStabilizer, classify_game_state
from src.states.runtime_state import RuntimeState, infer_runtime_state
from src.vision.target_detector import detect_target
from src.vision.knife_detector import detect_knives
from src.vision.knife_tracker import KnifeTracker
from src.runtime.watchdog import Watchdog
from src.recovery.planner import plan_recovery


class JsonlTelemetry:
    """Exclusive creation and bounded size; never writes hierarchy/serial/secrets."""
    def __init__(self, path, limit=10000):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('x', encoding='utf-8')
        self.limit, self.count = limit, 0

    def write(self, record):
        if self.count < self.limit:
            self.file.write(json.dumps(record, allow_nan=False) + '\n')
            self.count += 1

    def close(self):
        self.file.close()


class BotRuntime:
    def __init__(self, *, expected_package=None, verified_ad_activities=(), clock=monotonic,
                 observation_only=False, expected_game_activity=None):
        self.clock = clock
        self.observation_only = observation_only
        self.expected_game_activity = expected_game_activity
        self.expected_package, self.ad_activities = expected_package, tuple(verified_ad_activities)
        self.stabilizer, self.tracker = StateStabilizer(), KnifeTracker()
        self.controller = InputController()
        self.watchdog = Watchdog(started_at=clock())
        self.state, self.entered_at = RuntimeState.UNKNOWN, clock()
        self.counts, self.overhead_seconds = Counter(), 0.

    def process(self, packet, snapshot=None):
        started, now = perf_counter(), self.clock()
        prior_seq, prior_time = self.watchdog.last_sequence, self.watchdog.last_timestamp
        fresh = (isfinite(packet.timestamp) and isfinite(packet.received_at)
                 and 0 <= now - packet.received_at <= 1.
                 and (prior_seq is None or packet.sequence_number > prior_seq)
                 and (prior_time is None or packet.timestamp > prior_time))
        snapshot_fresh = (snapshot is not None and not snapshot.error
                          and 0 <= now - snapshot.observed_at <= 2.)
        snapshot = snapshot if snapshot_fresh else None
        overhead_before_vision = perf_counter() - started
        if fresh:
            if prior_time is not None and packet.timestamp - prior_time > .15:
                self.stabilizer.reset()
                self.tracker.reset()
            target = detect_target(packet.frame)
            result = classify_game_state(packet.frame, target)
            game_state = self.stabilizer.update(result)
            runtime_state = infer_runtime_state(game_state, snapshot, expected_package=self.expected_package,
                                               verified_ad_activities=self.ad_activities, previous=self.state)
            if (runtime_state == RuntimeState.PLAYING and self.expected_game_activity is not None
                    and (snapshot is None or snapshot.app.activity != self.expected_game_activity)):
                runtime_state = RuntimeState.UNKNOWN
            # Foreground uncertainty never carries tracked gameplay into other apps.
            knife_state = game_state if runtime_state == RuntimeState.PLAYING else GameState.UNKNOWN
            raw = detect_knives(packet.frame, target, state=knife_state)
            tracked = self.tracker.update(raw, target, packet.timestamp, state=knife_state)
            count = tracked.count if tracked.valid and knife_state == GameState.PLAYING else 0
            uncertain = (knife_state != GameState.PLAYING
                         or bool((tracked.diagnostics or {}).get('count_uncertain', True)))
            angles = tracked.angles_deg if tracked.valid and knife_state == GameState.PLAYING else []
        else:
            self.stabilizer.reset()
            self.tracker.reset()
            target, result, game_state = None, None, GameState.UNKNOWN
            runtime_state, count, uncertain = RuntimeState.STALLED, 0, True
            angles = []
        overhead_started = perf_counter()
        if runtime_state != self.state:
            self.state, self.entered_at = runtime_state, now
        if fresh:
            _, watchdog = self.watchdog.observe(packet.sequence_number, packet.timestamp, now,
                                                self.state, target is not None,
                                                ui_signature=snapshot.signature if snapshot else None)
        else:
            watchdog = self.watchdog.check(now)
        if watchdog.recommendation == 'STOP':
            self.state = RuntimeState.STALLED
            self.stabilizer.reset()
            self.tracker.reset()
        plan = (ActionPlan(ActionKind(watchdog.recommendation)) if self.observation_only else
                plan_recovery(self.state, snapshot, now=now, entered_at=self.entered_at,
                             sequence=packet.sequence_number,
                             resolution=(packet.frame.shape[1], packet.frame.shape[0]),
                             expected_package=self.expected_package, watchdog=watchdog))
        self.watchdog.record_intent(plan.kind.value)
        context = GuardContext(self.state, packet.sequence_number, fresh=fresh,
                               foreground_verified=bool(self.expected_package and snapshot
                                                        and snapshot.app.package == self.expected_package),
                               confidence=result.score if result else 0., knife_count_uncertain=uncertain)
        outcome = self.controller.submit(plan, context)
        record = dict(timestamp=packet.timestamp if isfinite(packet.timestamp) else None,
                      frame_sequence=packet.sequence_number, game_state=game_state.value,
                      runtime_state=self.state.value, target_valid=target is not None,
                      knife_count=count, knife_count_uncertain=uncertain,
                      target=tuple(map(int,target)) if target is not None else None,
                      knife_angles_deg=angles,
                      current_android_package=snapshot.app.package if snapshot else None,
                      watchdog_status=watchdog.recommendation, watchdog_reason=watchdog.reason, **outcome)
        self.counts[game_state.value] += 1
        self.overhead_seconds += overhead_before_vision + perf_counter() - overhead_started
        return record

    def check_without_frame(self):
        verdict = self.watchdog.check(self.clock())
        if verdict.recommendation == 'STOP':
            self.state = RuntimeState.STALLED
            self.stabilizer.reset()
            self.tracker.reset()
        return verdict
