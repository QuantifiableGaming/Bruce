"""Fast digit reader for the Valorant HUD.

The reader compares the picture with ten stored digit shapes. The shapes come from
real Valorant HUD pictures at 1080p. One read takes about one millisecond.
The reader does not start a second program, so it is much faster than Tesseract.
"""
import os

import cv2
import numpy as np

# The game uses a bold font for ammo and health, and a thin font for the score.
# Each style has a key in the shape file and the digit height of its stored shapes.
STYLES = {"bold": ("d", 34), "thin": ("t", 25)}
HUD_DIGIT_FRACTION = 34 / 1080    # Height of an ammo or health digit, as a part of the screen height.
SCORE_DIGIT_FRACTION = 20 / 1080  # Height of a score digit.

MIN_MATCH = 0.62         # Lowest match quality for one digit. 1.0 is a perfect match.
WEAK_MATCH = 0.45        # A possible digit that is not clear. It makes the full read unsafe.
MIN_CONTRAST = 35
SPECTATE_MATCH = 0.6     # In tests: 0.25 or less when alive, 0.84 or more when you watch a teammate.        # Lowest brightness step between a digit and its background.


class DigitReader:
    def __init__(self, path=None):
        path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "digits.npz")
        data = np.load(path)
        # The "SWITCH PLAYER" text at the bottom left. The game shows it only when you are dead
        # and watch a teammate. At 1080p it is about 20 px high.
        self.spec = data["spec"].astype(np.float32) if "spec" in data else None
        # Valorant's "Shooting Error" stat (Settings > Video > Stats). Small digits at the top left.
        self.se_label = data["se_label"] if "se_label" in data else None
        self.se_digits = {d: data["se" + d] for d in "0123456789"} if self.se_label is not None else {}
        self.se_where = None   # (x, y) of the "Error:" label at 1080p, found on the first good read.
        self.shapes = {}
        for style, (key, _) in STYLES.items():
            self.shapes[style] = {}
            for d in "0123456789":
                t = data[key + d].astype(np.float32)
                self.shapes[style][d] = (t, t > 128, t < 60)

    def read(self, img, digit_height, max_digits=3, style="bold"):
        """Read a number from a BGR picture. Return (number, quality) or (None, 0.0).

        digit_height is the expected height of one digit in this picture, in pixels.
        """
        if img is None or img.size == 0 or digit_height < 6:
            return None, 0.0
        scale = STYLES[style][1] / float(digit_height)
        if abs(scale - 1.0) > 0.03:
            method = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=method)
        # White text is bright in all three colours. Red text is bright only in one colour.
        best = (None, 0.0)
        for plane in (img.min(axis=2), img.max(axis=2)):
            result = self._read_plane(plane, max_digits, self.shapes[style])
            if result[0] is not None and result[1] > best[1]:
                best = result
            if best[1] >= 0.8:
                break
        return best

    def _read_plane(self, plane, max_digits, shapes):
        plane = cv2.copyMakeBorder(plane, 3, 3, 3, 3, cv2.BORDER_REPLICATE)
        fplane = plane.astype(np.float32)
        found = []   # (quality, x, y, width, digit)
        weak = []
        for d, (shape, on, off) in shapes.items():
            th, tw = shape.shape
            if plane.shape[0] < th or plane.shape[1] < tw:
                continue
            res = cv2.matchTemplate(fplane, shape, cv2.TM_CCOEFF_NORMED)
            for _ in range(max_digits):
                _, q, _, (x, y) = cv2.minMaxLoc(res)
                if q < WEAK_MATCH:
                    break
                patch = plane[y:y + th, x:x + tw]
                if float(patch[on].mean()) - float(patch[off].mean()) >= MIN_CONTRAST:
                    (found if q >= MIN_MATCH else weak).append((q, x, y, tw, d))
                res[:, max(0, x - tw // 2):x + tw // 2 + 1] = -1
        if not found:
            return None, 0.0
        found.sort(reverse=True)
        top_y = found[0][2]
        chosen = []
        for q, x, y, w, d in found:
            if abs(y - top_y) > 3:
                continue
            if any(min(x + w, cx + cw) - max(x, cx) > 0.3 * min(w, cw) for _, cx, _, cw, _ in chosen):
                continue
            chosen.append((q, x, y, w, d))
        chosen.sort(key=lambda c: c[1])
        if len(chosen) > max_digits:
            return None, 0.0
        # The digits of one number are next to each other.
        for a, b in zip(chosen, chosen[1:]):
            if b[1] - (a[1] + a[3]) > 10:
                return None, 0.0
        # An unclear digit next to the clear digits means that the number is not complete.
        # No read is better than a wrong read.
        for q, x, y, w, d in weak:
            if abs(y - top_y) > 3:
                continue
            if any(min(x + w, cx + cw) - max(x, cx) > 0.3 * min(w, cw) for _, cx, _, cw, _ in chosen):
                continue
            if any(-4 <= x - (cx + cw) <= 10 or -4 <= cx - (x + w) <= 10 for _, cx, _, cw, _ in chosen):
                return None, 0.0
        text = "".join(c[4] for c in chosen)
        if len(text) > 1 and text[0] == "0":
            return None, 0.0
        return int(text), min(c[0] for c in chosen)


    def spectating(self, frame):
        """Return True when the "SWITCH PLAYER" text shows (you are dead), False if not, None if unknown."""
        if self.spec is None or frame is None:
            return None
        h = frame.shape[0]
        k = h / 1080.0
        area = frame[int(835 * k):int(895 * k), int(80 * k):int(330 * k)]
        if area.size == 0:
            return None
        if abs(k - 1.0) > 0.03:
            area = cv2.resize(area, None, fx=1 / k, fy=1 / k, interpolation=cv2.INTER_AREA if k > 1 else cv2.INTER_CUBIC)
        plane = area.min(axis=2).astype(np.float32)
        if plane.shape[0] < self.spec.shape[0] or plane.shape[1] < self.spec.shape[1]:
            return None
        score = cv2.minMaxLoc(cv2.matchTemplate(plane, self.spec, cv2.TM_CCOEFF_NORMED))[1]
        return score >= SPECTATE_MATCH


    def shooting_error(self, frame):
        """Read Valorant's "Shooting Error" stat in degrees. None when the stat is not on the screen.

        The stat shows the spread of your last shot for about 0.1 s, then 0.00.
        A first shot above about 1 degree means you were moving (or jumping) when you fired.
        """
        if self.se_label is None or frame is None:
            return None
        k = frame.shape[0] / 1080.0
        strip = frame[0:int(round(26 * k)), 0:int(round(900 * k))]
        if strip.size == 0:
            return None
        if abs(k - 1.0) > 0.03:
            strip = cv2.resize(strip, None, fx=1 / k, fy=1 / k, interpolation=cv2.INTER_AREA if k > 1 else cv2.INTER_CUBIC)
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).astype(np.float32)
        th, tw = self.se_label.shape
        if self.se_where is not None:
            x, y = self.se_where
            patch = gray[y:y + th, x:x + tw]
            ok = patch.shape == self.se_label.shape and cv2.matchTemplate(patch, self.se_label, cv2.TM_CCOEFF_NORMED)[0, 0] > 0.75
            if not ok:
                self.se_where = None
        if self.se_where is None:
            if gray.shape[0] < th or gray.shape[1] < tw:
                return None
            _, q, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, self.se_label, cv2.TM_CCOEFF_NORMED))
            if q < 0.8:
                return None
            self.se_where = loc
        x, y = self.se_where
        text = ""
        for dx in (39, 49, 56):        # The three digits of "D.DD", to the right of "Error:"
            cell = gray[y:y + 14, x + dx:x + dx + 9]
            if cell.shape != (14, 9):
                return None
            cell = (cell - cell.mean()) / (cell.std() + 1e-3)
            best = max(((float((np.roll(cell, s, axis=1) * t).mean()), d)
                        for d, t in self.se_digits.items() for s in (-1, 0, 1)))
            if best[0] < 0.5:
                return None
            text += best[1]
        return int(text) / 100.0
