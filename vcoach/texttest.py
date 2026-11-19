"""Compare the word readers on your own recordings, and save the result in logs/text_test.txt.

It reads the banner and the top-left text in saved pictures with each reader that works on
this PC (the Windows text reader, and Tesseract if it is installed). It then shows the words
each reader found and how long each read took.
"""
import glob
import os
import time

import cv2
import numpy as np

WORDS = ["BUY PHASE", "MATCH POINT", "WON", "LOST", "CLUTCH", "ACE", "FLAWLESS", "THRIFTY",
         "VICTORY", "DEFEAT", "DRAW", "SWIFTPLAY", "UNRATED", "COMPETITIVE", "DEATHMATCH"]


def _keywords(text):
    text = " " + " ".join((text or "").upper().split()) + " "
    return [w for w in WORDS if f" {w} " in text]


def _samples(limit_banner=60, limit_full=30):
    folders = sorted(glob.glob(os.path.join("recordings", "match_*")))
    banners, fulls = [], []
    for folder in reversed(folders):
        banners += sorted(glob.glob(os.path.join(folder, "banner", "*.jpg")))
        fulls += sorted(glob.glob(os.path.join(folder, "full", "*.jpg")))
    pick = lambda items, n: items if len(items) <= n else [items[int(i * len(items) / n)] for i in range(n)]  # noqa: E731
    return pick(banners, limit_banner), pick(fulls, limit_full)


def _banner_frame(path):
    """A recording keeps the top 30% and the middle 50% of the screen. Put it back in a full frame."""
    crop = cv2.imread(path)
    if crop is None:
        return None
    h = int(round(crop.shape[0] / 0.30))
    w = int(round(crop.shape[1] / 0.50))
    frame = np.zeros((h, w, 3), np.uint8)
    frame[: crop.shape[0], int(w * 0.25): int(w * 0.25) + crop.shape[1]] = crop
    return frame


def run_text_test(cfg, out_path=os.path.join("logs", "text_test.txt")):
    from . import winocr
    from .hud import HudReader, find_tesseract

    lines = []
    say = lambda s="": (print(s), lines.append(s))  # noqa: E731
    readers = {}
    if winocr.available():
        readers["windows"] = HudReader(dict(cfg, text_reader="windows"))
        say("Windows text reader: works.")
    else:
        say(f"Windows text reader: not available ({winocr.error()}).")
    tess = find_tesseract(cfg.get("tesseract_cmd") or "")
    if tess:
        readers["tesseract"] = HudReader(dict(cfg, text_reader="tesseract"))
        say(f"Tesseract: {tess}")
    else:
        say("Tesseract: not installed.")
    if not readers:
        say("No word reader works. Install the Python packages from the Home page.")
        _save(out_path, lines)
        return 1

    banners, fulls = _samples()
    if not banners and not fulls:
        say("No recordings found. Turn on Settings > Record the next match, play a match, then run this test again.")
        _save(out_path, lines)
        return 0
    say(f"Pictures: {len(banners)} banner pictures and {len(fulls)} full screens from your recordings.")
    say("")
    names = list(readers)
    times = {n: [] for n in names}
    found = {n: 0 for n in names}
    agree = total = 0
    jobs = [(p, "banner", _banner_frame) for p in banners] + [(p, "top_left", cv2.imread) for p in fulls]
    for path, area, load in jobs:
        frame = load(path)
        if frame is None:
            continue
        row = {}
        for n in names:
            start = time.perf_counter()
            text = readers[n].banner(frame) if area == "banner" else readers[n].top_left(frame)
            times[n].append((time.perf_counter() - start) * 1000)
            row[n] = (text or "", _keywords(text))
            found[n] += bool(row[n][1])
        if any(k for _, k in row.values()):
            total += 1
            agree += len({tuple(k) for _, k in row.values()}) == 1
        say(f"{os.path.basename(os.path.dirname(os.path.dirname(path)))}/{area}/{os.path.basename(path)}")
        for n in names:
            say(f"    {n:10} {', '.join(row[n][1]) or '-':30} | {row[n][0][:70]}")
    say("")
    for n in names:
        t = sorted(times[n])
        say(f"{n}: words found in {found[n]} of {len(t)} pictures. Time per read: median {t[len(t) // 2]:.0f} ms.")
    if len(names) > 1 and total:
        say(f"Both readers found the same words in {agree} of {total} pictures that had a word.")
    _save(out_path, lines)
    print(f"Saved {out_path}")
    return 0


def _save(path, lines):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
