"""Thin vertical edge structure in the clear launch lane, no knife template."""
import cv2
import numpy as np


def projectile_tip(frame,target):
    if target is None: return None
    cx,cy,r=target
    h,w=frame.shape[:2]
    x0,x1=max(0,round(cx-.035*w)),min(w,round(cx+.035*w))
    y0,y1=max(0,round(cy+1.90*r)),min(h,round(.84*h))
    if y1-y0<.2*r or x1<=x0:return None
    gray=cv2.cvtColor(frame[y0:y1,x0:x1],cv2.COLOR_BGR2GRAY)
    edges=(np.abs(cv2.Sobel(gray,cv2.CV_32F,1,0))>80).astype(np.uint8)
    edges=cv2.morphologyEx(edges,cv2.MORPH_CLOSE,np.ones((5,3),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(edges)
    tips=[y0+y for x,y,bw,bh,area in stats[1:]
          if .14*r<=bh<=1.2*r and bw<=.30*r and bh>=2*bw and area>=30 and y>1]
    return float(min(tips)) if tips else None
