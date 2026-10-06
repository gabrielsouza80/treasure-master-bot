"""Short-horizon circular matching of observations, not a shot predictor."""
from dataclasses import dataclass
import numpy as np

from src.states.game_state import GameState
from src.vision.knife_detector import circular_distance, normalize_angle, signed_angle_delta


@dataclass
class _Track:
    identifier: int
    angle: float
    score: float
    hits: int = 1
    misses: int = 0
    confirmed: bool = False
    last_seen: float = 0.


@dataclass(frozen=True)
class TrackedKnife:
    identifier: int
    angle_deg: float
    score: float
    observed: bool


@dataclass(frozen=True)
class KnifeTrackingResult:
    valid: bool
    knives: tuple[TrackedKnife, ...] = ()
    diagnostics: dict = None

    @property
    def angles_deg(self):
        return [item.angle_deg for item in self.knives]

    @property
    def count(self):
        return len(self.knives)


class KnifeTracker:
    """One-to-one circular matches; three consecutive hits confirm a birth.

    Infer a shared step only from multiple consistent matched observations.
    Hold confirmed misses for at most 12 frames/200ms and only with that shared
    step. This bridges brief HUD occlusions without retaining counts forever.
    UNKNOWN, seeking, timestamp gaps, and large target changes reset.
    Counts can rise OR fall; neither monotonic counts nor fixed rotation is
    assumed. Diagnostics expose one-frame noise, count jumps, and residuals.
    """
    def __init__(self, confirm_frames=3, max_misses=12, match_distance=8.):
        if confirm_frames < 2 or max_misses < 0 or not 0 < match_distance < 180:
            raise ValueError('Invalid tracker parameters')
        self.confirm_frames = confirm_frames
        self.max_misses = max_misses
        self.match_distance = match_distance
        self.reset()

    def reset(self):
        self.tracks = []
        self.next_id = 1
        self.last_time = None
        self.last_target = None
        self.last_count = 0

    def update(self, detection, target, timestamp, *, state=GameState.UNKNOWN):
        if state != GameState.PLAYING or not detection.valid or target is None:
            had_tracks = bool(self.tracks)
            one_frame = sum(not t.confirmed and t.hits == 1 for t in self.tracks)
            self.reset()
            return KnifeTrackingResult(False, diagnostics={'reset': 'not_playing', 'had_tracks': had_tracks,
                                                           'one_frame_candidates_rejected': one_frame})
        reason = None
        if self.last_time is not None:
            dt = timestamp - self.last_time
            if dt <= 0 or dt > .15:
                reason = 'timestamp_discontinuity'
            else:
                x, y, radius = target
                px, py, pr = self.last_target
                if np.hypot(x - px, y - py) > .12 * pr or abs(radius / pr - 1) > .25:
                    reason = 'target_discontinuity'
        if reason:
            self.reset()
        self.last_time, self.last_target = float(timestamp), target
        candidates = detection.candidates
        previous = [t for t in self.tracks if t.confirmed and not t.misses]
        deltas = []
        for track in previous:
            if candidates:
                candidate = min(candidates, key=lambda c: circular_distance(c.angle_deg, track.angle))
                delta = signed_angle_delta(candidate.angle_deg, track.angle)
                if abs(delta) <= 12.:
                    deltas.append(delta)
        step, coherent = 0., False
        if len(deltas) >= 2:
            # Robust local mode: tolerate a new knife near an existing track.
            seed = max(deltas, key=lambda d: sum(abs(d - other) < 3 for other in deltas))
            cluster = [d for d in deltas if abs(d - seed) < 3]
            if len(cluster) >= 2 and len(cluster) >= len(deltas) * .6:
                step, coherent = float(np.median(cluster)), True
        pairs = sorted((circular_distance(c.angle_deg, normalize_angle(t.angle + step)), ti, ci)
                       for ti, t in enumerate(self.tracks) for ci, c in enumerate(candidates))
        matched_tracks, matched_candidates = set(), set()
        residuals = []
        for distance, ti, ci in pairs:
            if distance > self.match_distance:
                break
            if ti in matched_tracks or ci in matched_candidates:
                continue
            track, candidate = self.tracks[ti], candidates[ci]
            residuals.append(distance)
            track.angle, track.score = candidate.angle_deg, candidate.score
            track.hits = track.hits + 1 if not track.misses else 1
            track.misses = 0
            track.last_seen = float(timestamp)
            track.confirmed |= track.hits >= self.confirm_frames
            matched_tracks.add(ti)
            matched_candidates.add(ci)
        one_frame = 0
        kept = []
        for ti, track in enumerate(self.tracks):
            if ti not in matched_tracks:
                track.misses += 1
                if not track.confirmed:
                    one_frame += track.hits == 1
                    continue
                if (not coherent or track.misses > self.max_misses
                        or timestamp - track.last_seen > .20):
                    continue
                track.angle = normalize_angle(track.angle + step)
            kept.append(track)
        for ci, candidate in enumerate(candidates):
            if ci not in matched_candidates:
                # Avoid birthing a second ID beside a briefly held track.
                if any(circular_distance(candidate.angle_deg, t.angle) < 13 for t in kept):
                    continue
                kept.append(_Track(self.next_id, candidate.angle_deg, candidate.score,
                                   last_seen=float(timestamp)))
                self.next_id += 1
        # Prefer current evidence over a nearby extrapolated observation.
        unique = []
        for track in sorted(kept, key=lambda t: (t.misses, -t.hits, -t.score)):
            if not any(circular_distance(track.angle, other.angle) < 13 for other in unique):
                unique.append(track)
        kept = unique
        self.tracks = kept
        knives = tuple(sorted((TrackedKnife(t.identifier, normalize_angle(t.angle),
                                            t.score * (.9 ** t.misses), not t.misses)
                               for t in kept if t.confirmed), key=lambda k: k.angle_deg))
        jump = len(knives) - self.last_count
        self.last_count = len(knives)
        return KnifeTrackingResult(True, knives, {
            'reset': reason, 'shared_step_deg': step if coherent else None,
            'one_frame_candidates_rejected': one_frame,
            'large_count_jump': jump if abs(jump) >= 3 else 0,
            'max_match_residual_deg': max(residuals, default=0.),
            'held_tracks': sum(not k.observed for k in knives),
            'pending_candidates': sum(not t.confirmed for t in kept),
        })
