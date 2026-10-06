"""Geometric radial knife candidates. No enemy or knife templates.

Candidates are observations, not safe-shot evidence. Supply confirmed PLAYING
explicitly; other states fail closed before any image processing.
"""
from dataclasses import dataclass, field
import math

import cv2
import numpy as np

from src.states.game_state import GameState

ANGLE_BINS = 720
MIN_SEPARATION_DEG = 8.
_RADII = np.linspace(.85, 1.90, 106, dtype=np.float32)
_THETA = np.arange(ANGLE_BINS, dtype=np.float32) * (2 * np.pi / ANGLE_BINS)
_SIN, _COS = np.sin(_THETA)[:, None], np.cos(_THETA)[:, None]
_BODY_RADII = np.linspace(.80, 1.35, 111, dtype=np.float32)
_BANDS = tuple((_RADII >= lo) & (_RADII <= hi)
               for lo, hi in ((1.02, 1.28), (1.28, 1.52), (1.52, 1.74)))


def normalize_angle(angle):
    value = float(angle)
    if not math.isfinite(value):
        raise ValueError('Angle must be finite')
    return value % 360.


def signed_angle_delta(a, b):
    """Shortest clockwise-positive displacement from b to a, in [-180, 180)."""
    return (normalize_angle(a) - normalize_angle(b) + 180.) % 360. - 180.


def circular_distance(a, b):
    return abs(signed_angle_delta(a, b))


@dataclass(frozen=True)
class KnifeCandidate:
    angle_deg: float
    score: float
    radial_support: tuple[float, float, float]
    visible_fraction: float = 1.


@dataclass(frozen=True)
class KnifeDetectionResult:
    valid: bool
    candidates: tuple[KnifeCandidate, ...] = ()
    diagnostics: dict = field(default_factory=dict)

    @property
    def angles_deg(self):
        return [item.angle_deg for item in self.candidates]

    @property
    def count(self):
        return len(self.candidates)

    @property
    def score(self):
        # No candidates is NOT proof that the target has no knives.
        return float(np.mean([c.score for c in self.candidates])) if self.candidates else 0.


def _polar(frame, x, y, radius, radii, step=1):
    return cv2.remap(frame,
                     (x + _SIN[::step] * radius * radii).astype(np.float32),
                     (y - _COS[::step] * radius * radii).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def _body_radius(frame, target):
    """Find a supported outer circular rim when Hough followed an inner ring.

    Broad radial differences tolerate small center errors. Median over angles
    discounts isolated knives/decorations; choose an outer supported peak only.
    This refines the knife ROI, never the state classifier's target/evidence.
    """
    x, y, radius = target
    body = _polar(frame, x, y, radius, _BODY_RADII, step=2).astype(np.float32)
    profile = np.median(np.max(np.abs(body[:, 14:] - body[:, :-14]), axis=2), axis=0)
    radii = _BODY_RADII[7:-7]
    threshold = max(20., float(profile.max()) * .35)
    peaks = [i for i in range(1, len(profile) - 1)
             if 1.08 < radii[i] < 1.26 and profile[i] >= profile[i - 1]
             and profile[i] > profile[i + 1] and profile[i] > threshold]
    return float(radius * radii[peaks[-1]]) if peaks else float(radius)


def hud_mask(world_x, world_y, width, height):
    """Screen-fixed sample layout: gift disk, boost/reward icons, speaker.

    Mask the gift's circular footprint rather than its old bounding rectangle.
    Unknown layouts still need calibration; these are occlusions, not evidence.
    """
    hud = ((world_x - .10 * width)**2 + (world_y - .315 * height)**2 < (.103 * width)**2)
    hud |= ((world_x < .16 * width) & (world_y > .19 * height) & (world_y < .26 * height))
    hud |= ((world_x > .877 * width) & (world_x < .923 * width)
            & (world_y > .286 * height) & (world_y < .317 * height))
    hud |= ((world_x > .85 * width) & (world_x < .98 * width)
            & (world_y > .19 * height) & (world_y < .26 * height))
    return hud


def detect_knives(frame, target, *, state=GameState.UNKNOWN, debug=False):
    """Return raw candidates, sorted in clockwise angles from the top.

    Detection runs only when state is explicitly confirmed PLAYING. Structured
    invalid results distinguish skipped frames from valid frames with no hits.
    The annulus is sampled directly at fixed size, independent of resolution.
    """
    if state != GameState.PLAYING:
        return KnifeDetectionResult(False, diagnostics={'skipped': 'not_playing'})
    if target is None:
        return KnifeDetectionResult(False, diagnostics={'skipped': 'no_target'})
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size):
        raise ValueError('Expected a nonempty uint8 BGR frame')
    if len(target) != 3 or not np.isfinite(target).all() or target[2] <= 0:
        raise ValueError('Expected finite (x, y, positive radius)')
    x, y, radius = target
    h, w = frame.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return KnifeDetectionResult(False, diagnostics={'skipped': 'center_outside_frame'})
    effective_radius = _body_radius(frame, target)
    polar = _polar(frame, x, y, effective_radius, _RADII)
    # Angular neighborhood estimates local background, with circular padding.
    padded = np.concatenate((polar[-30:], polar, polar[:30])).astype(np.float32)
    background = cv2.blur(padded, (1, 41))[30:-30]
    contrast = np.max(np.abs(polar.astype(np.float32) - background), axis=2)
    luminance = cv2.cvtColor(polar, cv2.COLOR_BGR2GRAY)
    foreground = (contrast > 22) & (luminance > 65)
    world_x = x + _SIN * effective_radius * _RADII
    world_y = y - _COS * effective_radius * _RADII
    # Screen-fixed gift/boost/speaker UI can line up with a target decoration
    # and mimic one long radial object. Treat those occluded pixels as unknown,
    # even when that also hides a real hilt; never turn UI into knife evidence.
    hud = hud_mask(world_x, world_y, w, h)
    foreground[hud] = False
    # Join small radial gaps in ornate knives, then allow narrow side edges.
    # Pad angle dimension explicitly: morphology must also wrap at 0 degrees.
    mask = np.concatenate((foreground[-6:], foreground, foreground[:6])).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 7), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 1), np.uint8))[6:-6]
    mask[hud] = 0  # Morphology must not grow evidence into masked UI.
    visibility = np.stack([(~hud[:, band]).mean(axis=1) for band in _BANDS])
    supports = np.stack([mask[:, band].mean(axis=1) for band in _BANDS])
    # Missing pixels are unknown, not negatives. Do not promote a nearly fully
    # hidden band: every band still needs >=45% directly visible radial pixels.
    supports = np.divide(supports, visibility, out=np.zeros_like(supports), where=visibility >= .45)
    profile = supports.min(axis=0)
    profile = np.convolve(np.r_[profile[-1:], profile, profile[:1]], np.ones(3) / 3, 'valid')
    # One peak per plateau, rather than selecting every high-scoring bin.
    # The latter can turn a wide ornate hilt/HUD overlap into several knives.
    maxima = ((profile > 0) & (profile >= np.roll(profile, 1))
              & (profile >= np.roll(profile, -1)))
    starts = np.flatnonzero(maxima & ~np.roll(maxima, 1))
    peaks = []
    for start in starts:
        length = 1
        while length < ANGLE_BINS and maxima[(start + length) % ANGLE_BINS]:
            length += 1
        peaks.append(float((start + (length - 1) / 2) % ANGLE_BINS))
    selected = []
    decisions = []
    for peak in sorted(peaks, key=lambda p: profile[round(p) % ANGLE_BINS], reverse=True):
        index = round(peak) % ANGLE_BINS
        score = float(profile[index])
        angle = float(peak * 360 / ANGLE_BINS)
        reason = None
        if score < .50:
            reason = 'threshold_or_occlusion'
        separate = True
        for existing in selected:
            delta = signed_angle_delta(existing.angle_deg, angle)
            arc = (index + np.sign(delta) * np.arange(round(abs(delta) * 2) + 1).astype(int)) % ANGLE_BINS
            close_support = (score >= .65 and existing.score >= .65
                             and supports[1, index] >= .85 and existing.radial_support[1] >= .85)
            if (abs(delta) < MIN_SEPARATION_DEG or
                    (abs(delta) < 13 and not close_support) or
                    profile[arc.astype(int)].min() > min(score, existing.score) - .12):
                separate = False
                reason = ('minimum_separation' if abs(delta) < MIN_SEPARATION_DEG else
                          'weak_close_peak' if abs(delta) < 13 and not close_support
                          else 'no_radial_valley')
                break
        if separate and reason is None:
            selected.append(KnifeCandidate(angle, score, tuple(float(v) for v in supports[:, index]),
                                           float(visibility[:, index].mean())))
        if debug:
            decisions.append(dict(angle_deg=angle, score=score, reason=reason or 'accepted',
                                  radial_support=supports[:, index].tolist(),
                                  band_visibility=visibility[:, index].tolist()))
    selected.sort(key=lambda c: c.angle_deg)
    diagnostics = {
        'effective_radius': effective_radius,
        'radius_refinement_ratio': effective_radius / radius,
        'annulus': [.85, 1.90], 'support_bands': [[1.02, 1.28], [1.28, 1.52], [1.52, 1.74]],
        'angular_bins': ANGLE_BINS, 'minimum_separation_deg': MIN_SEPARATION_DEG,
        'hud_masked_fraction': float(hud.mean()),
        'hud_occluded_angles': (np.flatnonzero(visibility.min(axis=0) < .85) / 2).tolist(),
        'partial_candidates': sum(c.visible_fraction < .95 for c in selected),
        'confidence_is_probability': False,
    }
    if debug:
        diagnostics.update(peak_decisions=decisions, radial_profile=profile.tolist(),
                           band_support=supports.tolist(), band_visibility=visibility.tolist())
    return KnifeDetectionResult(True, tuple(selected), diagnostics)
