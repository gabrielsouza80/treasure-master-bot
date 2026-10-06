"""Regression checks use independently reviewed times in the actual video.

The video is optional for source-only checkouts; this task must run with it.
"""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.states.game_state import GameState, StateStabilizer, classify_game_state
from src.vision.target_detector import detect_target
from tools.inspect_video import annotate, seek_to_time

VIDEO = ROOT / 'debug' / 'treasure-sample-2.mp4'


class StateTests(unittest.TestCase):
    def test_circle_alone_is_not_gameplay(self):
        frame = np.zeros((780, 360, 3), np.uint8)
        cv2.circle(frame, (180, 289), 87, (255, 255, 255), 5)
        self.assertEqual(classify_game_state(frame, (180, 289, 87)).state, GameState.UNKNOWN)
        for value in (0, 255):
            self.assertEqual(classify_game_state(np.full_like(frame, value), (180, 289, 87)).state,
                             GameState.UNKNOWN)

    def test_invalid_input(self):
        for frame in (None, np.zeros((0, 0, 3), np.uint8), np.zeros((10, 10), np.uint8)):
            with self.assertRaises(ValueError):
                classify_game_state(frame)

    def test_stabilizer_confirmation_loss_and_reset(self):
        from src.states.game_state import StateResult
        positive = StateResult(GameState.PLAYING, 1., {}, ())
        negative = StateResult(GameState.UNKNOWN, 0., {}, ('target_geometry',))
        stable = StateStabilizer(3)
        self.assertEqual(stable.update(positive), GameState.UNKNOWN)
        self.assertEqual(stable.update(positive), GameState.UNKNOWN)
        self.assertEqual(stable.update(negative), GameState.UNKNOWN)
        for _ in range(2):
            self.assertEqual(stable.update(positive), GameState.UNKNOWN)
        self.assertEqual(stable.update(positive), GameState.PLAYING)
        self.assertEqual(stable.update(negative), GameState.UNKNOWN)
        for _ in range(3):
            stable.update(positive)
        stable.reset()
        self.assertEqual(stable.update(positive), GameState.UNKNOWN)
        with self.assertRaises(ValueError):
            StateStabilizer(0)

    def test_brief_transition_hits_are_not_confirmed(self):
        from src.states.game_state import StateResult
        positive = StateResult(GameState.PLAYING, 1., {}, ())
        negative = StateResult(GameState.UNKNOWN, 0., {}, ('target_geometry',))
        stable = StateStabilizer()
        for _ in range(5):
            self.assertEqual(stable.update(positive), GameState.UNKNOWN)
        self.assertEqual(stable.update(negative), GameState.UNKNOWN)
        for _ in range(5):
            self.assertEqual(stable.update(positive), GameState.UNKNOWN)
        self.assertEqual(stable.update(positive), GameState.PLAYING)


@unittest.skipUnless(VIDEO.exists(), 'Local recorded video unavailable')
class VideoTests(unittest.TestCase):
    POSITIVE = (622, 1096, 1809, 2189, 2931, 6083)
    NEGATIVE = (0, 180, 1260, 3060, 3240, 3420, 3600, 3780, 3960,
                4140, 4320, 4500, 4680, 4860, 5040, 5220, 5400, 5580, 5760, 5940)

    @classmethod
    def setUpClass(cls):
        # Frame IDs independently reviewed from SEQUENTIAL decode. OpenCV's
        # random frame/time seeks are inaccurate on this variable-rate sample.
        cap = cv2.VideoCapture(str(VIDEO))
        if not cap.isOpened():
            raise AssertionError('Sample video did not open')
        wanted = set(cls.NEGATIVE) | set(cls.POSITIVE)
        for index in cls.POSITIVE:
            wanted.update(range(index + 1, index + 21))
        cls.frames = {}
        try:
            for index in range(max(wanted) + 1):
                if not cap.grab():
                    raise AssertionError(f'Decode stopped at frame {index}')
                if index in wanted:
                    ok, frame = cap.retrieve()
                    if not ok:
                        raise AssertionError(f'Retrieve failed at {index}')
                    if index not in cls.POSITIVE:
                        frame = cv2.resize(frame, (540, 1170), interpolation=cv2.INTER_AREA)
                    cls.frames[index] = frame
        finally:
            cap.release()

    def frame_at(self, index):
        return self.frames[index].copy()

    def test_reviewed_non_gameplay_with_even_a_supplied_circle(self):
        # Android home, title, restart, Continue, reward, ads, boost overlay.
        for timestamp in self.NEGATIVE:
            with self.subTest(frame=timestamp):
                frame = self.frame_at(timestamp)
                h, w = frame.shape[:2]
                for target in (detect_target(frame), (w // 2, round(h * .37), round(w * .245))):
                    self.assertEqual(classify_game_state(frame, target).state, GameState.UNKNOWN)

    def test_different_enemies_and_resolution(self):
        for timestamp in self.POSITIVE:
            for width in (540, 1080):
                with self.subTest(frame=timestamp, width=width):
                    frame = self.frame_at(timestamp)
                    h, w = frame.shape[:2]
                    frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
                    target = detect_target(frame)
                    self.assertIsNotNone(target)
                    result = classify_game_state(frame, target)
                    self.assertEqual(result.state, GameState.PLAYING, result.failed_checks)
                    self.assertEqual(classify_game_state(frame, None).state, GameState.UNKNOWN)
                    display = annotate(frame, timestamp, target, result, result.state)
                    self.assertEqual(display.shape[0], 820)

    def test_known_gameplay_sequences(self):
        for timestamp in self.POSITIVE:
            with self.subTest(frame=timestamp):
                stable = StateStabilizer()
                playing = 0
                for index in range(timestamp + 1, timestamp + 21):
                    frame = self.frame_at(index)
                    result = classify_game_state(frame, detect_target(frame))
                    playing += stable.update(result) == GameState.PLAYING
                # Require sustained confirmation, not merely one positive frame.
                self.assertGreaterEqual(playing, 12)

    def test_timestamp_seek(self):
        cap = cv2.VideoCapture(str(VIDEO))
        try:
            for seconds in (18., 20., 19.):
                self.assertTrue(seek_to_time(cap, seconds))
                ok, _ = cap.read()
                self.assertTrue(ok)
                actual = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                self.assertGreaterEqual(actual, seconds)
                self.assertLess(actual - seconds, .15)
        finally:
            cap.release()


if __name__ == '__main__':
    unittest.main()
