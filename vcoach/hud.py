import os
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from .digits import DigitReader, HUD_DIGIT_FRACTION, SCORE_DIGIT_FRACTION
from .util import crop


TESSERACT_HELP = ("Tesseract is not installed, or the coach cannot find it. "
                  "Run install.bat and press Y to install it, or install it from "
                  "github.com/UB-Mannheim/tesseract/wiki into the default folder.")


def text_reader_name(cfg):
    """Which word reader the coach will use: "windows", "tesseract" or None."""
    choice = str(cfg.get("text_reader", "auto")).lower()
    if choice in ("auto", "windows"):
        from . import winocr

        if winocr.available():
            return "windows"
    if choice in ("auto", "tesseract") and find_tesseract(cfg.get("tesseract_cmd") or ""):
        return "tesseract"
    return None


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
    "agent_panel": [0.72, 0.09, 0.18, 0.11],          # Role and agent name in agent select (old layout).
    "agent_cards": [0.30, 0.885, 0.40, 0.05],         # The player cards in agent select (new layout, 25 Nov 2026).
    "result": [0.30, 0.30, 0.40, 0.32],               # The big VICTORY or DEFEAT (and SURRENDERED) at the match end.
}

RESULT_TEXT = ("VICTORY", "DEFEAT", "DRAW")


class HudReader:
    """Reads the game HUD.

    Numbers (ammo, health, score) use the fast digit reader in digits.py.
    Words (banner, menus, agent names) use the text reader built into Windows (winocr.py).
    When it is not available, the coach uses Tesseract if it is installed. The coach starts
    tesseract directly and sends the picture through a pipe, which is faster than temporary
    files. With no word reader, the coach still follows rounds by the score, but it cannot
    see buy phase, round credits, the match result or the agent you pick.
    Set number_reader to "tesseract" in config.yaml to read numbers with Tesseract.
    """

    # The reader sometimes sees a digit where the text has a letter.
    LOOKALIKE = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "$": "S", "|": "I"})

    def __init__(self, cfg):
        self.exe = find_tesseract(cfg.get("tesseract_cmd") or "")
        self.text_reader = text_reader_name(cfg)
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
        if self.digits is None and self.exe is None:
            raise RuntimeError(TESSERACT_HELP)
        if self.text_reader is None:
            from . import winocr

            print("[reader] No word reader: the coach cannot read BUY PHASE, round credits, VICTORY or agent names. "
                  f"Windows text reader: {winocr.error() or 'off'}. Tesseract: not found.")

    def ocr(self, img, psm, whitelist=None):
        if self.exe is None:
            return ""
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

    def timer(self, frame):
        """The round timer at the top centre, in seconds left. None when it is not on the screen."""
        return self.digits.timer(frame) if self.digits else None

    def ammo_health(self, frame):
        """Read ammo and health at the same time."""
        if self.digits:
            return self.ammo(frame), self.health(frame)
        a = self.pool.submit(self.ammo, frame)
        h = self.pool.submit(self.health, frame)
        return a.result(), h.result()

    def read_text(self, img, psm=7):
        """All text (letters and numbers) in a black-on-white picture, with the word reader."""
        if self.text_reader == "windows":
            from . import winocr

            raw = winocr.read(img)
            if raw is not None:
                return raw
        return self.ocr(img, psm) if self.exe else ""

    def my_scoreboard(self, frame):
        """Your own line on the end-of-match scoreboard, or None. See scoreboard.py.

        When the coach finds your line but cannot read it, it returns {"unread": text} and saves
        the line as logs/scoreboard_unread.png, so the problem can be seen (19 Nov: no numbers in
        any report, and nothing in the log to say why)."""
        if self.text_reader is None and not self.exe:
            return None
        from .scoreboard import find_my_row, my_row_image, parse

        row = find_my_row(frame)
        if row is None:
            return None
        binary = my_row_image(frame, row)
        texts = []
        if self.text_reader is not None:
            texts.append(self.read_text(binary))
        if self.exe and self.text_reader != "tesseract":
            texts.append(self.ocr(binary, 7))          # Second chance: Tesseract reads one line well.
        for text in texts:
            result = parse(text)
            if result is not None:
                result["row"] = [int(row[0]), int(row[1])]
                return result
        try:
            import time as _time
            if _time.monotonic() - getattr(self, "_unread_saved", -999) > 30:
                self._unread_saved = _time.monotonic()
                os.makedirs("logs", exist_ok=True)
                cv2.imwrite(os.path.join("logs", "scoreboard_unread.png"), binary)
        except Exception:
            pass
        return {"unread": " | ".join(t.strip() for t in texts if t)}

    def _words(self, binary, psm=11):
        if self.text_reader == "windows":
            from . import winocr

            raw = winocr.read(binary)
            if raw is None:
                if self.exe:
                    print(f"[reader] The Windows text reader failed ({winocr.error()}). The coach now uses Tesseract.")
                    self.text_reader = "tesseract"
                    raw = self.ocr(binary, psm)
                else:
                    raw = ""
        elif self.text_reader == "tesseract":
            raw = self.ocr(binary, psm)
        else:
            return None
        text = re.sub(r"[^A-Z ]", " ", raw.upper().translate(self.LOOKALIKE))
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

    def result_banner(self, frame):
        """The big match result in the middle of the screen, for example "SURRENDERED VICTORY"."""
        img = crop(frame, self.regions["result"])
        if img.size == 0:
            return None
        _, binary = cv2.threshold(img.max(axis=2), 200, 255, cv2.THRESH_BINARY_INV)
        return self._words(binary)

    def agent_panel(self, frame):
        """Read your agent name in agent select.

        25 Nov 2026: Valorant's new agent select has no panel at the top right. Each player has a card
        at the bottom. Your card has a gold name ("Me (KAY/O)"), and your agent's name is on the line
        under it. The old panel is still read when the new card is not found.
        """
        card = self.my_agent_card(frame)
        if card:
            return card
        img = crop(frame, self.regions["agent_panel"])
        if img.size == 0:
            return None
        gray = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return self._words(binary)

    def my_agent_card(self, frame):
        """The agent line under your gold name on the agent select cards, or None."""
        strip = crop(frame, self.regions["agent_cards"])
        if strip.size == 0:
            return None
        h, w = strip.shape[:2]
        names = strip[: h * 11 // 20]
        hsv = cv2.cvtColor(names, cv2.COLOR_BGR2HSV)
        gold = cv2.inRange(hsv, np.array((15, 90, 150)), np.array((35, 255, 255)))
        cols = np.nonzero(gold.sum(axis=0) > 0)[0]
        if len(cols) < max(6, w // 100):
            return None                     # No gold name: not agent select, or a different layout.
        cx = int(np.median(cols))
        half = max(10, int(frame.shape[1] * 0.036))
        line = strip[h * 11 // 20:, max(0, cx - half):min(w, cx + half)]
        gray = cv2.resize(cv2.cvtColor(line, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        text = self._words(binary, psm=7)
        return text.replace("/", "") if text else None


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
        # The Shooting Error stat shows one shot's value for only about 0.1 s, so it gets its own
        # reader that looks at every new frame. It reads a small strip at a known place (about 1 ms).
        self.threads = [threading.Thread(target=self._loop, args=(fn, period), daemon=True)
                        for fn, period in ((self._read_hud, hud_period), (self._read_scene, min_period),
                                           (self._read_stat, 0.0))]
        self.last_score_t = None
        self.last_stat = None

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
        self.results.put(("hud", t, (ammo, health, spectating, None)))   # The stat has its own reader.
        self._record("save_hud", frame, ammo, health, spectating, self.last_stat)

    def _read_stat(self, t, frame):
        check = getattr(self.reader, "shooting_error", None)
        value = check(frame) if check else None
        self.last_stat = value
        if value is not None:
            self.results.put(("stat", t, value))

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
    check = getattr(reader, "timer", None)
    if check and score is not None:
        events.append(("timer", t, check(frame)))
    events.append(("banner", t, reader.banner(frame)))
    check = getattr(reader, "result_banner", None)
    if check and score is None:
        # The match result shows in the middle for a few seconds. A surrender has no results
        # screen time to wait for, so read it at once.
        text = check(frame)
        if text and any(w in text.split() for w in RESULT_TEXT):
            events.append(("screen", t, text))
    if state.last_score_t is None or t - state.last_score_t > 3.0:
        screen = reader.top_left(frame)
        events.append(("screen", t, screen))
        if screen:
            events.append(("agent", t, reader.agent_panel(frame)))
        check = getattr(reader, "my_scoreboard", None)
        board = check(frame) if check else None
        if board:
            events.append(("scoreboard", t, board))
    return events
