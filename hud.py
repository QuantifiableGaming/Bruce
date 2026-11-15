import queue
import re
import threading
import time

import cv2

from .util import crop


TESSERACT_HELP = ("Tesseract is not installed, or the coach cannot find it. "
                  "Run install.bat and press Y to install it, or install it from "
                  "github.com/UB-Mannheim/tesseract/wiki into the default folder.")


def find_tesseract(configured=""):
    """Return the path to tesseract.exe, or None. Checks the setting, PATH, and the usual folders."""
    import os
    import shutil

    candidates = [configured] if configured else []
    on_path = shutil.which("tesseract")
    if on_path:
        candidates.append(on_path)
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tesseract-OCR")):
        if base:
            candidates.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
            candidates.append(os.path.join(base, "tesseract.exe"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class HudReader:
    """Reads ammo, health and the round banner with Tesseract OCR."""

    def __init__(self, cfg):
        import pytesseract

        self.tess = pytesseract
        path = find_tesseract(cfg.get("tesseract_cmd") or "")
        if path is None:
            raise RuntimeError(TESSERACT_HELP)
        pytesseract.pytesseract.tesseract_cmd = path
        self.regions = cfg["regions"]

    @staticmethod
    def prepare(img, thresh=170):
        # The max channel keeps white text and also red "low ammo" text bright.
        value = img.max(axis=2)
        value = cv2.resize(value, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(value, thresh, 255, cv2.THRESH_BINARY_INV)
        return binary

    def _number(self, img):
        if img.size == 0:
            return None
        text = self.tess.image_to_string(
            self.prepare(img), config="--psm 7 -c tessedit_char_whitelist=0123456789"
        )
        digits = re.sub(r"\D", "", text)
        if not digits or len(digits) > 3:
            return None
        return int(digits)

    def ammo(self, frame):
        return self._number(crop(frame, self.regions["ammo"]))

    def health(self, frame):
        return self._number(crop(frame, self.regions["health"]))

    # The reader sometimes sees a digit where the banner has a letter.
    LOOKALIKE = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "$": "S", "|": "I"})

    def banner(self, frame):
        """Read the banner area and return all words found.

        Large bright text on a busy game scene reads best with a high brightness
        limit and the "sparse text" mode. Two brightness limits cover coloured text.
        """
        img = crop(frame, self.regions["banner"])
        if img.size == 0:
            return None
        found = []
        for thresh in (220, 180):
            value = img.max(axis=2)
            value = cv2.resize(value, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            _, binary = cv2.threshold(value, thresh, 255, cv2.THRESH_BINARY_INV)
            text = self.tess.image_to_string(binary, config="--psm 11")
            text = re.sub(r"[^A-Z ]", " ", text.upper().translate(self.LOOKALIKE))
            for word in text.split():
                # The reader sometimes joins two words, for example ROUNDWIN.
                parts = ["ROUND", word[5:]] if word.startswith("ROUND") and len(word) > 5 else [word]
                for part in parts:
                    if len(part) >= 3 and part not in found:
                        found.append(part)
        return " ".join(found) or None


class HudWorker(threading.Thread):
    """Runs OCR on the newest frame in a background thread, so capture stays fast."""

    def __init__(self, reader, rate_hz, banner_every, recorder=None):
        super().__init__(daemon=True)
        self.reader = reader
        self.recorder = recorder
        self.period = 1.0 / rate_hz
        self.banner_every = max(1, banner_every)
        self.lock = threading.Lock()
        self.latest = None
        self.results = queue.Queue()
        self.stop_event = threading.Event()

    def publish(self, t, frame):
        with self.lock:
            self.latest = (t, frame)

    def run(self):
        count = 0
        last_t = None
        while not self.stop_event.is_set():
            start = time.perf_counter()
            with self.lock:
                item = self.latest
            if item is not None and item[0] != last_t:
                t, frame = item
                last_t = t
                banner_read = count % self.banner_every == 0
                banner = self.reader.banner(frame) if banner_read else None
                ammo, health = self.reader.ammo(frame), self.reader.health(frame)
                self.results.put((t, ammo, health, banner))
                if self.recorder:
                    try:
                        self.recorder.save(frame, ammo, health, banner_read, banner)
                    except Exception as exc:
                        print(f"[record] ERROR: {exc}. Recording stopped.")
                        self.recorder = None
                count += 1
            time.sleep(max(0.0, self.period - (time.perf_counter() - start)))

    def drain(self):
        items = []
        while True:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                return items

    def stop(self):
        self.stop_event.set()
