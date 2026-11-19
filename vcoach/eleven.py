"""ElevenLabs voices for the coach (optional).

The coach sends each line to ElevenLabs and plays the audio that comes back.
It needs an ElevenLabs API key in secrets.yaml (elevenlabs_api_key) or in the
ELEVENLABS_API_KEY environment variable. It uses plain HTTPS. No extra packages are needed.
The audio plays through the sounddevice package, which the coach already uses for the microphone.

Each spoken line uses ElevenLabs characters. The coach keeps a copy of each line on disk
(cache/voice), so the same line costs characters only one time.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request

API = "https://api.elevenlabs.io/v1"
SAMPLE_RATE = 22050                  # pcm_22050: raw 16-bit audio. All plans can use this format.
DEFAULT_MODEL = "eleven_flash_v2_5"  # The fast, low-cost model.
SECRET = "elevenlabs_api_key"
ENV = "ELEVENLABS_API_KEY"


class ElevenError(Exception):
    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal   # True: the key or the account has a problem. Stop trying for this session.


def api_key(secrets=None):
    secrets = secrets or {}
    return str(secrets.get(SECRET) or os.environ.get(ENV) or "").strip()


def _request(url, key, body=None, timeout=8):
    headers = {"xi-api-key": key, "Accept": "application/json", "User-Agent": "ValorantCoach/1.0"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        fatal = exc.code in (401, 402, 403) or "quota" in detail.lower()
        raise ElevenError(f"ElevenLabs HTTP {exc.code}: {_explain(exc.code, detail)}", fatal) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ElevenError(f"ElevenLabs network problem: {exc}") from None


def _explain(code, detail):
    low = detail.lower()
    if "quota" in low:
        return "Your ElevenLabs characters for this month are used up."
    if code == 401:
        return "The API key is not correct, or it does not have permission for this action."
    if code == 402:
        return "Your ElevenLabs plan does not allow this. " + detail
    return detail


def list_voices(key):
    """Return a list of {id, name, category} for the voices on the account."""
    raw = _request(f"{API}/voices", key)
    voices = json.loads(raw.decode("utf-8")).get("voices", [])
    out = [{"id": v.get("voice_id"), "name": v.get("name") or v.get("voice_id"),
            "category": v.get("category", "")} for v in voices if v.get("voice_id")]
    return sorted(out, key=lambda v: v["name"].lower())


def characters_left(key):
    """Return (used, limit) for this month, or None if the key cannot read it."""
    try:
        data = json.loads(_request(f"{API}/user/subscription", key).decode("utf-8"))
        return int(data.get("character_count", 0)), int(data.get("character_limit", 0))
    except Exception:
        return None


def speed_from_rate(rate):
    """Convert words per minute (about 180 is normal) to the ElevenLabs speed (0.7 to 1.2)."""
    try:
        return round(max(0.7, min(1.2, float(rate) / 180.0)), 2)
    except (TypeError, ValueError):
        return 1.0


class ElevenVoice:
    """Makes speech audio with ElevenLabs and keeps a copy of each line on disk."""

    def __init__(self, cfg, key, cache_dir="cache/voice"):
        cfg = cfg or {}
        self.key = key
        self.model = str(cfg.get("model") or DEFAULT_MODEL)
        self.default_voice = str(cfg.get("voice_id") or "").strip()
        self.voices = {str(k): str(v) for k, v in (cfg.get("voices") or {}).items() if v}
        self.cache_dir = cache_dir if cfg.get("cache", True) else None
        self.timeout = float(cfg.get("timeout_s", 8))

    def voice_for(self, personality):
        return self.voices.get(str(personality or ""), "") or self.default_voice

    def audio(self, text, voice_id, rate=180):
        """Return 16-bit mono audio bytes at SAMPLE_RATE."""
        if not voice_id:
            raise ElevenError("No ElevenLabs voice is chosen. Choose one in the Control Panel.", fatal=True)
        speed = speed_from_rate(rate)
        path = None
        if self.cache_dir:
            tag = hashlib.sha1(f"{voice_id}|{self.model}|{speed}|{text}".encode("utf-8")).hexdigest()[:20]
            path = os.path.join(self.cache_dir, f"{tag}.pcm")
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return f.read()
        body = {"text": text, "model_id": self.model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed}}
        data = _request(f"{API}/text-to-speech/{voice_id}?output_format=pcm_{SAMPLE_RATE}",
                        self.key, body, timeout=self.timeout)
        if len(data) < 200:
            raise ElevenError("ElevenLabs sent no audio.")
        if path:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
            except OSError:
                pass
        return data


def play_pcm(data, sample_rate=SAMPLE_RATE, cut=lambda: False):
    """Play 16-bit mono audio and wait until it ends, or until cut() is True."""
    import time

    import numpy as np
    import sounddevice as sd

    samples = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16)
    sd.play(samples, sample_rate)
    end = time.monotonic() + len(samples) / float(sample_rate) + 0.3
    while time.monotonic() < end:
        if cut():
            sd.stop()
            return
        time.sleep(0.05)
    sd.wait()


def from_config(cfg, quiet=False):
    """Return an ElevenVoice when ElevenLabs is turned on and ready, else None."""
    el = (cfg or {}).get("elevenlabs") or {}
    if not el.get("enabled"):
        return None
    from .ai import load_secrets

    key = api_key(load_secrets((cfg.get("ai") or {}).get("secrets_file", "secrets.yaml")))
    if not key:
        if not quiet:
            print("[voice] ElevenLabs is on, but there is no API key. The coach uses the Windows voice.")
        return None
    voice = ElevenVoice(el, key)
    if not voice.default_voice and not voice.voices:
        if not quiet:
            print("[voice] ElevenLabs is on, but no voice is chosen. The coach uses the Windows voice.")
        return None
    return voice
