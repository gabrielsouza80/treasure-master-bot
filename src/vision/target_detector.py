"""Circle candidates only; a detection is never proof of gameplay."""

import cv2


def detect_target(frame):
    """Return (x, y, radius) in input pixels, or None.

    Keep the existing Hough detector at a fixed working width so its cost and
    thresholds are independent of capture resolution.
    """
    h, w = frame.shape[:2]
    scale = 540 / w
    small = cv2.resize(frame, (540, round(h * scale)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    x1, x2 = int(sw * .08), int(sw * .92)
    y1, y2 = int(sh * .12), int(sh * .56)
    gray = cv2.cvtColor(small[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (9, 9), 2)
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=100,
        # Exclude the boss's inner ring, which otherwise displaces its body.
        param1=120, param2=55, minRadius=110, maxRadius=165,
    )
    if circles is None:
        return None
    candidates = []
    for cx, cy, radius in circles[0]:
        x, y = x1 + cx, y1 + cy
        if .22 * sw < x < .78 * sw and .18 * sh < y < .52 * sh:
            score = (abs(x - sw / 2) / (sw / 2)
                     + abs(y - sh * .37) / (sh * .30)
                     + abs(radius - 130) / 130)
            candidates.append((score, (round(x / scale), round(y / scale),
                                       round(radius / scale))))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None
