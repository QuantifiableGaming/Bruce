"""Instant replay for your stream, through OBS Studio.

"Bruce, replay that" does these steps:
1. OBS saves its replay buffer (the last seconds of your stream) to a file.
2. The coach loads that file into the media source of your replay scene.
3. The coach switches OBS to the replay scene. Your viewers see the replay. Your game does not change.
4. When the replay ends, the coach switches back to the scene you were on.

What you set up in OBS once:
- Tools > WebSocket Server Settings: turn on the server. Note the port and the password.
- Settings > Output > Replay Buffer: turn it on, and set the time (for example 20 seconds).
- A scene for replays (default name "Instant Replay") with a Media Source (default name "Replay").
  You can add your own overlay, transitions and filters to this scene, as in the video guides.

The coach only talks to OBS on your own PC. It never touches the game.
"""
import queue
import threading
import time

MEDIA_ENDED = ("OBS_MEDIA_STATE_ENDED", "OBS_MEDIA_STATE_STOPPED", "OBS_MEDIA_STATE_ERROR")
RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
PLAYING = "OBS_MEDIA_STATE_PLAYING"
START_WAIT_S = 4.0                       # A clip that does not start in this time has a problem.


def _field(resp, *names):
    """Read a response field. obsws-python gives snake_case names. Older versions differ."""
    for n in names:
        if hasattr(resp, n):
            return getattr(resp, n)
    return None


class ObsReplay:
    def __init__(self, cfg, password="", client_factory=None, log=print):
        oc = cfg.get("obs_replay") or {}
        self.enabled = bool(oc.get("enabled"))
        self.host = str(oc.get("host") or "localhost")
        self.port = int(oc.get("port") or 4455)
        self.password = str(password or "")
        self.scene = str(oc.get("scene") or "Instant Replay")
        self.media = str(oc.get("media_source") or "Replay")
        self.when = str(oc.get("when") or "now")             # "now" or "after_round"
        self.max_s = float(oc.get("max_length_s") or 30)
        self.save_wait_s = float(oc.get("save_wait_s") or 10)
        self.factory = client_factory
        self.log = log
        self.jobs = queue.Queue()
        self.held = []          # Saved replay files that wait for the round end ("after_round").
        self.busy = False
        self.worker = None
        self.client = None

    # --- Called by the session ---

    def request(self, round_live):
        """Save a replay now. Play it now, or at the round end when "when" is "after_round"."""
        if not self.enabled:
            return "off"
        later = self.when == "after_round" and round_live
        self._start_worker()
        self.jobs.put(("save_hold" if later else "save_play", None))
        return "later" if later else "now"

    def save_only(self):
        """Save the replay buffer to a file, and do not play it (auto-clip of a highlight)."""
        if not self.enabled:
            return "off"
        self._start_worker()
        self.jobs.put(("save_keep", None))
        return "saved"

    def round_over(self):
        """The round ended: play the replays that wait."""
        if self.enabled and self.held:
            self._start_worker()
            self.jobs.put(("play_held", None))

    def check(self):
        """Connect and check the OBS setup. Returns a list of problems (empty: all good)."""
        try:
            c = self._connect()
        except Exception as exc:
            return [f"Cannot connect to OBS at {self.host}:{self.port} ({exc}). In OBS, turn on "
                    "Tools > WebSocket Server Settings, and check the port and password."]
        problems = []
        try:
            if not _field(c.get_replay_buffer_status(), "output_active", "outputActive"):
                try:
                    c.start_replay_buffer()          # Start it for the player.
                    time.sleep(1.0)
                    if _field(c.get_replay_buffer_status(), "output_active", "outputActive"):
                        self.log("[replay] Started the OBS replay buffer.")
                        return problems + self._check_scene(c)
                except Exception:
                    pass
                problems.append("The OBS replay buffer is off. Turn it on in Settings > Output > Replay Buffer, "
                                "then click Start Replay Buffer.")
        except Exception as exc:
            problems.append(f"The OBS replay buffer is not set up ({exc}). Turn it on in Settings > Output > Replay Buffer.")
        return problems + self._check_scene(c)

    def _check_scene(self, c):
        problems = []
        try:
            names = [s.get("sceneName") for s in _field(c.get_scene_list(), "scenes") or []]
            if self.scene not in names:
                problems.append(f"OBS has no scene named '{self.scene}'. Make it, or change the scene name in the settings.")
        except Exception as exc:
            problems.append(f"Could not read the OBS scenes ({exc}).")
        try:
            c.get_media_input_status(self.media)
        except Exception:
            problems.append(f"OBS has no media source named '{self.media}'. Add a Media Source with this name to the replay scene.")
        return problems

    # --- Worker thread: OBS calls can take a few seconds. The coach must not wait. ---

    def _start_worker(self):
        if self.worker is None or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()

    def _run(self):
        while True:
            kind, _ = self.jobs.get()
            try:
                if kind == "save_keep":
                    self._save()
                elif kind in ("save_play", "save_hold"):
                    path = self._save()
                    if path and kind == "save_play":
                        self._play(path)
                    elif path:
                        self.held.append(path)
                        self.log("[replay] Saved. It plays at the end of the round.")
                elif kind == "play_held":
                    while self.held:
                        self._play(self.held.pop(0))
            except Exception as exc:
                self.client = None       # Connect again next time.
                self.log(f"[replay] OBS error: {exc}")

    def _connect(self):
        if self.client is None:
            if self.factory:
                self.client = self.factory()
            else:
                import obsws_python as obs

                self.client = obs.ReqClient(host=self.host, port=self.port, password=self.password, timeout=3)
        return self.client

    def _last_path(self, c):
        try:
            return _field(c.get_last_replay_buffer_replay(), "saved_replay_path", "savedReplayPath")
        except Exception:
            return None                  # No replay saved yet in this OBS session.

    def _save(self):
        c = self._connect()
        before = self._last_path(c)
        c.save_replay_buffer()
        end = time.monotonic() + self.save_wait_s
        while time.monotonic() < end:
            time.sleep(0.25)
            path = self._last_path(c)
            if path and path != before:
                self.log(f"[replay] Saved {path}")
                return path
        self.log("[replay] OBS did not save a replay. Is the replay buffer started?")
        return None

    def _play(self, path):
        c = self._connect()
        back = _field(c.get_current_program_scene(), "current_program_scene_name", "scene_name", "currentProgramSceneName")
        # "is_local_file" must be on: with it off, OBS plays the "input" web address and
        # ignores local_file, so the replay scene stays black (17 Nov). The other settings
        # make the clip play once and show nothing at the end.
        c.set_input_settings(self.media, {"is_local_file": True, "local_file": path, "looping": False,
                                          "restart_on_activate": True, "clear_on_media_end": True}, True)
        c.trigger_media_input_action(self.media, RESTART)
        if back != self.scene:
            c.set_current_program_scene(self.scene)
        self.busy = True
        self.log("[replay] Playing on stream.")
        start = time.monotonic()
        time.sleep(1.0)                  # The media source needs a moment to start.
        started = False
        while time.monotonic() - start < self.max_s:
            state = _field(c.get_media_input_status(self.media), "media_state", "mediaState")
            if state == PLAYING:
                started = True
            elif state in MEDIA_ENDED:
                break
            if not started and time.monotonic() - start > START_WAIT_S:
                self.log(f"[replay] OBS did not play the clip (state: {state}). Open the '{self.media}' "
                         f"source in OBS and check that the file plays: {path}")
                break
            time.sleep(0.25)
        self.busy = False
        now = _field(c.get_current_program_scene(), "current_program_scene_name", "scene_name", "currentProgramSceneName")
        if back and back != self.scene and now == self.scene:
            c.set_current_program_scene(back)   # Only when you did not change the scene yourself.
        self.log("[replay] Done. Back to " + str(back) + ".")


def _install_package():
    """obsws-python is optional. Install it the first time instant replay is on."""
    try:
        import obsws_python  # noqa: F401
        return True
    except ImportError:
        pass
    import subprocess
    import sys

    print("[replay] Installing the OBS package (obsws-python). This takes a few seconds...")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "obsws-python"], capture_output=True, text=True)
    if result.returncode != 0:
        print("[replay] Could not install obsws-python. Run: python -m pip install obsws-python")
        return False
    return True


def from_config(cfg):
    oc = cfg.get("obs_replay") or {}
    if not oc.get("enabled") or not _install_package():
        return None
    from .ai import load_secrets

    secrets = load_secrets((cfg.get("ai") or {}).get("secrets_file", "secrets.yaml"))
    return ObsReplay(cfg, password=str(secrets.get("obs_password") or ""))
