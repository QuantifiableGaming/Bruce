"""Coach Control Panel: a local website that replaces the black setup window.

It extends the region editor server. It runs on 127.0.0.1 only, so no other computer
can connect. Long jobs (installs, tests, the coach itself) run as separate Python
processes, and the page reads their output while they run.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from .editor_server import EditorServer

# Settings the panel can change: path, type, label, help, and limits or choices.
SETTINGS = [
    ("Coaching", [
        (["coach", "loss_streak_reset"], "int", "Losses before a reset message", "Lost rounds in a row before the coach helps you reset.", 2, 6),
        (["coach", "tip_cooldown_rounds"], "int", "Rounds between repeat tips", "How long before the same tip can play again.", 0, 6),
        (["coach", "mute_hotkey"], "str", "Mute hotkey", "Key that mutes the coach during a match, for example f8.", None, None),
        (["coach", "screenshot_hotkey"], "str", "Screenshot hotkey", "Key that saves the screen during a match, for the Screen regions page.", None, None),
        (["coach", "allow_18_plus"], "bool", "18+ personality (Degen)", "Adults only. Shows the Degen coach, which swears and makes crude jokes. NOT safe for streams: Twitch and YouTube can act on it. Random, Mix and chat votes never pick it.", None, None),
    ]),
    ("Mistake checks", [
        (["checks", "crosshair", "tolerance_px_at_1080"], "int", "Crosshair tolerance (px)", "How far from head height still counts as good aim.", 5, 60),
        (["checks", "moving_shots", "motion_threshold"], "float", "Movement threshold", "Lower values flag smaller movements as moving shots.", 0.1, 5.0),
        (["checks", "long_spray", "max_shots"], "int", "Longest good burst (shots)", "Bursts longer than this count as a long spray. Raise it for SMGs.", 3, 25),
        (["checks", "reload", "window_s"], "float", "Reload danger window (s)", "Damage this long before a reload means you reloaded in a fight.", 0.5, 6.0),
    ]),
    ("Voice commands", [
        (["voice_control", "enabled"], "bool", "Voice commands on", "Listen for the wake word during matches.", None, None),
        (["voice_control", "follow_up_s"], "int", "Follow-up time (seconds)", "After an AI answer, the next question in this time needs no \"Bruce\". 0: off.", 0, 20),
        (["ai", "small_talk"], "bool", "Chat mode (\"Hey Bruce\")", "\"Hey Bruce, ...\" starts a chat about anything between rounds. \"Bruce\" alone gives coaching.", None, None),
        (["voice_control", "model_path"], "choice", "Voice model", "The large model hears words better. After you change it, click Download next to Voice commands on the Home page.",
         [["models/vosk-model-small-en-us-0.15", "Small (40 MB, fast)"],
          ["models/vosk-model-en-us-0.22-lgraph", "Large (128 MB, more accurate)"]], None),
        (["voice_control", "wake_word"], "str", "Wake word", "The word you say first, for example bruce.", None, None),
        (["voice_control", "answer_window_s"], "int", "Answer window after a round (s)", "Coaching questions get answers for this long after a round ends.", 10, 60),
    ]),
    ("Content creator", [
        (["creator", "name"], "str", "Your name or channel name", "The coach says it in \"Bruce, say hi\" and \"Bruce, sign off\". Leave it empty to leave the name out.", None, None),
        (["creator", "clip_hotkey"], "str", "Recorder clip hotkey", "\"Bruce, mark that\" presses this key to save a clip, for example alt+f10 (NVIDIA) or win+alt+g (Xbox Game Bar). Leave it empty to only note the time in the report.", None, None),
        (["creator", "auto_clip"], "bool", "Auto-clip highlights", "An ACE, TEAM ACE or CLUTCH banner saves a clip by itself: a line in Clips to post, and a press of the clip hotkey.", None, None),
        (["creator", "auto_clip_obs"], "choice", "Auto-clip with OBS", "When OBS instant replay is on: save the replay file, play it on stream, or do nothing.",
         [["save", "Save the file"], ["replay", "Play it on stream"], ["off", "Do nothing"]], None),
        (["challenge", "shots"], "int", "Range challenge stops", "\"Bruce, start the challenge\" scores this many stops.", 5, 100),
        (["creator", "confirm_mark"], "bool", "Say \"Marked\" after a mark", "The coach confirms each \"mark that\". Turn it off to keep your clips quiet.", None, None),
    ]),
    ("Movement keys", [
        (["keys", "enabled"], "bool", "Read movement keys", "Used for moving shots and counter-strafes. The coach only notes when these keys go down and up.", None, None),
        (["keys", "forward"], "str", "Forward key", "One key, for example w.", None, None),
        (["keys", "left"], "str", "Left key", "One key, for example a.", None, None),
        (["keys", "back"], "str", "Back key", "One key, for example s.", None, None),
        (["keys", "right"], "str", "Right key", "One key, for example d.", None, None),
        (["keys", "fire"], "choice", "Fire button", "The button you shoot with.",
         [["mouse_left", "Left mouse button"], ["mouse_right", "Right mouse button"]], None),
    ]),
    ("Twitch chat", [
        (["twitch", "enabled"], "bool", "Read Twitch chat", "The coach reads your chat. It needs no account to read.", None, None),
        (["twitch", "channel"], "str", "Channel", "Your Twitch channel name.", None, None),
        (["twitch", "personality_votes"], "bool", "Personality votes", "Viewers type !vote savage. At each round end, the top vote takes over.", None, None),
        (["twitch", "stats_command"], "bool", "!coach command", "Answers !coach in chat with your session numbers. Needs the account and token below.", None, None),
        (["twitch", "bot_name"], "str", "Chat account", "The account that answers in chat. It can be your own.", None, None),
        (["secret", "twitch_oauth_token"], "secret", "Chat token", "An OAuth token for that account with chat:read and chat:edit. Saved in secrets.yaml.", None, None),
    ]),
    ("Stream instant replay (OBS)", [
        (["obs_replay", "enabled"], "bool", "Instant replay on", "\"Bruce, replay that\" saves the OBS replay buffer and shows it on your stream. Your own screen does not change.", None, None),
        (["obs_replay", "port"], "int", "OBS WebSocket port", "OBS: Tools > WebSocket Server Settings. The default is 4455.", 1, 65535),
        (["secret", "obs_password"], "secret", "OBS WebSocket password", "From the same OBS window (Show Connect Info). It is saved in secrets.yaml, not in config.yaml.", None, None),
        (["obs_replay", "scene"], "str", "Replay scene", "The OBS scene that shows the replay. Put your replay overlay in this scene.", None, None),
        (["obs_replay", "media_source"], "str", "Replay media source", "A Media Source in the replay scene. The coach loads each saved replay into it.", None, None),
        (["obs_replay", "when"], "choice", "When to play", "Play at once, or save at once and play when the round ends.",
         [["now", "At once"], ["after_round", "At the end of the round"]], None),
        (["obs_replay", "max_length_s"], "int", "Longest replay (seconds)", "The coach switches back to your scene after this time.", 5, 120),
    ]),
    ("AI coach", [
        (["ai", "timeout_s"], "float", "AI wait time (s)", "Longer than this, and the coach uses the scripted line.", 1.0, 10.0),
        (["ai", "max_words"], "int", "Longest AI line (words)", "Shorter lines finish before the buy phase ends.", 10, 40),
        (["ai", "post_match_analysis"], "bool", "Written match review", "After each match, the AI writes a review at the end of the report. One AI request for each match.", None, None),
        (["ai", "analysis_delay_s"], "int", "Review wait (s)", "The coach waits this long after the match, so the review can use the scoreboard numbers.", 0, 300),
        (["ai", "claude_model"], "str", "Claude model", "Change it only if Anthropic renames its models.", None, None),
        (["ai", "chatgpt_model"], "str", "ChatGPT model", "Change it only if OpenAI renames its models.", None, None),
        (["ai", "glm_model"], "str", "Free model (OpenRouter)", "A model ID from openrouter.ai that ends in :free.", None, None),
        (["ai", "groq_model"], "str", "Groq model", "A model on Groq's free plan, for example openai/gpt-oss-120b.", None, None),
        (["ai", "custom_base_url"], "str", "Custom AI address", "Up to and with /v1, for example http://localhost:11434/v1 (Ollama).", None, None),
        (["ai", "custom_model"], "str", "Custom AI model", "The model name that the service uses, for example llama3.2.", None, None),
    ]),
    ("Screen capture", [
        (["monitor"], "int", "Monitor number", "1 is your main monitor.", 1, 6),
        (["fps"], "int", "Capture rate (frames per second)", "Lower values use less CPU.", 10, 60),
        (["tesseract_cmd"], "str", "Tesseract path (optional)", "Leave empty. Set it only if Tesseract is in an unusual folder.", None, None),
    ]),
]


HOTKEY_NAMES = {"alt", "ctrl", "control", "shift", "win", "windows", "cmd", "home", "end", "insert", "delete",
                "page_up", "page_down", "print_screen", "pause", "scroll_lock", "space", "tab", "enter", "esc"}


def _known_key(name):
    """True for a key name that the coach can press (the same names as session.press_hotkey)."""
    if name in HOTKEY_NAMES or len(name) == 1:
        return True
    return name[:1] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 20


def _get(data, path):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


class Task:
    """A Python command that runs in the background. The panel reads its output lines."""

    def __init__(self, args, root, env=None):
        self.id = uuid.uuid4().hex[:10]
        self.args = args
        self.lines = []
        self.code = None
        self.started = time.time()
        full_env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", **(env or {}))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [sys.executable, "main.py"] + args, cwd=root, env=full_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            if "\r" in line:
                line = line.split("\r")[-1]
            if line:
                self.lines.append(line)
                del self.lines[:-400]
        self.code = self.proc.wait()

    @property
    def running(self):
        return self.code is None

    stopped_by_user = False

    def info(self, since=0):
        return {"id": self.id, "args": self.args, "running": self.running, "code": self.code,
                "stopped_by_user": self.stopped_by_user,
                "lines": self.lines[since:], "count": len(self.lines)}


class PanelServer(EditorServer):
    def __init__(self, root, config_path="config.yaml", port=8765):
        super().__init__(root, config_path=config_path, port=port)
        self.pages = {"/": os.path.join(root, "panel.html"),
                      "/panel.html": os.path.join(root, "panel.html"),
                      "/editor": self.html_path, "/editor.html": self.html_path}
        self.tasks = {}
        self.coach = None
        self.listen = None
        self.stop_file = os.path.join(root, ".coach_stop")
        self.record_next = False   # Record the next coach session (set on the Settings page).

    # ---------- helpers ----------
    def cfg(self):
        from .util import load_yaml

        return load_yaml(self.config_path)

    def set_value(self, path, value):
        from .util import set_config_value

        set_config_value(self.config_path, path, value)

    def run(self, args, env=None):
        task = Task(args, self.root, env)
        self.tasks[task.id] = task
        return task

    def secrets_path(self):
        return os.path.join(self.root, str(self.cfg().get("ai", {}).get("secrets_file", "secrets.yaml")))

    def _eleven_status(self, cfg, secrets):
        from .eleven import api_key

        el = cfg.get("elevenlabs") or {}
        return {"enabled": bool(el.get("enabled")), "key_set": bool(api_key(secrets)),
                "voice_id": str(el.get("voice_id") or ""), "voices": dict(el.get("voices") or {})}

    def _save_secret(self, name, value):
        secrets = self.secrets_path()
        if not os.path.exists(secrets):
            import shutil

            shutil.copy(os.path.join(self.root, "secrets.example.yaml"), secrets)
        from .util import set_config_value

        set_config_value(secrets, [name], value)

    def status(self):
        from .ai import DEFAULTS, AIClient, load_secrets
        from .hud import find_tesseract, text_reader_name
        from .personality import available
        from .vosk_setup import model_ready

        cfg = self.cfg()
        missing = []
        for mod in ("cv2", "numpy", "mss", "pyttsx3", "pynput", "ruamel.yaml", "vosk", "sounddevice", "winrt.windows.media.ocr"):
            try:
                __import__(mod)
            except Exception:
                missing.append(mod)
        model_path = os.path.join(self.root, cfg.get("voice_control", {}).get("model_path", ""))
        ai_cfg = cfg.get("ai", {}) or {}
        secrets = load_secrets(self.secrets_path())
        client = AIClient(ai_cfg, secrets)
        keys = {p: bool((secrets.get(d["secret"]) or os.environ.get(d["env"]) or "").strip())
                for p, d in DEFAULTS.items()}
        regions_set = os.path.exists(os.path.join(self.root, "calibration_raw.png"))
        return {
            "python": sys.version.split()[0],
            "python_ok": sys.version_info >= (3, 11),
            "packages_missing": missing,
            "tesseract": find_tesseract(str(cfg.get("tesseract_cmd") or "")),
            "text_reader": text_reader_name(cfg),
            "voice_model": model_ready(model_path),
            "voice_enabled": bool(cfg.get("voice_control", {}).get("enabled")),
            "wake_word": cfg.get("voice_control", {}).get("wake_word", "bruce"),
            "mic_device": cfg.get("voice_control", {}).get("mic_device"),
            "personality": cfg.get("coach", {}).get("personality", "calm"),
            "personalities": [{"key": k, "name": n, "description": d} for k, n, d in available(
                os.path.join(self.root, "personalities"), include_adult=bool(cfg.get("coach", {}).get("allow_18_plus")))],
            "voice_name": cfg.get("coach", {}).get("voice_name") or "",
            "voice_rate": cfg.get("coach", {}).get("voice_rate"),
            "enemy_color": cfg.get("enemy_highlight", {}).get("color", "red"),
            "gun_side": cfg.get("gun_side", "right"),
            "screenshot_taken": regions_set,
            "ai_provider": ai_cfg.get("provider", "off"),
            "ai_ready": client.enabled,
            "ai_reason": client.reason,
            "ai_keys": keys,
            "self_test_available": os.path.isfile(os.path.join(self.root, "tests", "test_logic.py")),
            "ai_models": {p: ai_cfg.get(f"{p}_model") or d["model"] for p, d in DEFAULTS.items()},
            "ai_custom_url": ai_cfg.get("custom_base_url") or DEFAULTS["custom"]["base_url"],
            "ai_review_only": sorted(p for p, d in DEFAULTS.items() if d.get("review_only")),
            "ai_use_for": (list(ai_cfg["use_for"]) if ai_cfg.get("use_for") is not None
                           else ["tips", "mental", "answers", "match_end"])
                          + (["review"] if ai_cfg.get("post_match_analysis", True) else []),
            "record_next": self.record_next,
            "eleven": self._eleven_status(cfg, secrets),
            "coach_running": bool(self.coach and self.coach.running),
            "listen_running": bool(self.listen and self.listen.running),
        }

    def settings(self):
        cfg = self.cfg()
        out = []
        for group, items in SETTINGS:
            rows = []
            for path, kind, label, help_text, lo, hi in items:
                row = {"path": path, "type": kind, "label": label, "help": help_text,
                       "min": lo, "max": hi, "value": _get(cfg, path)}
                if kind == "secret":
                    from .ai import load_secrets

                    row["value"] = ""                      # Never send a password to the page.
                    row["is_set"] = bool(load_secrets(self.secrets_path()).get(path[-1]))
                if kind == "choice":
                    row.update(min=None, choices=lo)
                rows.append(row)
            out.append({"group": group, "items": rows})
        return out

    def save_settings(self, changes):
        allowed = {tuple(p): (k, lo, hi) for _, items in SETTINGS for p, k, _l, _h, lo, hi in items}
        for change in changes:
            path = tuple(change["path"])
            if path not in allowed:
                raise ValueError(f"This setting cannot be changed here: {'.'.join(path)}")
            kind, lo, hi = allowed[path]
            value = change["value"]
            if kind == "secret":
                if str(value).strip():
                    self._save_secret(path[-1], str(value).strip())
                continue
            if kind == "choice":
                if value not in [c[0] for c in lo]:
                    raise ValueError(f"Unknown choice for {'.'.join(path)}.")
                self.set_value(list(path), value)
                continue
            if kind == "int":
                value = int(value)
            elif kind == "float":
                value = round(float(value), 3)
            elif kind == "bool":
                value = bool(value)
            else:
                value = str(value).strip()
                if path[-1] in ("mute_hotkey", "screenshot_hotkey", "wake_word"):
                    value = value.lower()
                    if not value:
                        raise ValueError("This setting cannot be empty.")
                if path[0] == "keys":
                    value = value.lower()
                    if len(value) != 1:
                        raise ValueError(f"The {path[-1]} key must be one key, for example w.")
                if path[-1] == "clip_hotkey" and value:
                    value = value.lower().replace(" ", "")
                    bad = [k for k in value.split("+") if not _known_key(k)]
                    if bad:
                        raise ValueError(f"Recorder clip hotkey: unknown key '{bad[0]}'. Use names such as alt+f10 or win+alt+g.")
            if lo is not None and not (lo <= value <= hi):
                raise ValueError(f"{'.'.join(path)} must be between {lo} and {hi}.")
            self.set_value(list(path), value)

    def start_coach(self, personality=None, no_mic=False, record=False):
        if self.coach and self.coach.running:
            return self.coach
        if self.listen and self.listen.running:
            self.stop_task(self.listen)  # The mic test and the coach cannot share the mic well.
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        args = ["live"]
        if personality:
            args += ["--personality", personality]
        if no_mic:
            args.append("--no-mic")
        if record:
            args.append("--record")
        self.coach = self.run(args, env={"COACH_STOP_FILE": self.stop_file})
        return self.coach

    def stop_task(self, task, wait=25.0):
        """Ask the task to stop cleanly (so the coach saves its report), then force it."""
        if not task or not task.running:
            return
        open(self.stop_file, "w").close()
        end = time.time() + wait
        while task.running and time.time() < end:
            time.sleep(0.2)
        if task.running:
            task.lines.append("The coach did not stop in time. The panel closed it.")
            task.proc.terminate()
        task.stopped_by_user = True
        try:
            os.remove(self.stop_file)
        except OSError:
            pass

    def on_shutdown(self):
        for task in (self.coach, self.listen):
            if task and task.running:
                self.stop_task(task, wait=3)

    def reports(self):
        folder = os.path.join(self.root, "reports")
        if not os.path.isdir(folder):
            return []
        names = sorted((n for n in os.listdir(folder) if n.endswith(".md")), reverse=True)
        return [{"name": n, "time": os.path.getmtime(os.path.join(folder, n))} for n in names[:100]]

    # ---------- routes ----------
    def extra_get(self, h, path):
        if path == "/api/status":
            return h._send(200, self.status())
        if path == "/api/settings":
            return h._send(200, self.settings())
        if path.startswith("/api/task/"):
            task = self.tasks.get(path.rsplit("/", 1)[-1])
            if not task:
                return h._send(404, {"error": "Unknown task."})
            since = 0
            if "since=" in h.path:
                since = int(h.path.split("since=")[-1].split("&")[0] or 0)
            return h._send(200, task.info(since))
        if path == "/api/coach":
            return h._send(200, self.coach.info() if self.coach else {"running": False, "lines": [], "count": 0})
        if path == "/api/reports":
            return h._send(200, self.reports())
        if path == "/api/history":
            from . import history

            entries = history.load(os.path.join(self.root, "reports", "history.jsonl"))
            return h._send(200, {"rows": history.table(entries), "count": len(entries)})
        if path.startswith("/api/report/"):
            name = os.path.basename(path.rsplit("/", 1)[-1])
            file = os.path.join(self.root, "reports", name)
            if not name.endswith(".md") or not os.path.isfile(file):
                return h._send(404, {"error": "No such report."})
            with open(file, encoding="utf-8") as fh:
                return h._send(200, {"name": name, "text": fh.read()})
        if path in ("/api/guide", "/api/readme"):
            name = "QUICKSTART.md" if path == "/api/guide" else "README.md"
            with open(os.path.join(self.root, name), encoding="utf-8") as fh:
                return h._send(200, {"text": fh.read()})
        if path == "/icons/coach.ico":
            with open(os.path.join(self.root, "icons", "coach.ico"), "rb") as fh:
                return h._send(200, fh.read(), "image/x-icon")
        return False

    def extra_post(self, h, path, body):
        if path == "/api/settings":
            self.save_settings(body.get("changes", []))
            return h._send(200, {"ok": True, "settings": self.settings()})
        if path == "/api/personality":
            from .personality import available

            name = str(body.get("name", ""))
            allow = bool((self.cfg() or {}).get("coach", {}).get("allow_18_plus"))
            keys = [k for k, _, _ in available(os.path.join(self.root, "personalities"), include_adult=allow)] + ["random", "mix"]
            if name not in keys:
                raise ValueError("Unknown personality.")
            self.set_value(["coach", "personality"], name)
            return h._send(200, {"ok": True})
        if path == "/api/voice":
            name = str(body.get("voice_name", "") or "")
            rate = body.get("voice_rate")
            rate = None if rate in (None, "", 0) else int(rate)
            if rate is not None and not (100 <= rate <= 300):
                raise ValueError("Speech speed must be between 100 and 300.")
            self.set_value(["coach", "voice_name"], name)
            self.set_value(["coach", "voice_rate"], rate)
            return h._send(200, {"ok": True})
        if path == "/api/open_voice_settings":
            if os.name != "nt":
                raise ValueError("This button works on Windows only.")
            os.startfile("ms-settings:speech")  # Settings > Time & language > Speech
            return h._send(200, {"ok": True})
        if path == "/api/mic":
            device = body.get("mic_device")
            self.set_value(["voice_control", "mic_device"], None if device in (None, "", "default") else int(device))
            return h._send(200, {"ok": True})
        if path == "/api/ai":
            from .ai import DEFAULTS

            provider = str(body.get("provider", "off"))
            if provider not in ("off",) + tuple(DEFAULTS):
                raise ValueError("Unknown AI provider.")
            key = str(body.get("key", "") or "").strip()
            if key:
                if provider == "off":
                    raise ValueError("Choose an AI for this key.")
                secrets = self.secrets_path()
                if not os.path.exists(secrets):
                    import shutil

                    shutil.copy(os.path.join(self.root, "secrets.example.yaml"), secrets)
                from .util import set_config_value

                set_config_value(secrets, [DEFAULTS[provider]["secret"]], key)
            if provider == "custom":
                url = str(body.get("custom_base_url") or "").strip().rstrip("/")
                model = str(body.get("custom_model") or "").strip()
                if url:
                    if not url.startswith(("http://", "https://")):
                        raise ValueError("The Custom AI address must start with http:// or https://.")
                    self.set_value(["ai", "custom_base_url"], url)
                if model:
                    self.set_value(["ai", "custom_model"], model)
            self.set_value(["ai", "provider"], provider)
            if "use_for" in body:
                valid = [u for u in body["use_for"] if u in ("tips", "mental", "answers", "match_end")]
                if not DEFAULTS.get(provider, {}).get("review_only"):
                    self.set_value(["ai", "use_for"], valid)   # GLM keeps your spoken choices for the other AIs.
                self.set_value(["ai", "post_match_analysis"], "review" in body["use_for"])
            return h._send(200, {"ok": True})
        if path == "/api/season":
            from . import history

            name = str(body.get("name", "") or "").strip()
            history.add_season(name, os.path.join(self.root, "reports", "history.jsonl"))
            return h._send(200, {"ok": True})
        if path == "/api/elevenlabs/voices":
            from .ai import load_secrets
            from .eleven import api_key, characters_left, list_voices

            key = str(body.get("key", "") or "").strip() or api_key(load_secrets(self.secrets_path()))
            if not key:
                raise ValueError("Paste your ElevenLabs API key first.")
            from .eleven import ElevenError

            try:
                voices = list_voices(key)
            except ElevenError as exc:
                raise ValueError(str(exc)) from None
            return h._send(200, {"ok": True, "voices": voices, "characters": characters_left(key)})
        if path == "/api/elevenlabs":
            from .eleven import SECRET

            key = str(body.get("key", "") or "").strip()
            if key:
                self._save_secret(SECRET, key)
            ids = [str(body.get("voice_id", "") or "")] + [str(v) for v in (body.get("voices") or {}).values()]
            if any(len(i) > 64 or not all(c.isalnum() or c in "-_" for c in i) for i in ids):
                raise ValueError("A voice ID is not valid.")
            self.set_value(["elevenlabs", "enabled"], bool(body.get("enabled")))
            self.set_value(["elevenlabs", "voice_id"], ids[0])
            self.set_value(["elevenlabs", "voices"], {str(k): str(v) for k, v in (body.get("voices") or {}).items() if v})
            return h._send(200, {"ok": True})
        if path == "/api/run":
            # Only these commands can run from the page.
            kind = body.get("kind")
            personality = body.get("personality")
            commands = {
                "install_tesseract": ["install-tesseract"],
                "text_test": ["text-test"],
                "self_test": ["self-test"],
                "install_voice_model": ["install-voice-model"],
                "install_packages": None,
                "voices": ["voices", "--json"],
                "mics": ["mics", "--json"],
                "preview": ["preview"] + (["--personality", personality] if personality else []),
                "ai_test": ["ai-test"] + (["--personality", personality] if personality else []),
            }
            if kind == "analyze":
                name = os.path.basename(str(body.get("report") or ""))
                if not name.endswith(".md"):
                    raise ValueError("Choose a report.")
                commands["analyze"] = ["analyze", name]
            if kind not in commands:
                raise ValueError("Unknown command.")
            if kind == "install_packages":
                task = self._pip()
            else:
                task = self.run(commands[kind])
            return h._send(200, task.info())
        if path == "/api/coach/start":
            st = self.status()
            if st["packages_missing"]:
                raise ValueError("Some parts are missing. Install them on the Home page first.")
            task = self.start_coach(body.get("personality") or None, bool(body.get("no_mic")),
                                    bool(body.get("record")) or self.record_next)
            self.record_next = False   # Recording is for one session only.
            return h._send(200, task.info())
        if path == "/api/record":
            self.record_next = bool(body.get("on"))
            return h._send(200, {"ok": True, "record_next": self.record_next})
        if path == "/api/coach/stop":
            threading.Thread(target=self.stop_task, args=(self.coach,), daemon=True).start()
            return h._send(200, {"ok": True})
        if path == "/api/listen/start":
            if self.coach and self.coach.running:
                raise ValueError("Stop the coach first. The coach is already using the mic.")
            if not (self.listen and self.listen.running):
                if os.path.exists(self.stop_file):
                    os.remove(self.stop_file)
                self.listen = self.run(["listen"], env={"COACH_STOP_FILE": self.stop_file})
            return h._send(200, self.listen.info())
        if path == "/api/listen/stop":
            threading.Thread(target=self.stop_task, args=(self.listen,), daemon=True).start()
            return h._send(200, {"ok": True})
        return False

    def _pip(self):
        """Install missing Python packages with pip."""
        task = Task.__new__(Task)
        task.id = uuid.uuid4().hex[:10]
        task.args = ["pip install -r requirements.txt"]
        task.lines, task.code, task.started = [], None, time.time()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        task.proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
        threading.Thread(target=task._read, daemon=True).start()
        self.tasks[task.id] = task
        return task
