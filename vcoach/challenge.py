"""The Range challenge: 20 stops, each followed by a shot, scored.

Say "Bruce, start the challenge" in the Range. Strafe, stop, and shoot 20 times. The coach
counts each first shot after a strafe:
  clean   you stopped with a counter-strafe tap (keys), or the Shooting Error stat was low
  slow    you let go of the key and waited
  moving  you were still moving at the shot
Shots fired while you stood still do not count, because the challenge is about stopping.

At the end the coach says your score and saves a result card (a 1080 x 1920 picture for a
Short) in reports/challenges/, and a line in reports/challenges.jsonl. The coach compares
the score with your best and your last challenge, so you can post a "before and after".
"""
import json
import os
from datetime import datetime

import cv2
import numpy as np


class Challenge:
    def __init__(self, target=20, threshold_deg=1.0):
        self.target = target
        self.threshold = threshold_deg
        self.shots = []          # {"kind": "clean"|"slow"|"moving", "gap_ms": int|None, "stat": float|None}
        self.started = datetime.now()

    @property
    def done(self):
        return len(self.shots) >= self.target

    def add(self, keys, stat, keys_on):
        """One first shot. Returns True when it counted."""
        if self.done:
            return False
        stat = stat if isinstance(stat, (int, float)) and not isinstance(stat, bool) else None
        if keys_on:
            if keys is None:
                return False                      # You stood still: not a stop.
            if keys["moving"]:
                kind = "moving"
            elif keys["counter"]:
                kind = "clean"
            else:
                kind = "slow"
            gap = keys.get("gap_ms")
        else:
            if stat is None:
                return False                      # Nothing to judge the shot with.
            kind = "clean" if stat <= self.threshold else "moving"
            gap = None
        self.shots.append({"kind": kind, "gap_ms": gap, "stat": stat})
        return True

    def result(self):
        n = len(self.shots)
        clean = sum(s["kind"] == "clean" for s in self.shots)
        gaps = [s["gap_ms"] for s in self.shots if s["kind"] == "clean" and s["gap_ms"] is not None]
        stats = [s["stat"] for s in self.shots if s["stat"] is not None]
        return {
            "date": self.started.strftime("%Y-%m-%d %H:%M"),
            "shots": n, "clean": clean, "slow": sum(s["kind"] == "slow" for s in self.shots),
            "moving": sum(s["kind"] == "moving" for s in self.shots),
            "score": round(100 * clean / n) if n else 0,
            "stop_ms": round(sorted(gaps)[len(gaps) // 2]) if gaps else None,
            "accurate": round(100 * sum(x <= self.threshold for x in stats) / len(stats)) if stats else None,
        }


def load_results(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def save_result(res, folder):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "challenges.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")


def summary_line(res, earlier):
    """The spoken result, with the best and the last earlier score."""
    text = f"Challenge done. {res['clean']} of {res['shots']} clean stops. That is {res['score']} percent."
    if res.get("stop_ms") is not None:
        text += f" Your median stop was {res['stop_ms']} milliseconds before the shot."
    if earlier:
        best = max(e["score"] for e in earlier)
        last = earlier[-1]["score"]
        if res["score"] > best:
            text += f" New best. Your old best was {best} percent."
        elif res["score"] > last:
            text += f" Up from {last} percent last time."
        elif res["score"] < last:
            text += f" Down from {last} percent last time. Your best is {best}."
        else:
            text += f" The same as last time. Your best is {best}."
    return text


def make_card(res, path, name="", earlier=None):
    """A 1080 x 1920 result card for a Short. Plain shapes and text only."""
    w, h = 1080, 1920
    img = np.full((h, w, 3), (24, 20, 18), np.uint8)
    red = (85, 70, 255)                      # Valorant-style red, in BGR
    white, grey = (240, 240, 240), (150, 150, 150)
    font, bold = cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX

    def center(text, y, scale, color, thick=2, f=font):
        (tw, _), _ = cv2.getTextSize(text, f, scale, thick)
        cv2.putText(img, text, ((w - tw) // 2, y), f, scale, color, thick, cv2.LINE_AA)

    cv2.rectangle(img, (0, 0), (w, 28), red, -1)
    center("COUNTER-STRAFE", 250, 2.4, white, 5, bold)
    center("CHALLENGE", 360, 2.4, red, 5, bold)
    center(f"{res['score']}%", 860, 9.0, white, 20, bold)
    center(f"{res['clean']} of {res['shots']} clean stops", 1010, 1.8, white, 3)
    rows = []
    if res.get("stop_ms") is not None:
        rows.append(f"Median stop: {res['stop_ms']} ms before the shot")
    if res.get("accurate") is not None:
        rows.append(f"Accurate first shots: {res['accurate']}%")
    rows.append(f"Slow stops: {res['slow']}    Moving: {res['moving']}")
    if earlier:
        rows.append(f"Best before: {max(e['score'] for e in earlier)}%    Last: {earlier[-1]['score']}%")
    for i, row in enumerate(rows):
        center(row, 1160 + i * 90, 1.25, grey, 2)
    center(res["date"], 1700, 1.2, grey, 2)
    if name:
        center(name, 1790, 1.6, red, 3, bold)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cv2.imwrite(path, img)
    return path
