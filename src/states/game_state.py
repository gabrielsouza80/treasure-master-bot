"""Conservative layout detection calibrated offline on treasure-sample-2.mp4.

Scores are diagnostic evidence strengths, not calibrated probabilities.
No controller, OCR, enemy templates, or device access is involved.
"""
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np


class GameState(str, Enum):
    PLAYING = "PLAYING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StateResult:
    state: GameState
    score: float
    features: dict[str, float]
    failed_checks: tuple[str, ...]


def classify_game_state(frame, target=None):
    """Require target geometry AND independent HUD/layout gates.

    Input is a nonempty uint8 BGR image; target coordinates use input pixels.
    Unsupported aspect ratios fail closed. Layout coordinates are normalized.
    """
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size):
        raise ValueError("Expected a nonempty uint8 BGR frame")
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (360, 780), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

    def region(x1, y1, x2, y2):
        return hsv[round(y1 * 780):round(y2 * 780),
                   round(x1 * 360):round(x2 * 360)]

    diamonds = region(.37, .128, .63, .157)
    green = ((diamonds[:, :, 0] > 30) & (diamonds[:, :, 0] < 90)
             & (diamonds[:, :, 1] > 100) & (diamonds[:, :, 2] > 145))
    # Check each slot separately: compression can join adjacent green rims.
    slots = set()
    for slot, expected in enumerate((14, 36, 57, 78)):
        mask = green[:, expected - 10:expected + 11].astype(np.uint8)
        _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
        for box, center in zip(stats[1:], centers[1:]):
            _, _, bw, bh, area = box
            if (14 <= bw <= 21 and 12 <= bh <= 24 and 40 <= area <= 300
                    and abs(center[0] - 10) <= 5):
                slots.add(slot)
    stage = region(.38, .163, .63, .184)
    stage_white = float(((stage[:, :, 1] < 65) & (stage[:, :, 2] > 200)).mean())
    gem = region(.88, .13, .967, .173)
    gem_ratio = float(((gem[:, :, 0] > 135) & (gem[:, :, 0] < 175)
                       & (gem[:, :, 1] > 100) & (gem[:, :, 2] > 145)).mean())
    lane = region(.22, .56, .78, .78)
    lane_bright = float((lane[:, :, 2] > 150).mean())
    geometry = False
    if target is not None:
        x, y, radius = target
        geometry = (.455 <= x / w <= .545 and .34 <= y / h <= .40
                    and .205 <= radius / w <= .28)
    features = {
        "aspect": float(2.10 <= h / w <= 2.23),
        "target_geometry": float(geometry),
        "diamond_slots": len(slots) / 4,
        "stage_white": stage_white,
        "currency_magenta": gem_ratio,
        "lane_bright": lane_bright,
    }
    gates = {
        "aspect": bool(features["aspect"]),
        "target_geometry": geometry,
        "diamond_slots": len(slots) == 4,
        "stage_white": .15 <= stage_white <= .45,
        "currency_magenta": .18 <= gem_ratio <= .45,
        "clear_lane": lane_bright < .035,
    }
    failed = tuple(name for name, passed in gates.items() if not passed)
    score = sum(gates.values()) / len(gates)
    return StateResult(GameState.UNKNOWN if failed else GameState.PLAYING,
                       score, features, failed)


class StateStabilizer:
    """Confirm positives; revoke immediately when any mandatory evidence fails.

    Delayed loss could carry PLAYING into a menu/ad. Prefer UNKNOWN during a
    circle dropout. Feed each new frame once; reset after seeks/discontinuity.
    Six frames add ~112ms at this sample's average FPS, suppressing brief
    circle hits while a new enemy expands into place.
    """
    def __init__(self, confirm_frames=6):
        if confirm_frames < 1:
            raise ValueError("confirm_frames must be positive")
        self.confirm_frames = confirm_frames
        self.reset()

    def reset(self):
        self.positive_frames = 0
        self.state = GameState.UNKNOWN

    def update(self, result):
        if result.state == GameState.PLAYING:
            self.positive_frames += 1
            if self.positive_frames >= self.confirm_frames:
                self.state = GameState.PLAYING
        else:
            self.reset()
        return self.state
