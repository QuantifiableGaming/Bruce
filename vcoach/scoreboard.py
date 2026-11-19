"""Read your own line from the end-of-match scoreboard (the SUMMARY tab after a match).

Valorant marks your own line with a yellow stripe at the left edge. Teammates have a green
stripe and enemies a red one. The coach finds the yellow line and reads its numbers:
performance (combat) score, kills / deaths / assists, trades, first bloods, plants and defuses.
"""
import re

import cv2
import numpy as np

STRIPE_X = (0.176, 0.190)     # Where the coloured stripes are, as parts of the screen width (16:9).
TABLE_Y = (0.25, 0.92)
NUMBERS_X = (0.395, 0.94)     # From the performance score column to the right end (leaves out the name).


def _runs(mask, min_len):
    """Start and end of each run of True values that is at least min_len long."""
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def _looks_like_table(runs, h):
    """True when the coloured lines look like the scoreboard: at least 8 lines of the same
    height, one under the other at the same distance. In-game pictures (kill banners, the
    combat report, map art) give a few lines of mixed size."""
    if len(runs) < 8:
        return False
    heights = np.array([b - a for a, b in runs], float)
    if heights.min() < 0.03 * h or heights.max() > 0.055 * h:
        return False
    starts = np.array([a for a, _ in runs], float)
    steps = np.diff(starts)
    return steps.min() > 0.8 * np.median(steps) and steps.max() < 1.2 * np.median(steps)


def find_my_row(frame):
    """Return (top, bottom) in frame pixels of your own scoreboard line, or None."""
    h, w = frame.shape[:2]
    y0, y1 = int(TABLE_Y[0] * h), int(TABLE_Y[1] * h)
    band = frame[y0:y1, int(STRIPE_X[0] * w):int(STRIPE_X[1] * w)]
    if band.size == 0:
        return None
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0].astype(int), hsv[..., 1], hsv[..., 2]
    strong = (sat > 55) & (val > 110)
    # The stripe is a thin line (2 px at 1080p). Use the one column that is coloured most often.
    col = int(np.argmax(strong.sum(axis=0)))
    cols = slice(max(0, col - 1), col + 2)
    hue, strong = hue[:, cols], strong[:, cols]
    yellow = (strong & (hue >= 12) & (hue <= 35)).any(axis=1)
    green = (strong & (hue >= 70) & (hue <= 100)).any(axis=1)
    red = (strong & ((hue <= 8) | (hue >= 165))).any(axis=1)
    min_len = max(8, int(0.022 * h))          # a line is about 46 px high at 1080p
    mine = _runs(yellow, min_len)
    others = _runs(green, min_len) + _runs(red, min_len)
    # A real scoreboard has your line and several other lines (ignore one-off yellow things).
    if len(mine) != 1 or len(others) < 3:
        return None
    if not _looks_like_table(sorted(mine + others), h):
        return None
    a, b = mine[0]
    return y0 + a, y0 + b


def parse(text):
    """Numbers from the right part of your line: '178 9 / 18 / 6 3 1 0 3'."""
    text = (text or "").replace("|", "/").replace("\\", "/")
    m = re.search(r"(\d{1,3})\s*/\s*(\d{1,3})\s*/\s*(\d{1,3})", text)
    if not m:
        return None
    before = re.findall(r"\d+", text[:m.start()])
    after = re.findall(r"\d+", text[m.end():])
    if not before:
        return None
    out = {"score": int(before[-1]), "kills": int(m.group(1)), "deaths": int(m.group(2)),
           "assists": int(m.group(3))}
    for key, value in zip(("trades", "first_bloods", "plants", "defuses"), after):
        out[key] = int(value)
    if not 0 <= out["score"] <= 999 or max(out["kills"], out["deaths"], out["assists"]) > 99:
        return None
    return out


def my_row_image(frame, row=None):
    """The binary image of the numbers on your line (dark text on white), or None."""
    row = row or find_my_row(frame)
    if row is None:
        return None
    h, w = frame.shape[:2]
    top, bottom = row
    img = frame[top:bottom, int(NUMBERS_X[0] * w):int(NUMBERS_X[1] * w)]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    scale = 2.0 * 1080.0 / h
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)   # dark text on white
    return cv2.copyMakeBorder(binary, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)


def read_my_row(frame, read_text):
    """read_text(gray_image) -> str. Returns a dict of your numbers, or None."""
    row = find_my_row(frame)
    if row is None:
        return None
    h, w = frame.shape[:2]
    top, bottom = row
    img = frame[top:bottom, int(NUMBERS_X[0] * w):int(NUMBERS_X[1] * w)]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    scale = 2.0 * 1080.0 / h
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)   # dark text on white
    binary = cv2.copyMakeBorder(binary, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    result = parse(read_text(binary))
    if result is not None:
        result["row"] = [int(top), int(bottom)]
    return result
