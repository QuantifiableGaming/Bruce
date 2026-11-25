"""The death review: did you have an advantage when you died?

From the H3ners video "This is Why You Keep Dying": before each death, ask "Did I have an
advantage?" The video names four: a flash, a teammate with you, an off angle, and drone or dart info.
This module reads the screen for two of them, and for one disadvantage:

- flash advantage: a blinded enemy. Valorant puts a bright white mark over a blinded enemy's head.
- info advantage: revealed enemies. Fade's reveal shows magenta figures through walls. Sova's Recon
  Bolt shows orange-red figures with pink edges: only the pink edges are used, because orange-red
  matches walls, lamps and gun skins too often.
- your own flash or recon ability key in the last seconds (the table in agent_abilities.yaml).
- you were blinded: a full flash makes your screen one flat colour (peach, about RGB 224 186 164),
  with no detail, for about 0.1 to 0.25 seconds. Then it fades over about 0.6 seconds.

All values come from a Range recording (25 Nov 2026). The coach cannot see an off angle, so it asks
the player. "A teammate with you" needs the minimap, which the Range does not have.
"""
from collections import deque

import cv2
import numpy as np

from .util import crop_box

# Enemy outline colours (the same as vision.COLOR_RANGES, without the import cycle).
_OUTLINE = {
    "red": [((0, 150, 150), (8, 255, 255)), ((172, 150, 150), (180, 255, 255))],
    "yellow": [((24, 150, 150), (36, 255, 255))],
    "purple": [((138, 110, 120), (162, 255, 255))],
}
# Revealed enemies: bright, part-saturated magenta (Range test: hue 159 to 165, saturation 140 to 150).
_REVEAL = [((145, 100, 205), (178, 200, 255))]

DEFAULTS = {
    "enabled": True,
    "sample_s": 0.1,          # Read the screen 10 times a second (it is cheap at quarter size).
    "window_s": 5.0,          # Look at the last 5 seconds before each death.
    "min_frames": 3,          # A sign must show in 3 reads or more, so one bad read does not count.
    "use_blind_marks": False, # The blind mark gave false reads on the Range walls. Test it in real matches first.
    "mark_frames": 5,
}


def load_abilities(path=None):
    """{agent: {"flash": [slots], "info": [slots]}} from agent_abilities.yaml."""
    import os

    import yaml

    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_abilities.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, ValueError) as exc:
        print(f"[death] Could not read {path}: {exc}")
        return {}
    return {str(k).lower().replace("/", ""): v or {} for k, v in data.items()}


def settings(cfg):
    out = dict(DEFAULTS)
    out.update((cfg.get("checks", {}) or {}).get("advantage", {}) or {})
    return out


class AdvantageReader:
    """Reads one frame for the blinded screen, blind marks on enemies and revealed enemies."""

    def __init__(self, cfg):
        color = (cfg.get("enemy_highlight", {}) or {}).get("color", "yellow")
        self.outline = _OUTLINE.get(color, _OUTLINE["yellow"])
        regions = cfg.get("regions", {}) or {}
        self.region = regions.get("enemy_search") or [0.0, 0.11, 1.0, 0.84]
        self.ignore = regions.get("gun_ignore")

    def read(self, frame, marks=False):
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        return {
            "blinded": self._blinded(small),
            "blind_marks": self._blind_marks(frame) if marks else 0,
            "revealed": self._revealed(small, sw, sh),
        }

    @staticmethod
    def _blinded(small):
        sh, sw = small.shape[:2]
        c = small[int(sh * 0.18):int(sh * 0.82), int(sw * 0.15):int(sw * 0.85)]
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
        # Flat and bright: no detail at all. A wall or the sky always has some texture.
        return bool(g.std() < 6.0 and hsv[..., 2].mean() > 190)

    def _mask_ignore(self, m, shape, x0, y0, f=1):
        if not self.ignore:
            return
        gx0, gy0, gx1, gy1 = crop_box(shape, self.ignore)
        ax0, ay0 = max(gx0 // f - x0, 0), max(gy0 // f - y0, 0)
        ax1, ay1 = gx1 // f - x0, gy1 // f - y0
        if ax1 > ax0 and ay1 > ay0:
            m[ay0:ay1, ax0:ax1] = 0

    def _revealed(self, small, sw, sh):
        x0, y0, x1, y1 = crop_box(small.shape, self.region)
        hsv = cv2.cvtColor(small[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        m = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in _REVEAL:
            m |= cv2.inRange(hsv, np.array(lo), np.array(hi))
        self._mask_ignore(m, (sh * 4, sw * 4), x0, y0, 4)
        n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(m, np.ones((3, 3), np.uint8)))
        count = 0
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            # A revealed person at quarter size: a small upright figure (about 4 to 40 px tall).
            if 4 <= bh <= 60 and bh >= 1.2 * bw and area >= 6:
                count += 1
        return count

    def _blind_marks(self, frame):
        """Bright white marks with the enemy outline colour just below them."""
        x0, y0, x1, y1 = crop_box(frame.shape, self.region)
        hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        white = cv2.inRange(hsv, np.array((0, 0, 235)), np.array((180, 70, 255)))
        self._mask_ignore(white, frame.shape, x0, y0)
        outline = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in self.outline:
            outline |= cv2.inRange(hsv, np.array(lo), np.array(hi))
        n, _, stats, _ = cv2.connectedComponentsWithStats(white)
        H, W = white.shape
        marks = 0
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if not (12 <= area <= 900 and 0.5 <= bw / max(1, bh) <= 2.0):
                continue   # A mark is small and round. Lamps, sky and muzzle flashes are not.
            by0, by1 = y + bh, min(H, y + bh + 4 * bh)
            bx0, bx1 = max(0, x - bw), min(W, x + 2 * bw)
            if by1 > by0 and cv2.countNonZero(outline[by0:by1, bx0:bx1]) >= max(6, area // 3):
                marks += 1
        return marks


class DeathReview:
    """Keeps the last few seconds of reads, and sums them up when you die."""

    def __init__(self, cfg, abilities=None):
        self.s = settings(cfg)
        self.enabled = bool(self.s["enabled"])
        self.reader = AdvantageReader(cfg) if self.enabled else None
        self.abilities = load_abilities() if abilities is None else abilities
        self.reads = deque()
        self.next_t = -1e9

    def on_frame(self, t, frame):
        if not self.enabled or t < self.next_t:
            return
        self.next_t = t + float(self.s["sample_s"])
        self.reads.append((t, self.reader.read(frame, bool(self.s["use_blind_marks"]))))
        while self.reads and t - self.reads[0][0] > float(self.s["window_s"]) + 3.0:
            self.reads.popleft()

    def review(self, death_t, agent=None, slots_used=()):
        """Return the advantages before a death, or None when the review is off.

        agent: the agent name (lower case). slots_used: the ability slots pressed in the window.
        """
        if not self.enabled:
            return None
        start = death_t - float(self.s["window_s"])
        window = [r for t, r in self.reads if start <= t <= death_t]
        need = int(self.s["min_frames"])
        kit = self.abilities.get(str(agent or "").lower().replace("/", ""), {})
        own_flash = bool(set(kit.get("flash") or []) & set(slots_used))
        own_info = bool(set(kit.get("info") or []) & set(slots_used))
        marks = sum(1 for r in window if r["blind_marks"]) >= int(self.s["mark_frames"])
        seen = sum(1 for r in window if r["revealed"]) >= need
        return {
            "flash": own_flash or marks,
            "info": own_info or seen,
            "own_flash": own_flash,
            "own_info": own_info,
            "blinded": sum(1 for r in window if r["blinded"]) >= 1,
            "reads": len(window),
        }

    def clear(self):
        self.reads.clear()


def describe(review):
    """One short line for the log and the report. Empty when there is no review."""
    if not review or not review["reads"]:
        return ""
    have = []
    if review["flash"]:
        have.append("your flash" if review["own_flash"] else "a flash")
    if review["info"]:
        have.append("your info" if review["own_info"] else "revealed enemies")
    text = ("Advantage: " + " and ".join(have)) if have else "No advantage seen"
    if review["blinded"]:
        text += ". You were blinded"
    return text + "."


def ai_note(reviews):
    """A note for the AI line at the end of the round, or None."""
    reviews = [r for r in reviews if r]
    if not reviews:
        return None
    lines = "; ".join(r.rstrip(".") for r in reviews)
    return ("Death review (from the video 'This is Why You Keep Dying'): " + lines + ". "
            "The coach cannot see an off angle or a teammate next to the player. If no advantage was seen, "
            "ask the player in one short question: did they have an off angle or a teammate to trade them? "
            "Remind them to wait for a flash, info or a teammate before they take a fight. "
            "If they were blinded, say that the fight was lost before it started.")
