"""Offline statistics and compact knife review artifacts, separate from UI."""
from collections import Counter
import json
from pathlib import Path

import cv2
import numpy as np

REVIEW_FRAMES = {2096, 410, 460, 540, 622, 720, 820, 970, 1096, 1200,
                1455, 1510, 1680, 1809, 1920, 2115, 2250, 2390, 2580,
                2640, 2850, 2931, 3020, 2105, 6000, 6083, 6140, 6310, 6410, 6500}
REVIEW_FRAMES |= {397,532,539,542,1531,2375,2380,2385,2394,5785,2100}


def draw_knives(view, target, tracked):
    if tracked is None or not tracked.valid or target is None:
        return
    x, y, radius = target
    for knife in tracked.knives:
        radians = np.deg2rad(knife.angle_deg)
        point = (round(x + np.sin(radians) * radius * 1.65),
                 round(y - np.cos(radians) * radius * 1.65))
        color = (70, 230, 80) if knife.observed else (0, 180, 255)
        cv2.line(view, (x, y), point, color, max(1, round(radius / 110)))
        cv2.circle(view, point, max(3, round(radius / 45)), color, -1)
        cv2.putText(view, f'{knife.angle_deg:.0f}', point, cv2.FONT_HERSHEY_SIMPLEX,
                    radius / 500, color, 1, cv2.LINE_AA)


def draw_debug(view, target, raw, tracked):
    """Extra review-only evidence; normal player shows confirmed/held rays."""
    x,y,radius=target
    radius=raw.diagnostics.get('effective_radius',radius)
    for scale in (1.02,1.74):
        cv2.circle(view,(x,y),round(radius*scale),(120,120,120),1)
    h,w=view.shape[:2]
    cv2.circle(view,(round(.10*w),round(.315*h)),round(.103*w),(0,180,255),2)
    cv2.rectangle(view,(round(.877*w),round(.286*h)),(round(.923*w),round(.317*h)),(0,180,255),2)
    for decision in raw.diagnostics.get('peak_decisions',[]):
        if decision['score'] < .3: continue
        a=np.deg2rad(decision['angle_deg'])
        point=(round(x+np.sin(a)*radius*1.80),round(y-np.cos(a)*radius*1.80))
        color=(255,160,60) if decision['reason']=='accepted' else (80,80,255)
        cv2.circle(view,point,5,color,1)
    for knife in tracked.probable_knives:
        a=np.deg2rad(knife.angle_deg)
        point=(round(x+np.sin(a)*radius*1.65),round(y-np.cos(a)*radius*1.65))
        cv2.circle(view,point,8,(255,80,255),2)


def profile_chart(diagnostics):
    """Angle profile: three support bands, combined strength and HUD gaps."""
    chart=np.zeros((320,720,3),np.uint8)
    profile=np.asarray(diagnostics['radial_profile'])
    visibility=np.asarray(diagnostics['band_visibility'])
    for index in np.flatnonzero(visibility.min(axis=0)<.85):
        cv2.line(chart,(int(index),40),(int(index),265),(35,35,35),1)
    cv2.line(chart,(0,155),(719,155),(100,100,100),1)  # .50 acceptance threshold
    colors=((220,110,50),(170,50,220),(30,160,230),(80,240,80))
    for values,color in zip([*diagnostics['band_support'],profile],colors):
        points=np.stack((np.arange(720),265-220*np.asarray(values)),axis=1).astype(np.int32)
        cv2.polylines(chart,[points],False,color,1)
    for decision in diagnostics['peak_decisions']:
        if decision['score']<.3:continue
        point=(round(decision['angle_deg']*2)%720,round(265-220*decision['score']))
        cv2.circle(chart,point,4,(80,240,80) if decision['reason']=='accepted' else (70,70,255),-1)
    for angle in range(0,360,45):
        cv2.putText(chart,str(angle),(angle*2,285),0,.4,(255,255,255),1)
    cv2.putText(chart,'GREEN=combined RED=rejected GREY=HUD; bands B/P/O; threshold=.50',
                (5,20),0,.4,(255,255,255),1)
    cv2.putText(chart,'Angles clockwise from top. Support is evidence strength, not probability.',
                (5,307),0,.4,(255,255,255),1)
    return chart


class KnifeAnalysis:
    def __init__(self, review_dir=None):
        self.raw_counts, self.tracked_counts = Counter(), Counter()
        self.frames = 0
        self.detector_seconds = self.pipeline_seconds = 0.
        self.resets = self.one_frame = self.held_frames = 0
        self.reset_reasons = Counter()
        self.jumps, self.discontinuities = [], []
        self.previous_count = None
        self.raw_variation = self.tracked_variation = 0
        self.previous_raw = None
        self.review_dir = review_dir
        self.tiles = []

    def update(self, index, timestamp, frame, target, raw, tracked, detector_seconds, pipeline_seconds):
        if not raw.valid:
            self.one_frame += tracked.diagnostics.get('one_frame_candidates_rejected', 0)
            if self.previous_count is not None:
                self.resets += 1
                self.reset_reasons['state_exit'] += 1
            self.previous_count = self.previous_raw = None
            return
        self.frames += 1
        self.detector_seconds += detector_seconds
        self.pipeline_seconds += pipeline_seconds
        self.raw_counts[raw.count] += 1
        self.tracked_counts[tracked.count] += 1
        diagnostics = tracked.diagnostics
        if diagnostics['reset']:
            self.resets += 1
            self.reset_reasons[diagnostics['reset']] += 1
            self.previous_count = self.previous_raw = None
        self.one_frame += diagnostics['one_frame_candidates_rejected']
        self.held_frames += diagnostics['held_tracks'] > 0
        if self.previous_count is not None:
            self.raw_variation += abs(raw.count - self.previous_raw)
            self.tracked_variation += abs(tracked.count - self.previous_count)
        self.previous_count, self.previous_raw = tracked.count, raw.count
        if diagnostics['large_count_jump']:
            self.jumps.append(dict(frame=index, time=timestamp, jump=diagnostics['large_count_jump']))
        if diagnostics['max_match_residual_deg'] > 6:
            self.discontinuities.append(dict(frame=index, time=timestamp,
                                            residual=diagnostics['max_match_residual_deg']))
        if self.review_dir and index in REVIEW_FRAMES:
            self.review_dir.mkdir(parents=True, exist_ok=True)
            view = frame.copy()
            draw_debug(view,target,raw,tracked)
            draw_knives(view, target, tracked)
            x, y, radius = target
            pad = round(radius * 2.)
            crop = view[max(0, y - pad):y + pad, max(0, x - pad):x + pad]
            tile = cv2.resize(crop, (330, 330), interpolation=cv2.INTER_AREA)
            panel = np.zeros((370, 330, 3), np.uint8)
            panel[:330] = tile
            for row, text in enumerate((f'{index} {timestamp:.2f}s RAW={raw.count} STABLE={tracked.count}',
                                        'A=' + ','.join(f'{a:.0f}' for a in tracked.angles_deg))):
                cv2.putText(panel, text, (4, 347 + row * 18), 0, .40, (255, 255, 255), 1)
            cv2.imwrite(str(self.review_dir / f'knife_{index:06d}.jpg'), panel)
            if 'peak_decisions' in raw.diagnostics:
                cv2.imwrite(str(self.review_dir / f'profile_{index:06d}.png'),profile_chart(raw.diagnostics))
                (self.review_dir / f'diagnostics_{index:06d}.json').write_text(
                    json.dumps(dict(frame=index,timestamp=timestamp,raw=raw.diagnostics,
                                    tracking=tracked.diagnostics,stable_angles=tracked.angles_deg,
                                    probable_angles=[k.angle_deg for k in tracked.probable_knives]),indent=2),encoding='utf-8')
            self.tiles.append(panel)

    def summary(self):
        if self.review_dir and self.tiles:
            for batch in range((len(self.tiles) + 14) // 15):
                sheet = np.zeros((3 * 370, 5 * 330, 3), np.uint8)
                for i, tile in enumerate(self.tiles[batch * 15:batch * 15 + 15]):
                    row, col = divmod(i, 5)
                    sheet[row * 370:(row + 1) * 370, col * 330:(col + 1) * 330] = tile
                cv2.imwrite(str(self.review_dir / f'contact_sheet_{batch}.jpg'), sheet)
        return dict(playing_frames_analyzed=self.frames,
                    raw_count_histogram=dict(sorted(self.raw_counts.items())),
                    stable_count_histogram=dict(sorted(self.tracked_counts.items())),
                    max_knives=max(self.tracked_counts, default=0),
                    count_resets_detected=self.resets,
                    reset_reasons=dict(self.reset_reasons),
                    one_frame_candidates_rejected=self.one_frame,
                    frames_with_held_tracks=self.held_frames,
                    raw_count_total_variation=self.raw_variation,
                    stable_count_total_variation=self.tracked_variation,
                    large_count_jumps=self.jumps,
                    angle_discontinuities=self.discontinuities,
                    detector_avg_ms=self.detector_seconds / self.frames * 1000 if self.frames else 0,
                    playing_pipeline_avg_ms=self.pipeline_seconds / self.frames * 1000 if self.frames else 0,
                    playing_pipeline_fps=self.frames / self.pipeline_seconds if self.pipeline_seconds else 0)
