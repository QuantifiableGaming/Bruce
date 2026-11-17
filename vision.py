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


# Shape and colour tests for one outline blob. Tuned on two real games with yellow outlines
# (Haven and Abyss): they keep about 85% of real enemies and reject about 95% of false blobs
# (fire, lights, tracers, yellow map lines, crates, ability icons).
# hue: the median hue of a real outline. Fire and lamps are more orange; crates are paler.
# max_val: lamps, fire and muzzle flashes are pure white-hot (median brightness 254 or more).
# aspect: height / width. Map lines are wide and flat. Light beams are very tall and thin.
# min_sat: soft coloured smoke and light gradients are less saturated than an outline.
BLOB_RULES = {
    "yellow": {"hue": (27.5, 33.5), "max_val": 254, "aspect": (0.8, 8.0), "min_sat": 158},
    "red": {"hue": None, "max_val": 254, "aspect": (0.8, 8.0)},
    "purple": {"hue": None, "max_val": 254, "aspect": (0.8, 8.0)},
}


class EnemyDetector:
    """Finds the enemy highlight outline near the crosshair and measures head height."""

    def __init__(self, cfg):
        eh = cfg["enemy_highlight"]
        self.ranges = COLOR_RANGES[eh["color"]]
        self.rules = BLOB_RULES[eh["color"]] if eh.get("shape_filter", True) else None
        self.min_pixels = eh["min_pixels"]
        self.min_height = eh["min_height_px"]
        # Very big outlines are enemies at arm's length, or a teammate with yellow clothes:
        # the top of the outline is then not a useful head height.
        self.max_height = eh.get("max_height_px", 180)
        # Only an enemy near the crosshair says something about crosshair height.
        self.max_dx = eh.get("max_center_dx_px", 300)
        self.region = cfg["regions"]["enemy_search"]
        self.ignore = cfg["regions"].get("gun_ignore")
        self.kernel = np.ones((5, 5), np.uint8)

    def mask(self, frame):
        x0, y0, x1, y1 = crop_box(frame.shape, self.region)
        hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        self.last_hsv = hsv
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

    def _looks_like_enemy(self, mask, hsv, bw, bh):
        r = self.rules
        if r is None:
            return True
        aspect = bh / float(max(1, bw))
        if not r["aspect"][0] <= aspect <= r["aspect"][1]:
            return False
        on = mask > 0
        if not on.any():
            return False
        if float(np.median(hsv[..., 2][on])) >= r["max_val"]:
            return False
        if float(np.median(hsv[..., 1][on])) < r.get("min_sat", 0):
            return False
        if r["hue"] is not None and not r["hue"][0] <= float(np.median(hsv[..., 0][on])) <= r["hue"][1]:
            return False
        return True

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
        parts = []   # Outline pieces that pass the colour and shape tests
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            count = int(cv2.countNonZero(m[by:by + bh, bx:bx + bw]))
            if count * scale * scale < self.min_pixels:
                continue
            if not self._looks_like_enemy(m[by:by + bh, bx:bx + bw], self.last_hsv[by:by + bh, bx:bx + bw], bw, bh):
                continue
            parts.append((bx, by, bw, bh))
        if not parts:
            return result
        result["visible"] = True

        # One enemy often breaks into several pieces (legs, arms, head). Join the pieces that
        # sit above or below each other, so the top of the person is found, not the top of an arm.
        people = []
        for bx, by, bw, bh in sorted(parts, key=lambda p: p[1]):
            for person in people:
                if bx < person[2] + 12 and bx + bw > person[0] - 12 and by <= person[3] + 25:
                    person[0], person[1] = min(person[0], bx), min(person[1], by)
                    person[2], person[3] = max(person[2], bx + bw), max(person[3], by + bh)
                    break
            else:
                people.append([bx, by, bx + bw, by + bh])
        best = None
        for px0, py0, px1, py1 in people:
            ph = py1 - py0
            if ph * scale < self.min_height or ph * scale > self.max_height:
                continue
            dist = abs((x0 + (px0 + px1) / 2) - w / 2)
            if dist * scale > self.max_dx:
                continue
            if best is None or dist < best[0]:
                best = (dist, py0, ph)
        if best is None:
            return result

        _, by, bh = best
        head_y = y0 + by + 0.08 * bh  # The head center sits a little below the top of the outline.
        result["head_offset"] = (h / 2 - head_y) * scale
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

    def _small(self, frame):
        h = int(round(self.width * frame.shape[0] / frame.shape[1]))
        # Shrink first, then make it grey: much less work than grey first on the full frame.
        small = cv2.resize(frame, (self.width, h), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small

    # --- On demand (live coach): keep small frames, measure only around a shot. ---

    def push(self, t, frame, keep_s=2.0):
        """Keep a small grey copy of the frame. Cheap: no optical flow here."""
        if not hasattr(self, "recent"):
            from collections import deque

            self.recent = deque()
            self.cache = {}
        self.recent.append((t, self._small(frame)))
        while self.recent and t - self.recent[0][0] > keep_s:
            old_t, _ = self.recent.popleft()
            self.cache.pop(old_t, None)

    def between(self, start, end):
        """Motion scores for the kept frames in [start, end]. Optical flow runs only here."""
        frames = list(getattr(self, "recent", ()))
        out = []
        for (t0, a), (t1, b) in zip(frames, frames[1:]):
            if t1 < start or t1 > end:
                continue
            if t1 not in self.cache:
                self.cache[t1] = self._pair(a, b)
            if self.cache[t1] is not None:
                out.append(self.cache[t1])
        return out

    # --- Every frame (replays, tests). ---

    def update(self, frame):
        small = self._small(frame)
        if self.prev is None or self.prev.shape != small.shape:
            self.prev = small
            self._setup(small.shape)
            return 0.0
        prev, self.prev = self.prev, small
        return self._pair(prev, small)

    def _pair(self, prev, small):
        """The motion score from one frame to the next. None during a flick."""
        if not hasattr(self, "pinv") or self.n == 0 or self.window.shape != small.shape:
            self._setup(small.shape)
        # Phase correlation finds large whole-screen shifts (flicks) that optical flow misses.
        (dx, dy), _ = cv2.phaseCorrelate(np.float32(prev), np.float32(small), self.window)
        if math.hypot(dx, dy) > self.max_turn_px:
            return None  # Unknown: you were flicking, so the program does not judge this frame.
        flow = cv2.calcOpticalFlowFarneback(prev, small, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        obs = np.concatenate([flow[..., 0][self.index], flow[..., 1][self.index]])
        coef = self.pinv @ obs
        residual = obs - self.design @ coef
        # Camera turn speed in pixels per frame at the screen center.
        turn = float(np.hypot(*coef) * self.f)
        if turn > self.max_turn_px:
            return None
        return float(np.median(np.hypot(residual[: self.n], residual[self.n:])))
