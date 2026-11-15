import os
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2

from .digits import DigitReader, HUD_DIGIT_FRACTION, SCORE_DIGIT_FRACTION
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


# One thread for each text reader process. More threads make short reads slower.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# Default screen regions that are the same for every 16:9 screen. Fractions of the screen.
FIXED_REGIONS = {
    "score_left": [0.4167, 0.0278, 0.0250, 0.0333],   # Your team's round score, left of the timer.
    "score_right": [0.5583, 0.0278, 0.0250, 0.0333],  # The enemy team's round score.
    "top_left": [0.0, 0.085, 0.42, 0.12],             # Map and mode in agent select. VICTORY or DEFEAT after a match.
    "agent_panel": [0.72, 0.09, 0.18, 0.11],          # Role and agent name in agent select.
}


class HudReader:
    """Reads the game HUD.

    Numbers (ammo, health, score) use the fast digit reader in digits.py.
    Words (banner, menus) use Tesseract OCR. The reader starts tesseract directly
    and sends the picture through a pipe. This is faster than temporary files,
    which virus scanners check on every read.
    Set number_reader to "tesseract" in config.yaml to read numbers with Tesseract.
    """

    # The reader sometimes sees a digit where the text has a letter.
    LOOKALIKE = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "$": "S", "|": "I"})

    def __init__(self, cfg):
        path = find_tesseract(cfg.get("tesseract_cmd") or "")
        if path is None:
            raise RuntimeError(TESSERACT_HELP)
        self.exe = path
        self.regions = dict(FIXED_REGIONS)
        self.regions.update(cfg["regions"])
        self.flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.digits = None
        if str(cfg.get("number_reader", "fast")).lower() != "tesseract":
            try:
                self.digits = DigitReader()
            except Exception as exc:
                print(f"[reader] The fast digit reader did not load ({exc}). The coach uses Tesseract for numbers.")

    def ocr(self, img, psm, whitelist=None):
        ok, png = cv2.imencode(".png", img)
        if not ok:
            return ""
        cmd = [self.exe, "stdin", "stdout", "--psm", str(psm)]
        if whitelist:
            cmd += ["-c", "tessedit_char_whitelist=" + whitelist]
        try:
            out = subprocess.run(cmd, input=png.tobytes(), capture_output=True, timeout=10,
                                 creationflags=self.flags)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return out.stdout.decode("utf-8", "replace")

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
        digits = re.sub(r"\D", "", self.ocr(self.prepare(img), 7, "0123456789"))
        if not digits or len(digits) > 3:
            return None
        return int(digits)

    @staticmethod
    def _wide(frame, region, extra):
        """Cut a region from the frame with extra width on each side. extra is a part of the screen height."""
        h, w = frame.shape[:2]
        x, y, rw, rh = region
        pad = int(round(extra * h))
        x0, x1 = max(0, int(x * w) - pad), min(w, int((x + rw) * w) + pad)
        return frame[max(0, int(y * h)):min(h, int((y + rh) * h)), x0:x1]

    def _fast_number(self, frame, key):
        # A digit at the edge of the box is still readable with the extra width.
        img = self._wide(frame, self.regions[key], 0.0075)
        return self.digits.read(img, HUD_DIGIT_FRACTION * frame.shape[0])[0]

    def ammo(self, frame):
        if self.digits:
            return self._fast_number(frame, "ammo")
        return self._number(crop(frame, self.regions["ammo"]))

    def health(self, frame):
        if self.digits:
            return self._fast_number(frame, "health")
        return self._number(crop(frame, self.regions["health"]))

    def spectating(self, frame):
        """True when you are dead and watch a teammate. None when the fast reader is off."""
        return self.digits.spectating(frame) if self.digits else None

    def shooting_error(self, frame):
        """Valorant's "Shooting Error" stat in degrees, or None when it is not on the screen."""
        return self.digits.shooting_error(frame) if self.digits else None

    def ammo_health(self, frame):
        """Read ammo and health at the same time."""
        if self.digits:
            return self.ammo(frame), self.health(frame)
        a = self.pool.submit(self.ammo, frame)
        h = self.pool.submit(self.health, frame)
        return a.result(), h.result()

    def _words(self, binary, psm=11):
        text = re.sub(r"[^A-Z ]", " ", self.ocr(binary, psm).upper().translate(self.LOOKALIKE))
        return " ".join(w for w in text.split() if len(w) >= 3) or None

    def banner(self, frame):
        """Read the banner under the timer: BUY PHASE, WON, LOST, CLUTCH, TEAM ACE and so on."""
        img = crop(frame, self.regions["banner"])
        if img.size == 0:
            return None
        _, binary = cv2.threshold(img.max(axis=2), 200, 255, cv2.THRESH_BINARY_INV)
        return self._words(binary)

    def _score_digit(self, frame, key):
        img = crop(frame, self.regions[key])
        if img.size == 0:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        binary = cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        text = self.ocr(binary, 10, "0123456789").strip()
        return int(text) if text.isdigit() and len(text) <= 2 else None

    def score(self, frame):
        """Read the round score at the top of the screen. Return (yours, enemy) or None."""
        if self.digits:
            height = SCORE_DIGIT_FRACTION * frame.shape[0]
            left, right = (self.digits.read(crop(frame, self.regions[key]), height, 2, "thin")[0]
                           for key in ("score_left", "score_right"))
            if left is None or right is None or left > 30 or right > 30:
                return None
            return (left, right)
        a = self.pool.submit(self._score_digit, frame, "score_left")
        b = self.pool.submit(self._score_digit, frame, "score_right")
        left, right = a.result(), b.result()
        if left is None or right is None or left > 30 or right > 30:
            return None
        return (left, right)

    def top_left(self, frame):
        """Read the top-left area: map and mode in agent select, or the match result."""
        img = crop(frame, self.regions["top_left"])
        if img.size == 0:
            return None
        value = cv2.resize(img.max(axis=2), None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(value, 170, 255, cv2.THRESH_BINARY_INV)
        return self._words(binary)

    def agent_panel(self, frame):
        """Read the role and agent name in agent select."""
        img = crop(frame, self.regions["agent_panel"])
        if img.size == 0:
            return None
        gray = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return self._words(binary)


class HudWorker:
    """Reads the newest frame in two background threads, so screen capture stays fast.

    Thread 1 reads ammo and health 20 times each second.
    Thread 2 reads the score and the banner. When there is no score on screen (menus,
    agent select, results), it also reads the top-left area and the agent panel.
    Results are events: (kind, time, value).
    """

    def __init__(self, reader, recorder=None, min_period=0.1, hud_period=0.05):
        self.reader = reader
        self.recorder = recorder
        self.min_period = min_period
        self.lock = threading.Lock()
        self.latest = None
        self.results = queue.Queue()
        self.stop_event = threading.Event()
        self.threads = [threading.Thread(target=self._loop, args=(fn, period), daemon=True)
                        for fn, period in ((self._read_hud, hud_period), (self._read_scene, min_period))]
        self.last_score_t = None

    def start(self):
        for th in self.threads:
            th.start()

    def join(self, timeout=None):
        for th in self.threads:
            th.join(timeout)

    def publish(self, t, frame):
        with self.lock:
            self.latest = (t, frame)

    def _record(self, method, *args):
        if self.recorder:
            try:
                getattr(self.recorder, method)(*args)
            except Exception as exc:
                print(f"[record] ERROR: {exc}. Recording stopped.")
                self.recorder = None

    def _read_hud(self, t, frame):
        ammo, health = self.reader.ammo_health(frame)
        check = getattr(self.reader, "spectating", None)
        spectating = check(frame) if check else None
        check = getattr(self.reader, "shooting_error", None)
        shot_error = check(frame) if check else None
        self.results.put(("hud", t, (ammo, health, spectating, shot_error)))
        self._record("save_hud", frame, ammo, health, spectating, shot_error)

    def _read_scene(self, t, frame):
        events = read_scene(self.reader, t, frame, self)
        for event in events:
            self.results.put(event)
        self._record("save_scene", frame, {kind: value for kind, _, value in events})

    def _loop(self, fn, period):
        last_t = None
        while not self.stop_event.is_set():
            start = time.perf_counter()
            with self.lock:
                item = self.latest
            if item is not None and item[0] != last_t:
                last_t = item[0]
                try:
                    fn(*item)
                except Exception as exc:
                    print(f"[reader] ERROR: {exc}")
                    time.sleep(1.0)
            time.sleep(max(0.005, period - (time.perf_counter() - start)))

    def drain(self):
        items = []
        while True:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                return items

    def stop(self):
        self.stop_event.set()


def read_scene(reader, t, frame, state):
    """Read score and banner. With no score for 3 seconds, also read the menu areas.

    state is any object with a last_score_t attribute. Returns a list of events.
    """
    events = []
    score = reader.score(frame)
    events.append(("score", t, score))
    if score is not None:
        state.last_score_t = t
    events.append(("banner", t, reader.banner(frame)))
    if state.last_score_t is None or t - state.last_score_t > 3.0:
        screen = reader.top_left(frame)
        events.append(("screen", t, screen))
        if screen:
            events.append(("agent", t, reader.agent_panel(frame)))
    return events
