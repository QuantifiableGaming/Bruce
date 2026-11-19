import time

import cv2
import numpy as np


ATTEMPTS = 3   # Capture tries before a frame is skipped


class ScreenSource:
    """Captures the screen with mss. It does not touch the game process."""

    def __init__(self, monitor=1, fps=30):
        import mss

        self._mss = mss
        self.monitor_index = monitor
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[monitor]
        self.interval = 1.0 / fps
        self.next_t = time.perf_counter()
        self.failing_since = None

    def read(self):
        """(time, frame). The frame is None when Windows cannot capture the screen for a moment:
        the PC is locked, a Windows permission prompt is open, the display sleeps, or the game
        changes its display mode. 18 Nov: this crashed the coach (BitBlt failed)."""
        now = time.perf_counter()
        if now < self.next_t:
            time.sleep(self.next_t - now)
        self.next_t = max(self.next_t + self.interval, time.perf_counter())
        shot = None
        for attempt in range(ATTEMPTS):                     # Try 3 times, then skip this frame.
            try:
                shot = np.asarray(self.sct.grab(self.monitor))   # A view of the grab: no copy yet.
                break
            except Exception as exc:                         # mss.exception.ScreenShotError and others
                error = exc
                time.sleep(0.05)
                if attempt == 1:
                    self._new_handle()                       # A new capture handle often fixes it
        if shot is None:
            if self.failing_since is None:
                self.failing_since = time.perf_counter()
                print(f"[capture] Windows cannot capture the screen right now ({type(error).__name__}). "
                      "The PC may be locked, a Windows prompt may be open, or the display may be off. "
                      f"The coach skips the frame after {ATTEMPTS} tries, and goes on.")
            time.sleep(0.3)
            return time.perf_counter(), None
        if self.failing_since is not None:
            print(f"[capture] Screen capture works again after {time.perf_counter() - self.failing_since:.0f} s.")
            self.failing_since = None
        frame = np.ascontiguousarray(shot[:, :, :3])  # BGRA to BGR, in one copy
        return time.perf_counter(), frame

    def _new_handle(self):
        try:
            self.sct.close()
        except Exception:
            pass
        try:
            self.sct = self._mss.mss()
            self.monitor = self.sct.monitors[self.monitor_index]
        except Exception:
            pass

    def read_frame(self, wait_s=10.0):
        """One frame for a screenshot: tries again for up to wait_s seconds."""
        end = time.perf_counter() + wait_s
        while True:
            t, frame = self.read()
            if frame is not None or time.perf_counter() > end:
                return t, frame


class VideoSource:
    """Reads a recorded clip. Timestamps come from the video, not the clock."""

    def __init__(self, path):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.index = 0

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None, None
        t = self.index / self.fps
        self.index += 1
        return t, frame
