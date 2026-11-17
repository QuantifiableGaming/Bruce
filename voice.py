import queue
import threading


def _sapi_rate(wpm):
    """Convert words per minute to the Windows speech rate (-10 to 10)."""
    return max(-10, min(10, round((wpm - 180) / 15)))


class SapiBackend:
    """Talks to the Windows speech system (SAPI) directly. Works in a background thread."""

    name = "Windows speech (SAPI)"

    def __init__(self):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()  # Needed once in each thread that uses Windows speech.
        self.sp = win32com.client.Dispatch("SAPI.SpVoice")
        self.sp.Volume = 100
        self.default = self.sp.Voice

    def voices(self):
        tokens = self.sp.GetVoices()
        return [tokens.Item(i) for i in range(tokens.Count)]

    def apply(self, rate, voice_name):
        self.sp.Rate = _sapi_rate(rate)
        self.sp.Voice = self.default
        if voice_name:
            match = next((v for v in self.voices() if voice_name.lower() in v.GetDescription().lower()), None)
            if match is not None:
                self.sp.Voice = match
            else:
                print(f"[voice] No voice matches '{voice_name}'. Using the default voice.")

    def speak(self, text, cut=lambda: False):
        # Speak in the background (flag 1) and check every 50 ms if the line must stop.
        self.sp.Speak(text, 1)
        while not self.sp.WaitUntilDone(50):
            if cut():
                self.sp.Speak("", 3)   # 1 + 2: stop the current line at once
                return

    def voice_names(self):
        return [v.GetDescription() for v in self.voices()]


class Pyttsx3Backend:
    """Fallback: the pyttsx3 library."""

    name = "pyttsx3"

    def __init__(self):
        import pyttsx3

        self.engine = pyttsx3.init()
        self.default = self.engine.getProperty("voice")

    def apply(self, rate, voice_name):
        self.engine.setProperty("rate", rate)
        self.engine.setProperty("voice", self.default)
        if voice_name:
            match = next((v for v in self.engine.getProperty("voices")
                          if voice_name.lower() in v.name.lower()), None)
            if match:
                self.engine.setProperty("voice", match.id)
            else:
                print(f"[voice] No voice matches '{voice_name}'. Using the default voice.")

    def speak(self, text):
        self.engine.say(text)
        self.engine.runAndWait()

    def voice_names(self):
        return [v.name for v in self.engine.getProperty("voices")]


BACKENDS = [SapiBackend, Pyttsx3Backend]


def _open_backend(start=0, backends=None):
    """Return (backend, index) for the first speech engine that starts, or (None, None)."""
    backends = BACKENDS if backends is None else backends
    for i in range(start, len(backends)):
        try:
            return backends[i](), i
        except Exception as exc:
            print(f"[voice] {backends[i].name} is not available: {exc}")
    return None, None


class Voice:
    """Speaks lines in its own thread, so the coach never waits for speech."""

    def __init__(self, rate=185, silent=False, voice_name="", backends=None, eleven=None, personality=None):
        self.rate = rate
        self.silent = silent
        self.voice_name = voice_name or ""
        self.eleven = eleven              # An ElevenVoice, or None for the Windows voice only.
        self.personality = personality    # Chooses the ElevenLabs voice.
        self.backends = backends
        self.muted = False
        self.allowed = lambda: True    # The session sets this: False while a round is live.
        self.gen = 0                   # stop() raises this. Lines from an older generation are dropped.
        self.speaking = False
        self.q = queue.Queue()
        if not silent:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        backend, index = _open_backend(0, self.backends)
        if backend is None:
            print("[voice] ERROR: no speech engine works. The coach cannot speak. Lines still show in the log.")
        else:
            print(f"[voice] Speech engine: {backend.name}")
            self._apply(backend)
        if self.eleven is not None:
            print("[voice] ElevenLabs voices on. The Windows voice is the backup.")
        while True:
            item = self.q.get()
            try:
                if item is None:
                    return
                if isinstance(item, tuple) and item[0] == "style":
                    if backend is not None:
                        self._apply(backend)
                    continue
                gen, item = item
                if gen != self.gen:
                    continue           # stop() was called after this line was queued
                cut = lambda: self.gen != gen  # noqa: E731
                self.speaking = True
                if self._speak_eleven(item, cut):
                    continue
                while backend is not None:
                    try:
                        try:
                            backend.speak(item, cut)
                        except TypeError:
                            backend.speak(item)   # an engine that cannot stop in the middle of a line
                        break
                    except Exception as exc:
                        # Show the error and try the next speech engine.
                        print(f"[voice] ERROR in {backend.name}: {exc}")
                        backend, index = _open_backend(index + 1, self.backends)
                        if backend is not None:
                            print(f"[voice] Speech engine: {backend.name}")
                            self._apply(backend)
                        else:
                            print("[voice] ERROR: no speech engine works. The coach cannot speak.")
            finally:
                self.speaking = False
                self.q.task_done()

    def stop(self):
        """Stop the current line at once and drop every queued line."""
        self.gen += 1

    def busy(self):
        return self.speaking or not self.q.empty()

    def _speak_eleven(self, text, cut=lambda: False):
        """Speak with ElevenLabs. Return False if the Windows voice must speak the line."""
        if self.eleven is None:
            return False
        from .eleven import ElevenError, play_pcm

        try:
            audio = self.eleven.audio(text, self.eleven.voice_for(self.personality), self.rate)
            if not cut():
                play_pcm(audio, cut=cut)
            return True
        except ElevenError as exc:
            print(f"[voice] ERROR: {exc}")
            if exc.fatal:
                print("[voice] ElevenLabs is off until the coach restarts. The Windows voice speaks.")
                self.eleven = None
        except Exception as exc:
            print(f"[voice] ERROR: could not play the ElevenLabs audio: {exc}")
        return False

    def _apply(self, backend):
        try:
            backend.apply(self.rate, self.voice_name)
        except Exception as exc:
            print(f"[voice] Could not set the voice or speed: {exc}")

    def wait(self):
        """Block until every queued line has been spoken."""
        if not self.silent:
            self.q.join()

    def set_style(self, rate, voice_name="", personality=None):
        self.rate, self.voice_name = rate, voice_name or ""
        if personality is not None:
            self.personality = personality
        if not self.silent:
            self.q.put(("style", self.rate, self.voice_name))

    def say(self, text):
        if not self.allowed():
            print(f"[coach] (not spoken: the round is live) {text}")
            return
        print(f"[coach] {text}" + (" (muted)" if self.muted else ""))
        if not self.silent and not self.muted:
            self.q.put((self.gen, text))

    def set_muted(self, muted):
        self.muted = muted
        print("[voice] Muted." if muted else "[voice] Unmuted.")

    def toggle_mute(self):
        self.muted = not self.muted
        print("[coach] muted" if self.muted else "[coach] on")
        if not self.muted and not self.silent and self.allowed():
            self.q.put((self.gen, "Coach on."))


def voice_names():
    """Return the names of the voices the coach can use."""
    backend, _ = _open_backend()
    return backend.voice_names() if backend else []


def list_voices():
    names = voice_names()
    for name in names:
        print(f"- {name}")
    return names


def start_hotkey(name, callback):
    """Listen for a global hotkey, for example 'f8'. Returns the listener or None."""
    try:
        from pynput import keyboard
    except ImportError:
        print("pynput is not installed. The mute hotkey is off.")
        return None
    target = getattr(keyboard.Key, name.lower(), None) or keyboard.KeyCode.from_char(name)

    def on_press(key):
        if key == target:
            callback()

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    return listener
