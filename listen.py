"""Offline wake-word listener. Uses Vosk speech recognition and the sounddevice microphone stream."""
import json
import queue
import threading
import time

from .commands import split_wake


class WakeListener(threading.Thread):
    """Listens for the wake word, then passes the rest of the sentence (or the next sentence) on.

    on_text(command_text) runs in this thread. Keep it fast: put the text in a queue.
    """

    def __init__(self, cfg, on_text, on_heard=None):
        super().__init__(daemon=True)
        vc = cfg["voice_control"]
        self.wake_words = [vc["wake_word"]] + list(vc.get("wake_aliases", []))
        self.model_path = vc["model_path"]
        self.device = vc.get("mic_device")
        self.timeout = vc.get("command_timeout_s", 5.0)
        self.on_text = on_text
        self.on_heard = on_heard  # Optional: called with every sentence, for the listen test.
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.error = None

    def run(self):
        try:
            self._run()
        except Exception as exc:  # Any startup failure must not leave the coach waiting.
            self.error = self.error or f"Voice control failed: {exc}"
        finally:
            self.ready.set()

    def _run(self):
        try:
            import sounddevice as sd
            import vosk
        except (ImportError, OSError) as exc:
            self.error = f"Voice control needs vosk and sounddevice ({exc}). Run: pip install vosk sounddevice"
            self.ready.set()
            return
        from .vosk_setup import download_model, model_ready

        if not model_ready(self.model_path) and not download_model(self.model_path):
            self.error = (f"The voice model is missing at '{self.model_path}', and the download did not work. "
                          "Click Download next to Voice commands on the Home page of the Control Panel.")
            self.ready.set()
            return

        vosk.SetLogLevel(-1)
        recognizer = vosk.KaldiRecognizer(vosk.Model(self.model_path), 16000)
        audio = queue.Queue()

        def callback(indata, frames, t, status):
            audio.put(bytes(indata))

        awaiting_until = 0.0
        try:
            with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16",
                                   channels=1, callback=callback, device=self.device):
                self.ready.set()
                while not self.stop_event.is_set():
                    try:
                        data = audio.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if not recognizer.AcceptWaveform(data):
                        continue
                    text = json.loads(recognizer.Result()).get("text", "")
                    if not text:
                        continue
                    if self.on_heard:
                        self.on_heard(text)
                    heard, command = split_wake(text, self.wake_words)
                    if heard:
                        if command:
                            self.on_text(command)
                            awaiting_until = 0.0
                        else:
                            self.on_text("")  # Only the wake word: the coach answers "Yes?"
                            awaiting_until = time.monotonic() + self.timeout
                    elif time.monotonic() < awaiting_until:
                        self.on_text(text)
                        awaiting_until = 0.0
        except Exception as exc:  # Microphone missing or busy.
            self.error = f"Microphone error: {exc}"
            self.ready.set()

    def stop(self):
        self.stop_event.set()


def list_mics():
    import sounddevice as sd

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"{i:3}  {dev['name']}")
