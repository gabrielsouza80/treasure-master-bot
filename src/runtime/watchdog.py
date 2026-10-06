"""Monotonic freshness checks; recommendations never perform Android I/O."""
from dataclasses import dataclass
from math import isfinite
from src.states.runtime_state import RuntimeState


@dataclass(frozen=True)
class WatchdogResult:
    recommendation: str = 'WAIT'
    reason: str = 'healthy'


class Watchdog:
    def __init__(self, *, started_at, stale_seconds=2., unknown_seconds=20.):
        if not all(isfinite(v) for v in (started_at, stale_seconds, unknown_seconds)) or min(stale_seconds, unknown_seconds) <= 0:
            raise ValueError('Finite clock and positive watchdog timeouts required')
        self.last_fresh = started_at
        self.stale_seconds, self.unknown_seconds = stale_seconds, unknown_seconds
        self.last_sequence = self.last_timestamp = None
        self.state = None
        self.state_since = self.ui_since = started_at
        self.ui_signature = None
        self.target_lost_at = None
        self.had_target = False
        self.last_intent = None
        self.repeated_intents = 0

    def observe(self, sequence, timestamp, now, state, target_valid, *, ui_signature=None, intent=None):
        fresh = (isfinite(timestamp) and isfinite(now) and now >= self.last_fresh
                 and (self.last_sequence is None or sequence > self.last_sequence)
                 and (self.last_timestamp is None or timestamp > self.last_timestamp))
        if fresh:
            self.last_fresh, self.last_sequence, self.last_timestamp = now, sequence, timestamp
        if state != self.state:
            self.state, self.state_since = state, now
            self.repeated_intents = 0
        if ui_signature is not None and ui_signature != self.ui_signature:
            self.ui_signature, self.ui_since = ui_signature, now
            self.repeated_intents = 0
        if target_valid:
            self.had_target, self.target_lost_at = True, None
        elif state == RuntimeState.ADVERTISEMENT:
            self.target_lost_at = None
        elif self.had_target and self.target_lost_at is None:
            self.target_lost_at = now
        self.record_intent(intent)
        return fresh, self.check(now)

    def record_intent(self, intent):
        if intent and intent not in ('WAIT','RECHECK_UI','STOP'):
            self.repeated_intents = self.repeated_intents + 1 if intent == self.last_intent else 1
            self.last_intent = intent

    def check(self, now):
        if not isfinite(now) or now < self.last_fresh:
            return WatchdogResult('STOP','invalid_clock')
        if now - self.last_fresh >= self.stale_seconds:
            return WatchdogResult('STOP','stale_frames_or_timestamp')
        if self.repeated_intents >= 3:
            return WatchdogResult('RECHECK_UI','repeated_intent_without_progress')
        if self.state == RuntimeState.UNKNOWN and now - self.state_since >= self.unknown_seconds:
            return WatchdogResult('RECHECK_UI','persistent_unknown')
        if self.target_lost_at is not None and now - self.target_lost_at >= 1.:
            return WatchdogResult('RECHECK_UI','target_disappeared')
        if self.ui_signature and self.state != RuntimeState.PLAYING and now - self.ui_since >= 45.:
            return WatchdogResult('RECHECK_UI','unchanged_ui')
        return WatchdogResult()
