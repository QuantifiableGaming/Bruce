"""Records one match for troubleshooting: every text reading, with pictures of what was read.

Output folder: recordings/match_<date>_<time>/
  readings.csv   one line per reading: time, ammo, health, score, banner text, menu text
  banner/        the top of the screen (score and banner) at every score read
  hud/           the Ammo and Health boxes at every score read
  full/          the full screen every 2 seconds
  summary.txt    written at the end: how often each reading worked, and every banner text seen
"""
import csv
import os
import threading
import time
from collections import Counter
from datetime import datetime

import cv2
import numpy as np

from .util import crop


class MatchRecorder:
    def __init__(self, cfg, root="recordings", full_every_s=2.0, max_minutes=45):
        self.regions = cfg["regions"]
        self.folder = os.path.join(root, datetime.now().strftime("match_%Y%m%d_%H%M%S"))
        for sub in ("banner", "hud", "full"):
            os.makedirs(os.path.join(self.folder, sub), exist_ok=True)
        self.file = open(os.path.join(self.folder, "readings.csv"), "w", newline="", encoding="utf-8")
        self.csv = csv.writer(self.file)
        self.csv.writerow(["clock", "seconds", "kind", "ammo", "health", "score", "banner_text", "menu_text",
                           "agent_text", "pictures", "spectating", "shooting_error"])
        self.full_every = full_every_s
        self.max_s = max_minutes * 60
        self.start = time.monotonic()
        self.last_full = -1e9
        self.lock = threading.Lock()
        self.n = self.hud_reads = self.ammo_ok = self.health_ok = 0
        self.scene_reads = self.score_ok = self.pictures = 0
        self.texts = Counter()
        self.scores = []
        self.active = True
        print(f"[record] Recording this match to {self.folder}")

    def _jpg(self, sub, name, img, quality=85):
        if img is None or img.size == 0:
            return
        cv2.imwrite(os.path.join(self.folder, sub, name + ".jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        self.pictures += 1

    def _begin(self):
        """Return (elapsed, picture name), or None when the recording is over."""
        if not self.active:
            return None
        elapsed = time.monotonic() - self.start
        if elapsed > self.max_s:
            print("[record] Time limit reached. Recording stopped.")
            self._close()
            return None
        self.n += 1
        return elapsed, f"{self.n:06d}_{elapsed:07.2f}s".replace(".", "_")

    def _row(self, elapsed, kind, ammo="", health="", score="", banner="", menu="", agent="", saved="", spectating="", shot_error=None):
        self.csv.writerow([datetime.now().strftime("%H:%M:%S.%f")[:-3], f"{elapsed:.2f}", kind,
                           "" if ammo is None else ammo, "" if health is None else health,
                           score, banner or "", menu or "", agent or "", saved,
                           "" if spectating is None else int(bool(spectating)),
                           "" if shot_error is None else shot_error])

    def save_hud(self, frame, ammo, health, spectating=None, shot_error=None):
        """Call after every ammo and health reading."""
        with self.lock:
            begin = self._begin()
            if not begin:
                return
            self.hud_reads += 1
            self.ammo_ok += ammo is not None
            self.health_ok += health is not None
            self._row(begin[0], "hud", ammo, health, spectating=spectating, shot_error=shot_error)

    def save_scene(self, frame, values):
        """Call after every score and banner reading. values has score, banner, screen and agent."""
        with self.lock:
            begin = self._begin()
            if not begin:
                return
            elapsed, name = begin
            self.scene_reads += 1
            score = values.get("score")
            if score:
                self.score_ok += 1
                if not self.scores or self.scores[-1][1] != score:
                    self.scores.append((elapsed, score))
            for key in ("banner", "screen", "agent"):
                if values.get(key):
                    self.texts[(key, values[key])] += 1
            h = frame.shape[0]
            self._jpg("banner", name, frame[: int(h * 0.30), int(frame.shape[1] * 0.25): int(frame.shape[1] * 0.75)])
            a, hp = crop(frame, self.regions["ammo"]), crop(frame, self.regions["health"])
            if a.size and hp.size:
                height = max(a.shape[0], hp.shape[0])
                pad = lambda im: cv2.copyMakeBorder(im, 0, height - im.shape[0], 0, 8, cv2.BORDER_CONSTANT)  # noqa: E731
                self._jpg("hud", name, np.hstack([pad(hp), pad(a)]), 95)
            if elapsed - self.last_full >= self.full_every:
                self.last_full = elapsed
                self._jpg("full", name, frame, 80)
            self._row(elapsed, "scene", score="" if not score else f"{score[0]}-{score[1]}",
                      banner=values.get("banner"), menu=values.get("screen"), agent=values.get("agent"), saved=name)

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if not self.active:
            return
        self.active = False
        self.file.close()
        elapsed = time.monotonic() - self.start
        pct = lambda n, d: f"{100 * n / d:.0f}%" if d else "n/a"  # noqa: E731
        rate = lambda n: f"{n / elapsed:.1f} per second" if elapsed > 0 else "n/a"  # noqa: E731
        lines = [
            "Valorant Coach - match recording summary",
            f"Length: {elapsed / 60:.1f} minutes",
            f"Ammo and health readings: {self.hud_reads} ({rate(self.hud_reads)})",
            f"Ammo read: {pct(self.ammo_ok, self.hud_reads)}   Health read: {pct(self.health_ok, self.hud_reads)}",
            f"Score and banner readings: {self.scene_reads} ({rate(self.scene_reads)})",
            f"Score read: {pct(self.score_ok, self.scene_reads)}",
            f"Pictures saved: {self.pictures}",
            "",
            "Score changes (seconds, yours-enemy):",
        ]
        lines += [f"  {t:7.1f}  {s[0]}-{s[1]}" for t, s in self.scores[:80]] or ["  none"]
        lines += ["", "Every text seen (number of reads, area, text):"]
        lines += [f"  {n:4}  {key:6}  {text}" for (key, text), n in self.texts.most_common(200)] or ["  none"]
        with open(os.path.join(self.folder, "summary.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"[record] Recording saved: {self.folder} ({self.pictures} pictures). Read summary.txt first.")
