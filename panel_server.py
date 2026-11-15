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
    ]),
    ("Mistake checks", [
        (["checks", "crosshair", "tolerance_px_at_1080"], "int", "Crosshair tolerance (px)", "How far from head height still counts as good aim.", 5, 60),
        (["checks", "moving_shots", "motion_threshold"], "float", "Movement threshold", "Lower values flag smaller movements as moving shots.", 0.1, 5.0),
        (["checks", "long_spray", "max_shots"], "int", "Longest good burst (shots)", "Bursts longer than this count as a long spray. Raise it for SMGs.", 3, 25),
        (["checks", "reload", "window_s"], "float", "Reload danger window (s)", "Damage this long before a reload means you reloaded in a fight.", 0.5, 6.0),
    ]),
    ("Voice commands", [
        (["voice_control", "enabled"], "bool", "Voice commands on", "Listen for the wake word during matches.", None, None),
        (["voice_control", "model_path"], "choice", "Voice model", "The large model hears words better. After you change it, click Download next to Voice commands on the Home page.",
         [["models/vosk-model-small-en-us-0.15", "Small (40 MB, fast)"],
          ["models/vosk-model-en-us-0.22-lgraph", "Large (128 MB, more accurate)"]], None),
        (["voice_control", "wake_word"], "str", "Wake word", "The word you say first, for example bruce.", None, None),
        (["voice_control", "answer_window_s"], "int", "Answer window after a round (s)", "Coaching questions get answers for this long after a round ends.", 10, 60),
    ]),
    ("AI coach", [
        (["ai", "timeout_s"], "float", "AI wait time (s)", "Longer than this, and the coach uses the scripted line.", 1.0, 10.0),
        (["ai", "max_words"], "int", "Longest AI line (words)", "Shorter lines finish before the buy phase ends.", 10, 40),
        (["ai", "claude_model"], "str", "Claude model", "Change it only if Anthropic renames its models.", None, None),
        (["ai", "chatgpt_model"], "str", "ChatGPT model", "Change it only if OpenAI renames its models.", None, None),
    ]),
    ("Screen capture", [
        (["monitor"], "int", "Monitor number", "1 is your main monitor.", 1, 6),
        (["fps"], "int", "Capture rate (frames per second)", "Lower values use less CPU.", 10, 60),
        (["tesseract_cmd"], "str", "Tesseract path (optional)", "Leave empty. Set it only if Tesseract is in an unusual folder.", None, None),
    ]),
]


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

    def info(self, since=0):
        return {"id": self.id, "args": self.args, "running": self.running, "code": self.code,
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

    def status(self):
        from .ai import DEFAULTS, AIClient, load_secrets
        from .hud import find_tesseract
        from .personality import available
        from .vosk_setup import model_ready

        cfg = self.cfg()
        missing = []
        for mod in ("cv2", "numpy", "mss", "pytesseract", "pyttsx3", "pynput", "ruamel.yaml", "vosk", "sounddevice"):
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
            "voice_model": model_ready(model_path),
            "voice_enabled": bool(cfg.get("voice_control", {}).get("enabled")),
            "wake_word": cfg.get("voice_control", {}).get("wake_word", "bruce"),
            "mic_device": cfg.get("voice_control", {}).get("mic_device"),
            "personality": cfg.get("coach", {}).get("personality", "calm"),
            "personalities": [{"key": k, "name": n, "description": d} for k, n, d in available(os.path.join(self.root, "personalities"))],
            "voice_name": cfg.get("coach", {}).get("voice_name") or "",
            "voice_rate": cfg.get("coach", {}).get("voice_rate"),
            "enemy_color": cfg.get("enemy_highlight", {}).get("color", "red"),
            "gun_side": cfg.get("gun_side", "right"),
            "screenshot_taken": regions_set,
            "ai_provider": ai_cfg.get("provider", "off"),
            "ai_ready": client.enabled,
            "ai_reason": client.reason,
            "ai_keys": keys,
            "ai_models": {"claude": ai_cfg.get("claude_model"), "chatgpt": ai_cfg.get("chatgpt_model")},
            "ai_use_for": list(ai_cfg.get("use_for") or []),
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

    def stop_task(self, task, wait=8.0):
        """Ask the task to stop cleanly (so the coach saves its report), then force it."""
        if not task or not task.running:
            return
        open(self.stop_file, "w").close()
        end = time.time() + wait
        while task.running and time.time() < end:
            time.sleep(0.2)
        if task.running:
            task.proc.terminate()
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
            keys = [k for k, _, _ in available(os.path.join(self.root, "personalities"))] + ["random"]
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
            if provider not in ("off", "claude", "chatgpt"):
                raise ValueError("Unknown AI provider.")
            key = str(body.get("key", "") or "").strip()
            if key:
                if provider == "off":
                    raise ValueError("Choose Claude or ChatGPT for this key.")
                secrets = self.secrets_path()
                if not os.path.exists(secrets):
                    import shutil

                    shutil.copy(os.path.join(self.root, "secrets.example.yaml"), secrets)
                from .util import set_config_value

                set_config_value(secrets, [DEFAULTS[provider]["secret"]], key)
            self.set_value(["ai", "provider"], provider)
            if "use_for" in body:
                valid = [u for u in body["use_for"] if u in ("tips", "mental", "answers", "match_end")]
                self.set_value(["ai", "use_for"], valid)
            return h._send(200, {"ok": True})
        if path == "/api/run":
            # Only these commands can run from the page.
            kind = body.get("kind")
            personality = body.get("personality")
            commands = {
                "install_tesseract": ["install-tesseract"],
                "install_voice_model": ["install-voice-model"],
                "install_packages": None,
                "voices": ["voices", "--json"],
                "mics": ["mics", "--json"],
                "preview": ["preview"] + (["--personality", personality] if personality else []),
                "ai_test": ["ai-test"] + (["--personality", personality] if personality else []),
            }
            if kind not in commands:
                raise ValueError("Unknown command.")
            if kind == "install_packages":
                task = self._pip()
            else:
                task = self.run(commands[kind])
            return h._send(200, task.info())
        if path == "/api/coach/start":
            st = self.status()
            if st["packages_missing"] or not st["tesseract"]:
                raise ValueError("Some parts are missing. Install them on the Home page first.")
            task = self.start_coach(body.get("personality") or None, bool(body.get("no_mic")),
                                    bool(body.get("record")))
            return h._send(200, task.info())
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
