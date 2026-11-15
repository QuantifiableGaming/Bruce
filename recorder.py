"""Records one match for troubleshooting: every text reading, with pictures of what was read.

Output folder: recordings/match_<date>_<time>/
  readings.csv   one line per reading: time, ammo, health, banner text
  banner/        the Round banner box at every banner read (about 2 per second)
  hud/           the Ammo and Health boxes at every banner read
  full/          the full screen every 2 seconds
  summary.txt    written at the end: how often each reading worked, and every banner text seen
"""
import csv
import os
import time
from collections import Counter
from datetime import datetime

import cv2
import numpy as np

from .util import crop


class MatchRecorder:
    def __init__(self, cfg, root="recordings", full_every_s=2.0, max_minutes=45):
        self.regions = cfg["regions"]
        self.keywords = cfg.get("banner_keywords", {})
        self.folder = os.path.join(root, datetime.now().strftime("match_%Y%m%d_%H%M%S"))
        for sub in ("banner", "hud", "full"):
            os.makedirs(os.path.join(self.folder, sub), exist_ok=True)
        self.file = open(os.path.join(self.folder, "readings.csv"), "w", newline="", encoding="utf-8")
        self.csv = csv.writer(self.file)
        self.csv.writerow(["clock", "seconds", "ammo", "health", "banner_read", "banner_text", "pictures"])
        self.full_every = full_every_s
        self.max_s = max_minutes * 60
        self.start = time.monotonic()
        self.last_full = -1e9
        self.reads = self.ammo_ok = self.health_ok = self.banner_reads = self.pictures = 0
        self.texts = Counter()
        self.active = True
        print(f"[record] Recording this match to {self.folder}")

    def _jpg(self, sub, name, img, quality=85):
        if img is None or img.size == 0:
            return
        cv2.imwrite(os.path.join(self.folder, sub, name + ".jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        self.pictures += 1

    def save(self, frame, ammo, health, banner_read, banner_text):
        """Call after every HUD reading. banner_read says if the banner was read this time."""
        if not self.active:
            return
        elapsed = time.monotonic() - self.start
        if elapsed > self.max_s:
            print("[record] Time limit reached. Recording stopped.")
            self.close()
            return
        name = f"{self.reads:06d}_{elapsed:07.2f}s".replace(".", "_")
        saved = ""
        self.reads += 1
        self.ammo_ok += ammo is not None
        self.health_ok += health is not None
        if banner_read:
            self.banner_reads += 1
            if banner_text:
                self.texts[banner_text] += 1
            self._jpg("banner", name, crop(frame, self.regions["banner"]))
            a, h = crop(frame, self.regions["ammo"]), crop(frame, self.regions["health"])
            if a.size and h.size:
                height = max(a.shape[0], h.shape[0])
                pad = lambda im: cv2.copyMakeBorder(im, 0, height - im.shape[0], 0, 8, cv2.BORDER_CONSTANT)  # noqa: E731
                self._jpg("hud", name, np.hstack([pad(h), pad(a)]), 95)
            saved = name
        if elapsed - self.last_full >= self.full_every:
            self.last_full = elapsed
            self._jpg("full", name, frame, 80)
            saved = name
        self.csv.writerow([datetime.now().strftime("%H:%M:%S.%f")[:-3], f"{elapsed:.2f}",
                           "" if ammo is None else ammo, "" if health is None else health,
                           int(bool(banner_read)), banner_text or "", saved])

    def close(self):
        if not self.active:
            return
        self.active = False
        self.file.close()
        elapsed = time.monotonic() - self.start
        pct = lambda n: f"{100 * n / self.reads:.0f}%" if self.reads else "n/a"  # noqa: E731
        words = [w for group in self.keywords.values() for w in group]
        hits = Counter()
        for text, count in self.texts.items():
            for w in words:
                if f" {w} " in f" {text} ":
                    hits[w] += count
        lines = [
            "Valorant Coach - match recording summary",
            f"Length: {elapsed / 60:.1f} minutes",
            f"Readings: {self.reads}",
            f"Ammo read: {pct(self.ammo_ok)} of readings",
            f"Health read: {pct(self.health_ok)} of readings",
            f"Banner reads: {self.banner_reads}, with text: {sum(self.texts.values())}",
            f"Pictures saved: {self.pictures}",
            "",
            "Result words found in the banner box (word: number of reads):",
        ]
        lines += [f"  {w}: {n}" for w, n in hits.most_common()] or ["  none"]
        lines += ["", "Every banner text seen (number of reads, text):"]
        lines += [f"  {n:4}  {text}" for text, n in self.texts.most_common(200)] or ["  none"]
        with open(os.path.join(self.folder, "summary.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"[record] Recording saved: {self.folder} ({self.pictures} pictures). Read summary.txt first.")
