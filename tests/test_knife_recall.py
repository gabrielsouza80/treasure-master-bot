"""Close blades, bounded memory, reset negatives and benchmark correctness."""
import unittest

from src.states.game_state import GameState
from src.vision.knife_detector import detect_knives, KnifeDetectionResult
from src.vision.knife_tracker import KnifeTracker
from tests.test_knife_detection import synthetic, observation
from tools.knife_benchmark import match_angles, evaluate

PLAYING=GameState.PLAYING
TARGET=(250,320,90)


class RecallTests(unittest.TestCase):
    def test_close_blades_and_wraparound_are_separate(self):
        for angles in ((40,50),(359,9)):
            frame,target=synthetic(angles)
            raw=detect_knives(frame,target,state=PLAYING)
            self.assertEqual(raw.count,2,raw.angles_deg)
            self.assertEqual(len(match_angles(angles,raw.angles_deg,3)),2)
            tracker=KnifeTracker()
            for i in range(4):
                result=tracker.update(raw,target,i*.02,state=PLAYING)
            self.assertEqual(result.count,2)

    def test_single_loss_is_uncertain_and_sustained_loss_expires(self):
        tracker=KnifeTracker()
        for i in range(3):tracker.update(observation(359),TARGET,i*.02,state=PLAYING)
        result=tracker.update(observation(),TARGET,.06,state=PLAYING)
        self.assertEqual(result.count,1)
        self.assertEqual(len(result.occluded_tracks),1)
        self.assertTrue(result.diagnostics['count_uncertain'])
        result=tracker.update(observation(),TARGET,.08,state=PLAYING)
        self.assertEqual(result.count,0)

    def test_hud_hold_requires_motion_support_and_expires(self):
        tracker=KnifeTracker()
        for i in range(3):tracker.update(observation(0,90,180),TARGET,i*.02,state=PLAYING)
        partial=KnifeDetectionResult(True,observation(0,90).candidates,
                                     {'hud_occluded_angles':[180.]})
        for i in range(3,31):
            result=tracker.update(partial,TARGET,i*.02,state=PLAYING)
            self.assertEqual(result.count,3)
            self.assertEqual(len(result.confirmed_observed),2)
            self.assertEqual(len(result.occluded_tracks),1)
        result=tracker.update(partial,TARGET,.66,state=PLAYING)
        self.assertEqual(result.count,2)
        # A HUD tag alone cannot preserve tracks when shared motion is absent.
        only_one=KnifeDetectionResult(True,observation(0).candidates,partial.diagnostics)
        result=tracker.update(only_one,TARGET,.68,state=PLAYING)
        self.assertEqual(result.count,1)

    def test_dense_count_stays_stable_then_stage_and_unknown_clear(self):
        tracker=KnifeTracker()
        angles=(359,9,45,90,140,180,230,280)
        for i in range(3):tracker.update(observation(*angles),TARGET,i*.02,state=PLAYING)
        for i in range(3,8):
            moved=[(a+i)%360 for a in angles]
            result=tracker.update(observation(*moved[:-1]),TARGET,i*.02,state=PLAYING)
            self.assertEqual(result.count,8)
        reset=tracker.update(observation(10), (300,320,90),.16,state=PLAYING)
        self.assertEqual(reset.count,0)
        self.assertEqual(reset.diagnostics['reset'],'target_discontinuity')
        self.assertEqual(len(reset.probable_knives),1)
        reset=tracker.update(observation(10),TARGET,.18,state=GameState.UNKNOWN)
        self.assertFalse(reset.valid)
        self.assertEqual(reset.probable_knives,())

    def test_complete_one_frame_flash_keeps_uncertain_tracks(self):
        tracker=KnifeTracker()
        for i in range(3):tracker.update(observation(10,90,180),TARGET,i*.02,state=PLAYING)
        result=tracker.update(observation(),TARGET,.06,state=PLAYING)
        self.assertEqual(result.count,3)
        self.assertEqual(result.diagnostics['observation_quality'],0)
        self.assertTrue(result.diagnostics['count_uncertain'])
        result=tracker.update(observation(12,92,182),TARGET,.08,state=PLAYING)
        self.assertEqual(result.count,3)
        self.assertEqual(len(result.confirmed_observed),3)


class BenchmarkTests(unittest.TestCase):
    def test_optimal_one_to_one_matching_not_greedy_or_double_counted(self):
        # Greedy matching 0->1 would prevent matching 3; optimum matches both.
        pairs=match_angles([0,3],[1,358],3)
        self.assertEqual(len(pairs),2)
        self.assertEqual(len({p[1] for p in pairs}),2)
        self.assertEqual(len(match_angles([359,1],[0],3)),1)
        self.assertEqual(match_angles([90],[180],6),())

    def test_metrics_separate_misses_extras_and_excluded_labels(self):
        labels=dict(angle_tolerance_deg=6,frames=[
            dict(frame=1,timestamp=.1,expected_count=2,angles_deg=[359,90],categories=[]),
            dict(frame=2,timestamp=.2,expected_count=0,angles_deg=[],categories=[]),
            dict(frame=3,uncertain=True)])
        rows={1:dict(state='PLAYING',time='.1',stable_angles='[1]'),
              2:dict(state='PLAYING',time='.2',stable_angles='[180]')}
        result=evaluate(labels,rows)
        self.assertEqual(result['labeled_frames'],2)
        self.assertEqual(result['uncertain_excluded'],1)
        self.assertEqual(result['count_exact_accuracy'],0)
        self.assertEqual(result['count_mae'],1)
        self.assertEqual(result['undercount_rate'],.5)
        self.assertEqual(result['overcount_rate'],.5)
        self.assertEqual(result['angle_recall'],.5)
        self.assertEqual(result['angle_precision'],.5)
        self.assertEqual(result['angle_mae_deg'],2)
        rows[1]['state']='UNKNOWN'
        with self.assertRaises(ValueError):evaluate(labels,rows)


if __name__=='__main__':unittest.main()
