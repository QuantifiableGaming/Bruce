import time

import cv2
import numpy as np


class ScreenSource:
    """Captures the screen with mss. It does not touch the game process."""

    def __init__(self, monitor=1, fps=30):
        import mss

        self.sct = mss.mss()
        self.monitor = self.sct.monitors[monitor]
        self.interval = 1.0 / fps
        self.next_t = time.perf_counter()

    def read(self):
        now = time.perf_counter()
        if now < self.next_t:
            time.sleep(self.next_t - now)
        self.next_t = max(self.next_t + self.interval, time.perf_counter())
        shot = np.array(self.sct.grab(self.monitor))
        frame = np.ascontiguousarray(shot[:, :, :3])  # BGRA to BGR
        return time.perf_counter(), frame


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
