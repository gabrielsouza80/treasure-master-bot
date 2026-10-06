"""Geometric, circular-matching and independently reviewed video regressions."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.states.game_state import GameState, classify_game_state
from src.vision.target_detector import detect_target
from src.vision.knife_detector import (KnifeCandidate, KnifeDetectionResult,
    detect_knives, normalize_angle, circular_distance)
from src.vision.knife_tracker import KnifeTracker
from tools.inspect_video import annotate

VIDEO = ROOT / 'debug' / 'treasure-sample-2.mp4'


def observation(*angles):
    return KnifeDetectionResult(True, tuple(KnifeCandidate(normalize_angle(a), 1., (1., 1., 1.))
                                           for a in sorted(angles)))


def synthetic(angles=(), center=(250, 320), color=(210, 180, 120)):
    frame = np.full((850, 500, 3), 25, np.uint8)
    radius = 90
    cv2.circle(frame, center, radius, (90, 110, 140), -1)
    for angle in angles:
        radians = np.deg2rad(angle)
        def point(distance):
            return (round(center[0] + np.sin(radians) * distance),
                    round(center[1] - np.cos(radians) * distance))
        cv2.line(frame, point(radius * .99), point(radius * 1.76), color, 7)
    return frame, (*center, radius)


class GeometryTests(unittest.TestCase):
    def test_normalization_and_distance(self):
        for value, expected in ((-1,359), (360,0), (721,1), (-720,0)):
            self.assertEqual(normalize_angle(value), expected)
        self.assertEqual(circular_distance(359,1), 2)
        self.assertEqual(circular_distance(1,359), 2)
        self.assertEqual(circular_distance(90,270), 180)
        with self.assertRaises(ValueError):
            normalize_angle(float('nan'))

    def test_nonplaying_skips_processing(self):
        with patch('src.vision.knife_detector._polar', side_effect=AssertionError('Should not run')):
            result = detect_knives(None, (250,320,90), state=GameState.UNKNOWN)
            self.assertFalse(result.valid)
            self.assertEqual(result.count, 0)
            self.assertFalse(detect_knives(None, None).valid)

    def test_radial_shapes_colors_positions_and_sorted_angles(self):
        for center in ((250,320),(240,330)):
            for color in ((210,180,120),(180,180,180),(120,220,100),
                          (230,70,70),(70,70,230)):
                frame, target = synthetic((0,90,210), center, color)
                result = detect_knives(frame,target,state=GameState.PLAYING)
                self.assertTrue(result.valid)
                self.assertEqual(result.count,3, result.angles_deg)
                self.assertEqual(result.angles_deg, sorted(result.angles_deg))
                for expected in (0,90,210):
                    self.assertLess(min(circular_distance(expected,a) for a in result.angles_deg),5)
                self.assertTrue(all(0 <= a < 360 for a in result.angles_deg))
        frame,target = synthetic((359,))
        result = detect_knives(frame,target,state=GameState.PLAYING)
        self.assertEqual(result.count,1)
        self.assertLess(circular_distance(result.angles_deg[0],359),5)

    def test_waiting_projectile_and_short_decoration(self):
        frame,target = synthetic()
        # Waiting blade far below the annulus; flying blade not yet at the rim.
        cv2.line(frame,(250,650),(250,750),(230,230,230),8)
        cv2.line(frame,(250,445),(250,510),(230,230,230),8)
        # Short triangular protrusion, not a long knife.
        cv2.fillConvexPoly(frame,np.array([[320,275],[348,258],[328,298]]), (220,80,180))
        result = detect_knives(frame,target,state=GameState.PLAYING)
        self.assertEqual(result.count,0,result.angles_deg)

    def test_crossguard_is_one_radial_object(self):
        frame,target = synthetic((90,))
        cv2.line(frame,(365,300),(365,340),(220,220,220),5)
        result=detect_knives(frame,target,state=GameState.PLAYING)
        self.assertEqual(result.count,1,result.angles_deg)

    def test_invalid_and_clipped_target(self):
        with self.assertRaises(ValueError):
            detect_knives(None,(250,320,90),state=GameState.PLAYING)
        frame,target = synthetic()
        with self.assertRaises(ValueError):
            detect_knives(frame,(250,320,0),state=GameState.PLAYING)
        self.assertFalse(detect_knives(frame,(-10,320,90),state=GameState.PLAYING).valid)
        # Annulus outside the image must not crash or produce nonfinite angles.
        result=detect_knives(frame,(15,15,90),state=GameState.PLAYING)
        self.assertTrue(all(np.isfinite(result.angles_deg)))


class TrackerTests(unittest.TestCase):
    def test_wraparound_noise_and_state_reset(self):
        tracker=KnifeTracker()
        target=(250,320,90)
        ids=[]
        for i, angle in enumerate((357,359,1,3)):
            result=tracker.update(observation(angle),target,i*.02,state=GameState.PLAYING)
            self.assertEqual(result.count,0 if i<2 else 1)
            if result.count: ids.append(result.knives[0].identifier)
        self.assertEqual(ids,[1,1])
        self.assertEqual(result.angles_deg,[3])
        tracker.update(observation(5,120),target,.08,state=GameState.PLAYING)
        result=tracker.update(observation(7),target,.10,state=GameState.PLAYING)
        self.assertEqual(result.count,1)
        self.assertEqual(result.diagnostics['one_frame_candidates_rejected'],1)
        result=tracker.update(observation(9),target,.12,state=GameState.UNKNOWN)
        self.assertFalse(result.valid)
        self.assertEqual(result.count,0)

    def test_common_rotation_and_limited_occlusion(self):
        tracker=KnifeTracker(max_misses=2)
        target=(250,320,90)
        for i in range(3):
            result=tracker.update(observation(i*2,90+i*2,180+i*2),target,i*.02,state=GameState.PLAYING)
        self.assertEqual(result.count,3)
        for i in (3,4):
            result=tracker.update(observation(i*2,90+i*2),target,i*.02,state=GameState.PLAYING)
            self.assertEqual(result.count,3)
            self.assertEqual(result.diagnostics['held_tracks'],1)
            self.assertIn(180+i*2,result.angles_deg)
        result=tracker.update(observation(10,100),target,.1,state=GameState.PLAYING)
        self.assertEqual(result.count,2)
        result=tracker.update(observation(12,102),target,.4,state=GameState.PLAYING)
        self.assertEqual(result.count,0)
        self.assertEqual(result.diagnostics['reset'],'timestamp_discontinuity')

    def test_one_to_one_and_explicit_seek_reset(self):
        tracker=KnifeTracker()
        target=(250,320,90)
        for i in range(3):
            result=tracker.update(observation(10,24),target,i*.02,state=GameState.PLAYING)
        self.assertEqual(result.count,2)
        # One observation cannot update both tracks.
        result=tracker.update(observation(17),target,.06,state=GameState.PLAYING)
        self.assertEqual(result.count,1)
        tracker.reset()
        self.assertEqual(tracker.update(observation(15),target,.08,state=GameState.PLAYING).count,0)

    def test_hold_is_bounded_by_elapsed_time(self):
        tracker=KnifeTracker(max_misses=12)
        target=(250,320,90)
        for i in range(3):
            tracker.update(observation(0,90,180),target,i*.01,state=GameState.PLAYING)
        for time in (.10,.18):
            result=tracker.update(observation(0,90),target,time,state=GameState.PLAYING)
            self.assertEqual(result.count,3)
        result=tracker.update(observation(0,90),target,.25,state=GameState.PLAYING)
        self.assertEqual(result.count,2)


@unittest.skipUnless(VIDEO.exists(),'Local recorded video unavailable')
class KnifeVideoTests(unittest.TestCase):
    # Independently reviewed visible knife directions, rounded from the images.
    # These are NOT generated from detector outputs. ±6deg allows center/rim
    # uncertainty and the width of ornate hilts. Hidden overlaps are unlabeled.
    LABELS={397:[],460:[],622:[15,135,242],970:[8,330],1455:[170],
            1510:[45,63,90,112,137,158],2115:[8,130,248],
            2096:[0,120,240],2100:[0,120,240],2105:[2,122,241],
            2250:[12,34,152,191,206,253,270],
            2580:[],5785:[],6000:[],6083:[202,251,308],
            6500:[35,78,130,176,216,263,315,358]}

    @classmethod
    def setUpClass(cls):
        wanted=set(cls.LABELS)|set(range(528,546))|{1260,3060,4140,4680,5940}
        cls.frames={}
        cap=cv2.VideoCapture(str(VIDEO))
        if not cap.isOpened():raise AssertionError('Video did not open')
        try:
            for i in range(max(wanted)+1):
                if not cap.grab():raise AssertionError(f'Decode failed at {i}')
                if i in wanted:
                    ok,frame=cap.retrieve()
                    if not ok:raise AssertionError(f'Retrieve failed at {i}')
                    cls.frames[i]=(frame,cap.get(cv2.CAP_PROP_POS_MSEC)/1000)
        finally:cap.release()

    def test_reviewed_visible_counts_and_angles(self):
        for index,expected in self.LABELS.items():
            with self.subTest(frame=index):
                frame,_=self.frames[index];target=detect_target(frame)
                self.assertEqual(classify_game_state(frame,target).state,GameState.PLAYING)
                result=detect_knives(frame,target,state=GameState.PLAYING)
                self.assertTrue(result.valid)
                self.assertEqual(result.count,len(expected),result.angles_deg)
                remaining=result.angles_deg.copy()
                for angle in expected:
                    closest=min(remaining,key=lambda a:circular_distance(a,angle))
                    self.assertLessEqual(circular_distance(closest,angle),6)
                    remaining.remove(closest)

    def test_projectile_birth_needs_rotating_confirmation_and_overlay(self):
        tracker=KnifeTracker()
        results={}
        for index in range(528,546):
            frame,time=self.frames[index];target=detect_target(frame)
            raw=detect_knives(frame,target,state=GameState.PLAYING)
            stable=tracker.update(raw,target,time,state=GameState.PLAYING)
            results[index]=(raw,stable)
        self.assertEqual(results[539][1].count,1)
        self.assertEqual(results[540][0].count,2)
        self.assertEqual(results[540][1].count,1)
        self.assertEqual(results[542][1].count,2)
        frame,time=self.frames[542];target=detect_target(frame)
        raw,stable=results[542]
        view=annotate(frame,time,target,classify_game_state(frame,target),GameState.PLAYING,raw,stable)
        self.assertEqual(view.shape[0],820)

    def test_real_nonplaying_never_valid(self):
        for index in (1260,3060,4140,4680,5940):
            frame,_=self.frames[index];target=detect_target(frame)
            state=classify_game_state(frame,target).state
            self.assertEqual(state,GameState.UNKNOWN)
            result=detect_knives(frame,target,state=state)
            self.assertFalse(result.valid)


if __name__=='__main__':unittest.main()
