import math

import cv2
import numpy as np

from .util import crop_box

# HSV ranges for the enemy highlight colors in Valorant settings.
COLOR_RANGES = {
    "red": [((0, 150, 150), (8, 255, 255)), ((172, 150, 150), (180, 255, 255))],
    "yellow": [((24, 150, 150), (36, 255, 255))],
    "purple": [((138, 110, 120), (162, 255, 255))],
}


class EnemyDetector:
    """Finds the enemy highlight outline near the crosshair and measures head height."""

    def __init__(self, cfg):
        eh = cfg["enemy_highlight"]
        self.ranges = COLOR_RANGES[eh["color"]]
        self.min_pixels = eh["min_pixels"]
        self.min_height = eh["min_height_px"]
        self.region = cfg["regions"]["enemy_search"]
        self.ignore = cfg["regions"].get("gun_ignore")
        self.kernel = np.ones((5, 5), np.uint8)

    def mask(self, frame):
        x0, y0, x1, y1 = crop_box(frame.shape, self.region)
        hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        m = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in self.ranges:
            m |= cv2.inRange(hsv, np.array(lo), np.array(hi))
        if self.ignore:
            # Clear the part of the search area that covers your own gun.
            gx0, gy0, gx1, gy1 = crop_box(frame.shape, self.ignore)
            ax0, ay0 = max(gx0, x0) - x0, max(gy0, y0) - y0
            ax1, ay1 = min(gx1, x1) - x0, min(gy1, y1) - y0
            if ax1 > ax0 and ay1 > ay0:
                self.last_ignored = int(cv2.countNonZero(m[ay0:ay1, ax0:ax1]))
                m[ay0:ay1, ax0:ax1] = 0
                return m, (x0, y0)
        self.last_ignored = 0
        return m, (x0, y0)

    def detect(self, frame):
        """Return visibility and head offset in 1080p pixels.

        A positive head_offset means the head is above the crosshair (aim too low).
        Pixels inside the "gun_ignore" region are ignored: that is your own gun and arms.
        """
        h, w = frame.shape[:2]
        scale = 1080.0 / h
        m, (x0, y0) = self.mask(frame)
        pixels = int(cv2.countNonZero(m))
        result = {"pixels": pixels, "visible": False, "head_offset": None, "ignored": self.last_ignored}
        if pixels * scale * scale < self.min_pixels:
            return result

        blobs = cv2.dilate(m, self.kernel, iterations=2)
        contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            count = int(cv2.countNonZero(m[by:by + bh, bx:bx + bw]))
            if count * scale * scale < self.min_pixels:
                continue
            result["visible"] = True
            if bh * scale < self.min_height:
                continue
            dist = abs((x0 + bx + bw / 2) - w / 2)
            if best is None or dist < best[0]:
                best = (dist, by, bh)
        if best is None:
            return result

        _, by, bh = best
        head_y = y0 + by + 0.08 * bh  # The head center sits a little below the top of the outline.
        result["head_offset"] = (h / 2 - head_y) * scale
        return result


class MotionMeter:
    """Estimates how much YOU move (strafe), not how much you turn the camera.

    Camera rotation moves the whole picture in a fixed pattern. The meter fits that
    rotation pattern to the optical flow and removes it. Movement through the world
    causes parallax (near objects move more than far ones), which the rotation
    pattern cannot explain. The score is the median flow that is left over.
    """

    def __init__(self, width=320, hfov_deg=103.0, max_turn_px=4.0):
        self.width = width
        self.max_turn_px = max_turn_px  # Faster turns (flicks) make the flow unreliable.
        self.hfov = math.radians(hfov_deg)
        self.prev = None

    def _setup(self, shape):
        h, w = shape
        f = (w / 2) / math.tan(self.hfov / 2)
        self.f = f
        self.window = cv2.createHanningWindow((w, h), cv2.CV_32F)
        ys, xs = np.mgrid[0:h:4, 0:w:4]
        x = xs - w / 2.0
        y = ys - h / 2.0
        # Ignore the HUD and gun at the bottom, and the crosshair area.
        keep = (ys < h * 0.65) & ~((np.abs(x) < w * 0.08) & (np.abs(y) < h * 0.08))
        self.index = (ys[keep], xs[keep])
        xk, yk = x[keep], y[keep]
        yaw = np.concatenate([f + xk ** 2 / f, xk * yk / f])
        pitch = np.concatenate([xk * yk / f, f + yk ** 2 / f])
        self.design = np.stack([yaw, pitch], axis=1)
        self.pinv = np.linalg.pinv(self.design)
        self.n = len(xk)

    def update(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h = int(round(self.width * frame.shape[0] / frame.shape[1]))
        small = cv2.resize(gray, (self.width, h), interpolation=cv2.INTER_AREA)
        if self.prev is None or self.prev.shape != small.shape:
            self.prev = small
            self._setup(small.shape)
            return 0.0
        # Phase correlation finds large whole-screen shifts (flicks) that optical flow misses.
        (dx, dy), _ = cv2.phaseCorrelate(np.float32(self.prev), np.float32(small), self.window)
        if math.hypot(dx, dy) > self.max_turn_px:
            self.prev = small
            return None  # Unknown: you were flicking, so the program does not judge this frame.
        flow = cv2.calcOpticalFlowFarneback(self.prev, small, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        self.prev = small
        obs = np.concatenate([flow[..., 0][self.index], flow[..., 1][self.index]])
        coef = self.pinv @ obs
        residual = obs - self.design @ coef
        # Camera turn speed in pixels per frame at the screen center.
        turn = float(np.hypot(*coef) * self.f)
        if turn > self.max_turn_px:
            return None
        return float(np.median(np.hypot(residual[: self.n], residual[self.n:])))
