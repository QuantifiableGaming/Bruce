"""Read words with the text reader that is built into Windows 10 and 11 (Windows.Media.Ocr).

No extra program is needed. Python needs the small "winrt" packages from requirements.txt.
If they are missing, or Windows has no text-reading language, available() is False and the
coach uses Tesseract instead (if it is installed).
"""
import asyncio
import threading

import cv2
import numpy as np

_state = {"engine": None, "error": None, "checked": False}
_lock = threading.Lock()
_local = threading.local()


def _create_engine():
    from winrt.windows.media.ocr import OcrEngine

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        from winrt.windows.globalization import Language

        engine = OcrEngine.try_create_from_language(Language("en-US"))
    if engine is None:
        raise RuntimeError("Windows has no text-reading language. Add English in Settings > Time & language > Language.")
    return engine


def _bitmap(gray):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap

    h, w = gray.shape[:2]
    data = np.ascontiguousarray(gray, dtype=np.uint8).tobytes()
    return SoftwareBitmap.create_copy_from_buffer(data, BitmapPixelFormat.GRAY8, w, h)


def _max_dimension():
    try:
        from winrt.windows.media.ocr import OcrEngine

        return int(OcrEngine.max_image_dimension) or 2600
    except Exception:
        return 2600


def _loop():
    loop = getattr(_local, "loop", None)
    if loop is None:
        loop = _local.loop = asyncio.new_event_loop()
    return loop


def _read(gray):
    engine = _state["engine"]
    max_dim = _max_dimension()
    h, w = gray.shape[:2]
    if max(h, w) > max_dim:
        s = max_dim / float(max(h, w))
        gray = cv2.resize(gray, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    result = _loop().run_until_complete(engine.recognize_async(_bitmap(gray)))
    return " ".join(line.text for line in result.lines)


def available():
    """True when Windows can read text. The first call loads the reader and tests it once."""
    with _lock:
        if _state["checked"]:
            return _state["engine"] is not None
        _state["checked"] = True
        try:
            _state["engine"] = _create_engine()
            # Self-test: big black words on white. A working reader finds BUY.
            test = np.full((90, 420), 255, np.uint8)
            cv2.putText(test, "BUY PHASE", (12, 62), cv2.FONT_HERSHEY_DUPLEX, 1.8, 0, 3)
            if "BUY" not in _read(test).upper():
                raise RuntimeError("The Windows text reader did not pass its self-test.")
        except Exception as exc:
            _state["engine"] = None
            _state["error"] = f"{type(exc).__name__}: {exc}"
        return _state["engine"] is not None


def error():
    return _state["error"]


def read(gray):
    """Return the text in a grayscale picture (dark text on a light background works best).

    Returns None when the reader failed (not when it found no text).
    """
    if not available():
        return None
    try:
        return _read(gray)
    except Exception as exc:
        _state["error"] = f"{type(exc).__name__}: {exc}"
        return None
