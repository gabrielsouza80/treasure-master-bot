"""Offline uncertainty/rotation audit; never imports an input backend."""
import argparse
from collections import Counter
import csv
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cv2
from src.runtime.bot_runtime import BotRuntime
from src.android.frame_source import VideoFrameSource
from src.prediction.rotation_estimator import RotationEstimator
from src.vision.projectile import projectile_tip


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--csv',type=Path,required=True)
    args=parser.parse_args()
    runtime=BotRuntime(observation_only=True)
    rotation=RotationEstimator()
    source=VideoFrameSource(args.video)
    counts,reasons,directions=Counter(),Counter(),Counter()
    playing=uncertain=valid=frames=0
    started=perf_counter()
    args.csv.parent.mkdir(parents=True,exist_ok=True)
    with args.csv.open('x',newline='',encoding='utf-8') as output:
        writer=csv.DictWriter(output,fieldnames=['frame','timestamp','state','target','angles','tracks','uncertain','reasons',
                                                'rotation_valid','omega','acceleration','rotation_reason','tip_y'])
        writer.writeheader()
        try:
            while (packet:=source.read()) is not None:
                record=runtime.process(packet)
                result=rotation.update(runtime.last_tracking,packet.timestamp)
                state=record['game_state']
                counts[state]+=1
                if state=='PLAYING':
                    playing+=1;uncertain+=record['knife_count_uncertain']
                    reasons.update(record['count_uncertain_reasons'])
                    valid+=result.valid
                    if result.valid:directions[result.direction]+=1
                tracked=runtime.last_tracking
                writer.writerow(dict(frame=packet.sequence_number,timestamp=packet.timestamp,state=state,
                                     target=json.dumps(record['target']),angles=json.dumps(record['knife_angles_deg']),
                                     tracks=json.dumps([asdict(k) for k in tracked.knives]) if tracked else '[]',
                                     uncertain=record['knife_count_uncertain'],reasons=json.dumps(record['count_uncertain_reasons']),
                                     rotation_valid=result.valid,omega=result.angular_velocity_deg_s,
                                     acceleration=result.angular_acceleration_deg_s2,rotation_reason=result.reason,
                                     tip_y=projectile_tip(packet.frame,record['target'])))
                frames+=1
        finally:source.close()
    summary=dict(frames=frames,state_counts=dict(counts),uncertain_frames=uncertain,
                 uncertainty_rate=uncertain/playing if playing else None,reasons=dict(reasons),
                 rotation_valid_frames=valid,directions=dict(directions),processing_fps=frames/(perf_counter()-started))
    args.report.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
