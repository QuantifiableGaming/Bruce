"""Valorant screen coach.

Usage:
  python main.py live                       Coach you while you play.
  python main.py live --personality random  Pick a new coach personality each match.
  python main.py live --personality mix     Pick a new coach personality each round, with no announcement.
  python main.py personalities              List the coach personalities.
  python main.py voices                     List the Windows voices the coach can use.
  python main.py preview --personality mate Hear sample lines from a personality.
  python main.py ai-setup                   Turn on AI lines with Claude or ChatGPT, and test them.
  python main.py ai-test --personality sassy  Show sample AI lines for a personality.
  python main.py install-tesseract          Install Tesseract (an optional word reader).
  python main.py text-test                  Compare the word readers on your recordings. Writes logs/text_test.txt.
  python main.py install-voice-model        Download the Vosk speech model for "Bruce".
  python main.py mics                       List microphones.
  python main.py listen                     Test voice commands ("Bruce, how am I doing?") without the game.
  python main.py replay clip.mp4 --log m.csv  Run the coach on a recorded clip (for tuning).
  python main.py panel                      Open the Coach Control Panel in your browser.
  python main.py editor                     Open only the visual region editor in your browser.
  python main.py calibrate                  Save a screenshot with the HUD regions drawn on it.
  python main.py calibrate --video clip.mp4 --at 42
"""
import argparse
import os
import queue
import sys
import threading
import time

if sys.version_info < (3, 11):
    raise SystemExit("This coach needs Python 3.11 or later. Python 3.14 or 3.15 is recommended.")

try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
    import ruamel.yaml  # noqa: F401
except ModuleNotFoundError as exc:
    raise SystemExit(f"The Python package '{exc.name}' is missing. "
                     f"Run install.bat, or run: {sys.executable} -m pip install -r requirements.txt")

import cv2

from vcoach.capture import ScreenSource, VideoSource
from vcoach.eleven import from_config as eleven_from_config
from vcoach.personality import PersonalityPicker, available
from vcoach.session import Session
from vcoach.util import coach_name, crop_box, load_yaml
from vcoach.vision import EnemyDetector
from vcoach.voice import Voice, list_voices, start_hotkey


def run_self_test():
    """Run the test suite on this PC and print a short result."""
    if not os.path.isfile(os.path.join("tests", "test_logic.py")):
        print("The tests folder is not here (a shared copy leaves it out). Nothing to test.")
        return 1
    try:
        import pytest
    except ImportError:
        print("pytest is not installed. Click Install packages on the Home page, or run: python -m pip install pytest")
        return 1
    import subprocess

    print("Running the self-test. It takes about 30 seconds...\n")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout + proc.stderr
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "self_test.txt"), "w", encoding="utf-8") as f:
        f.write(out)
    lines = out.strip().splitlines()
    fails = [ln for ln in lines if ln.startswith(("FAILED", "ERROR"))]
    for ln in fails[:20]:
        print(ln)
    print(lines[-1] if lines else "No output.")
    code = proc.returncode
    print()
    if code == 0:
        print("SELF-TEST PASSED. The coach's logic works on this PC.")
    else:
        print("SELF-TEST FAILED. Send logs\\self_test.txt to Claude.")
    return int(code)


def capture_fps(cfg, keys_on, recording):
    """Fewer frames when nothing needs every frame.

    Every frame is needed only for the picture motion check (no key reader), the crosshair
    check, and recordings. The ammo and HUD reads need 20 frames a second at most.
    """
    fps = int(cfg["fps"])
    low = int(cfg.get("low_fps", 20))
    if keys_on and not recording and not cfg["checks"]["crosshair"].get("enabled", True):
        return min(fps, low)
    return fps


def banner_every(cfg):
    return max(1, round(cfg["hud_rate_hz"] / cfg["banner_rate_hz"]))


def make_picker(cfg, args):
    choice = args.personality or cfg["coach"].get("personality", "calm")
    return PersonalityPicker(choice, path=args.lines, pool=cfg["coach"].get("random_pool"))


def start_listener(cfg, commands):
    """Start the wake-word listener. Spoken commands go into the commands queue."""
    if not cfg.get("voice_control", {}).get("enabled"):
        return None
    from vcoach.listen import WakeListener

    listener = WakeListener(cfg, on_text=commands.put)
    listener.start()
    listener.ready.wait(timeout=600)  # The first start can download the voice model.
    if listener.error:
        print(f"Voice control is off: {listener.error}")
        return None
    print(f"Voice control on. Say \"{cfg['voice_control']['wake_word'].capitalize()}\", then a command.")
    return listener


def drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def stop_requested():
    """The control panel asks a running coach to stop by creating this file."""
    import os

    path = os.environ.get("COACH_STOP_FILE")
    return bool(path) and os.path.exists(path)


def save_screenshot(frame, voice):
    """Save the current screen for the region editor. Used to capture the round banner."""
    os.makedirs("screenshots", exist_ok=True)
    path = os.path.join("screenshots", time.strftime("shot_%Y%m%d_%H%M%S.png"))
    cv2.imwrite(path, frame)
    cv2.imwrite("calibration_raw.png", frame)  # The region editor shows this picture.
    print(f"[screenshot] Saved {path}. Open Screen regions in the Control Panel to see it.")
    voice.say("Screenshot saved.")


def start_update_check():
    """Look for a newer version on GitHub in the background. It only prints one line."""
    from vcoach import updater

    def run():
        info = updater.check(os.path.dirname(os.path.abspath(__file__)))
        if info["newer"]:
            print(f"[update] Version {info['remote']} is on GitHub. You have {info['local']}. "
                  "Open the Control Panel and press Update now on the Home page.")

    threading.Thread(target=run, daemon=True).start()


def run_live(cfg, args):
    from vcoach.player_log import LogFiles

    # coach_log.txt: what you said, what the coach said, marks and replays. debug_log.txt: everything.
    sys.stdout = LogFiles(sys.stdout, os.path.join("logs", "debug_log.txt"), os.path.join("logs", "coach_log.txt"))
    sys.stderr = sys.stdout   # Errors go to the debug log.
    print(f"Coach started {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if cfg.get("update_check", True):
        start_update_check()
    from vcoach.hud import HudReader, HudWorker

    voice = Voice(silent=args.silent, eleven=None if args.silent else eleven_from_config(cfg))
    start_hotkey(cfg["coach"]["mute_hotkey"], voice.toggle_mute)
    from vcoach.keys import InputLog, start_listener as start_key_listener

    inputs = InputLog(keep_s=10.0)   # 10 s: the death review looks 5 s back from the death.
    key_listeners = start_key_listener(cfg, inputs)
    from vcoach.obs_replay import from_config as obs_from_config

    obs = obs_from_config(cfg)
    if obs:
        problems = obs.check()
        for p in problems:
            print("[replay] " + p)
        if not problems:
            print(f'[replay] OBS is ready. Say "{coach_name(cfg)}, replay that" for an instant replay on stream.')
    from vcoach.twitch_chat import from_config as chat_from_config

    chat = chat_from_config(cfg)
    if chat:
        chat.start()
    session = Session(cfg, make_picker(cfg, args), voice, args.log,
                      banner_log=os.path.join("logs", "banner_reads.txt"),
                      inputs=inputs if key_listeners else None, obs=obs, chat=chat)
    shot_key = str(cfg["coach"].get("screenshot_hotkey") or "f9")
    shot = threading.Event()
    start_hotkey(shot_key, shot.set)
    commands = queue.Queue()
    listener = None if args.no_mic else start_listener(cfg, commands)
    recorder = None
    if args.record:
        from vcoach.recorder import MatchRecorder

        recorder = MatchRecorder(cfg)
    worker = HudWorker(HudReader(cfg), recorder)
    worker.start()
    fps = capture_fps(cfg, bool(key_listeners), bool(recorder) or bool(args.log))
    if fps < cfg["fps"]:
        print(f"[capture] {fps} frames a second: the keys judge your shots, so the coach needs fewer frames.")
    source = ScreenSource(cfg["monitor"], fps)
    print(f"Coach running. Press {cfg['coach']['mute_hotkey'].upper()} to mute. "
          f"Press {shot_key.upper()} to save a screenshot. Press Ctrl+C to stop.")
    next_stop_check = 0.0
    try:
        while True:
            if time.perf_counter() >= next_stop_check:
                next_stop_check = time.perf_counter() + 0.5
                if stop_requested():
                    print("Stop requested by the control panel.")
                    break
            t, frame = source.read()
            if frame is None:
                continue                  # The screen cannot be captured for a moment (see capture.py).
            if shot.is_set():
                shot.clear()
                save_screenshot(frame, voice)
            worker.publish(t, frame)
            session.frame(t, frame)
            for kind, et, value in worker.drain():
                if kind == "hud":
                    session.hud(et, value[0], value[1], None, *value[2:4])
                elif kind == "stat":
                    session.shot_error(et, value)
                else:
                    session.scene(kind, et, value)
            for text in drain(commands):
                session.command(t, text)
            if session.follow_up_due and listener and not voice.busy():
                session.follow_up_due = False
                listener.open_follow_up(session.follow_up_s)
                print(f"[voice] Listening {session.follow_up_s:g} s for a follow-up question (no \"{coach_name(cfg)}\" needed).")
            if chat:
                for user, text in drain(chat.messages):
                    session.chat_message(t, user, text)
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        worker.stop()
        if recorder:
            worker.join(timeout=1.5)
            recorder.close()
        if listener:
            listener.stop()
        for kl in key_listeners:
            kl.stop()
        if chat:
            chat.stop()
        session.close()
        print("Coach stopped.")


def run_replay(cfg, args):
    from vcoach.hud import HudReader

    voice = Voice(silent=not args.speak)
    session = Session(cfg, make_picker(cfg, args), voice, args.log)
    reader = HudReader(cfg)
    source = VideoSource(args.video)
    hud_every = max(1, round(source.fps / cfg["hud_rate_hz"]))
    b_every = banner_every(cfg)
    from types import SimpleNamespace

    from vcoach.hud import read_scene

    scene_state = SimpleNamespace(last_score_t=None)
    i = hud_reads = 0
    started = time.perf_counter()
    while True:
        t, frame = source.read()
        if frame is None:
            break
        session.frame(t, frame)
        session.shot_error(t, reader.shooting_error(frame))   # Every frame, as in the live coach.
        if i % hud_every == 0:
            ammo, health = reader.ammo_health(frame)
            session.hud(t, ammo, health, None, reader.spectating(frame), None)
            if hud_reads % b_every == 0:
                for kind, et, value in read_scene(reader, t, frame, scene_state):
                    session.scene(kind, et, value)
            hud_reads += 1
        i += 1
    session.close()
    print(f"Processed {i} frames in {time.perf_counter() - started:.1f} s.")


def run_listen(cfg, args):
    """Voice command test without the game: prints what the recognizer hears and answers."""
    voice = Voice(silent=args.silent, eleven=None if args.silent else eleven_from_config(cfg))
    session = Session(cfg, make_picker(cfg, args), voice)
    commands = queue.Queue()
    from vcoach.listen import WakeListener

    listener = WakeListener(cfg, on_text=commands.put, on_heard=lambda t: print(f"[mic] {t}"))
    listener.start()
    listener.ready.wait(timeout=600)  # The first start can download the voice model.
    if listener.error:
        raise SystemExit(listener.error)
    print(f"Listening. Say \"{cfg['voice_control']['wake_word'].capitalize()}, how am I doing?\" Press Ctrl+C to stop.")
    try:
        while not stop_requested():
            try:
                session.command(time.perf_counter(), commands.get(timeout=0.5))
            except queue.Empty:
                pass
            if session.follow_up_due and not voice.busy():
                session.follow_up_due = False
                listener.open_follow_up(session.follow_up_s)
                print(f"Listening {session.follow_up_s:g} s for a follow-up (no wake word needed).")
    except KeyboardInterrupt:
        pass
    listener.stop()
    print("Listening stopped.")


def run_preview(cfg, args):
    import random

    from vcoach.personality import load

    choice = args.personality or cfg["coach"].get("personality", "calm")
    if choice in ("random", "mix"):
        raise SystemExit("Preview one personality at a time, for example: --personality hype")
    lines = load(choice)
    v = lines.get("voice", {})
    voice = Voice(cfg["coach"].get("voice_rate") or v.get("rate", 180), silent=args.silent,
                  voice_name=cfg["coach"].get("voice_name") or v.get("name_contains", ""),
                  eleven=None if args.silent else eleven_from_config(cfg), personality=choice)
    rng = random.Random()
    issue = rng.choice(list(lines["tips"]))
    print(f"Personality: {lines['name']} - {lines.get('description', '')}")
    voice.say(rng.choice(lines["reset"]))
    voice.say(rng.choice(lines["credit"]))
    voice.say(rng.choice(lines["focus"]).format(focus=lines["focus_names"][issue]))
    voice.say(rng.choice(lines["tips"][issue]))
    voice.say(lines["match_good"]["crosshair"].format(good=14, total=20))
    voice.wait()


def run_editor(args, panel=False):
    import os
    import webbrowser

    from vcoach.editor_server import EditorServer
    from vcoach.panel_server import PanelServer

    root = os.path.dirname(os.path.abspath(__file__))
    if panel:
        server = PanelServer(root, config_path=args.config, port=args.port)
    else:
        server = EditorServer(root, config_path=args.config, port=args.port)
    url = server.start()
    print(f"{'Coach Control Panel' if panel else 'Region editor'}: {url}")
    print("Your browser opens the page. Keep this window open while you use it.")
    print("To close it, click 'Close panel' on the page, or press Ctrl+C here.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve()
    except KeyboardInterrupt:
        server.on_shutdown()
    print("Closed.")


def _ai_try(cfg, personality, count=4, show_errors=True, speak=False):
    """Ask the AI for sample lines. Returns True when at least one worked.
    With speak=True the coach also says each line in this personality's voice."""
    from vcoach.ai import AIClient, clean_line, load_secrets, sample_prompts, system_prompt
    from vcoach.personality import load

    ai_cfg = cfg.get("ai", {})
    client = AIClient(ai_cfg, load_secrets(ai_cfg.get("secrets_file", "secrets.yaml")))
    if client.provider == "glm":
        print("GLM writes only the match review. The coach does not speak GLM lines in a match.")
        print("This test only checks that GLM answers.\n")
    if not client.enabled:
        print(f"AI is not ready: {client.reason}")
        return False
    lines = load(personality)
    print(f"Testing {client.provider} ({client.model}) with the {lines['name']} personality...\n")
    voice = None
    if speak and client.provider != "glm":
        try:
            v = lines.get("voice", {})
            voice = Voice(cfg["coach"].get("voice_rate") or v.get("rate", 180),
                          voice_name=cfg["coach"].get("voice_name") or v.get("name_contains", ""),
                          eleven=eleven_from_config(cfg), personality=personality)
        except Exception as exc:      # The text test still works without sound.
            print(f"(No sound: {exc})")
    ok = False
    for kind, user in sample_prompts(lines)[:count]:
        start = time.perf_counter()
        try:
            text = clean_line(client.complete(system_prompt(lines, int(ai_cfg.get("max_words", 25))), user,
                                              timeout=max(10, client.timeout)), int(ai_cfg.get("max_words", 25)))
            ms = (time.perf_counter() - start) * 1000
            print(f"  {kind:7} ({ms:5.0f} ms)  {text}")
            ok = ok or bool(text)
            if voice and text:
                voice.say(text)
        except Exception as exc:
            print(f"  {kind:7} FAILED: {exc}")
            if show_errors:
                msg = str(exc)
                if "1010" in msg:
                    print("\n  The AI service's firewall blocked the request (error 1010). Update the coach to the newest version.")
                elif "401" in msg or "403" in msg:
                    print("\n  The API key is wrong or has no access. Run the AI setup again with a new key.")
                elif "404" in msg or "model" in msg.lower():
                    print("\n  The model name may be wrong. Change the model on the Settings page (AI coach card).")
                elif "429" in msg:
                    print("\n  Rate limit or no credit. Check the billing page of your AI account.")
                elif "Network" in msg:
                    print("\n  No connection to the AI service. Check your internet connection.")
            break
    if voice:
        voice.wait()
    timeout = float(ai_cfg.get("timeout_s", 4))
    if ok:
        print(f"\nThe coach waits up to {timeout:g} s for the AI, then uses the scripted line.")
    return ok


def run_ai_setup(cfg, args):
    import getpass
    import os
    import shutil

    from vcoach.util import set_config_value

    print("AI COACH SETUP\n")
    print("The coach can use an AI to write its lines, so they sound less scripted.")
    print("The AI only gets the score, mistake counts, recent lines, and your spoken questions.")
    print("It never gets screenshots. You pay the AI company for use (a few cents for many matches).\n")
    print("  1. Claude (Anthropic)  - key from console.anthropic.com")
    print("  2. ChatGPT (OpenAI)    - key from platform.openai.com")
    print("  3. GLM 5.3 Flash, FREE - match review only. Free key from openrouter.ai/keys")
    print("  4. Groq, FREE          - fast enough for live lines. Free key from console.groq.com/keys")
    print("  5. Custom AI           - Ollama on this PC, Gemini, Mistral... (OpenAI chat format)")
    print("  6. Turn the AI off (scripted lines only)\n")
    choice = input("Choose 1 to 6: ").strip()
    provider = {"1": "claude", "2": "chatgpt", "3": "glm", "4": "groq", "5": "custom", "6": "off"}.get(choice)
    if provider is None:
        raise SystemExit("No change made.")
    if provider == "off":
        set_config_value(args.config, ["ai", "provider"], "off")
        raise SystemExit("AI is off. The coach uses scripted lines.")

    secrets_path = cfg.get("ai", {}).get("secrets_file", "secrets.yaml")
    if not os.path.exists(secrets_path):
        shutil.copy("secrets.example.yaml", secrets_path)
    from vcoach.ai import DEFAULTS, load_secrets

    if provider == "custom":
        url = input(f"Address up to /v1 [{cfg['ai'].get('custom_base_url') or DEFAULTS['custom']['base_url']}]: ").strip()
        model = input(f"Model name [{cfg['ai'].get('custom_model') or DEFAULTS['custom']['model']}]: ").strip()
        if url:
            set_config_value(args.config, ["ai", "custom_base_url"], url.rstrip("/"))
        if model:
            set_config_value(args.config, ["ai", "custom_model"], model)
    key_name = DEFAULTS[provider]["secret"]
    existing = (load_secrets(secrets_path).get(key_name) or "").strip()
    prompt = "Paste your API key and press Enter (it does not show on screen)"
    if DEFAULTS[provider].get("key_optional"):
        prompt += ". For Ollama on this PC, press Enter"
    if existing:
        prompt += ", or press Enter to keep the saved key"
    key = getpass.getpass(prompt + ": ").strip()
    if key:
        set_config_value(secrets_path, [key_name], key)
        print(f"Key saved in {secrets_path}. Keep that file private.")
    elif not existing and not DEFAULTS[provider].get("key_optional"):
        raise SystemExit("No key given. No change made.")

    set_config_value(args.config, ["ai", "provider"], provider)
    cfg = load_yaml(args.config)
    print()
    if _ai_try(cfg, cfg["coach"].get("personality", "calm") if cfg["coach"].get("personality") not in ("random", "mix") else "calm", count=2):
        print("\nAI coach is ON. Start the coach as normal.")
    else:
        print("\nThe test did not work. The coach still runs with scripted lines until you fix the problem.")


def run_ai_test(cfg, args):
    choice = args.personality or cfg["coach"].get("personality", "calm")
    if choice in ("random", "mix"):
        choice = "calm"
    _ai_try(cfg, choice, speak=not getattr(args, "silent", False))


def run_calibrate(cfg, args):
    if args.video:
        cap = cv2.VideoCapture(args.video)
        cap.set(cv2.CAP_PROP_POS_MSEC, args.at * 1000)
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("Cannot read a frame at that time.")
    else:
        print(f"Taking a screenshot in {args.delay} seconds. Switch to Valorant now.")
        time.sleep(args.delay)
        _, frame = ScreenSource(cfg["monitor"]).read_frame()
        if frame is None:
            raise SystemExit("Windows cannot capture the screen. Unlock the PC and try again.")

    cv2.imwrite("calibration_raw.png", frame)
    out = frame.copy()
    detector = EnemyDetector(cfg)
    mask, (x0, y0) = detector.mask(frame)
    roi = out[y0:y0 + mask.shape[0], x0:x0 + mask.shape[1]]
    roi[mask > 0] = (255, 0, 255)  # Enemy highlight pixels show in magenta.
    for name, region in cfg["regions"].items():
        a, b, c, d = crop_box(frame.shape, region)
        cv2.rectangle(out, (a, b), (c, d), (0, 255, 0), 2)
        cv2.putText(out, name, (a, max(15, b - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    h, w = frame.shape[:2]
    cv2.drawMarker(out, (w // 2, h // 2), (0, 255, 255), cv2.MARKER_CROSS, 30, 2)
    cv2.imwrite(args.out, out)
    print(f"Saved {args.out} (regions in green, enemy pixels in magenta).")

    enemy = detector.detect(frame)
    print(f"Enemy pixels: {enemy['pixels']}, visible: {enemy['visible']}, head offset: {enemy['head_offset']}")
    try:
        from vcoach.hud import HudReader

        reader = HudReader(cfg)
        print(f"Ammo: {reader.ammo(frame)}  Health: {reader.health(frame)}  Score: {reader.score(frame)}  "
              f"Banner: {reader.banner(frame)!r}")
    except Exception as exc:  # Tesseract missing or not on PATH.
        print(f"OCR is not available: {exc}")


def main():
    p = argparse.ArgumentParser(description="Valorant screen coach")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--lines", help="Use a custom personality file instead of the personalities folder.")
    p.add_argument("--personality", help="calm, hype, tough, mate, sassy, savage, random or mix. Overrides config.yaml.")
    sub = p.add_subparsers(dest="mode", required=True)

    sub.add_parser("personalities")
    vo = sub.add_parser("voices")
    vo.add_argument("--json", action="store_true")
    mi = sub.add_parser("mics")
    mi.add_argument("--json", action="store_true")
    sub.add_parser("ai-setup")
    sub.add_parser("install-tesseract")
    sub.add_parser("text-test")
    sub.add_parser("self-test")
    sub.add_parser("update", help="Get the newest version from GitHub.")
    sub.add_parser("check-update", help="Show if a newer version is on GitHub.")
    an = sub.add_parser("analyze")
    an.add_argument("report", nargs="?", help="A report in the reports folder. Default: the newest one.")
    sub.add_parser("install-voice-model")
    ait = sub.add_parser("ai-test")
    ait.add_argument("--personality", default=argparse.SUPPRESS)
    lis = sub.add_parser("listen")
    lis.add_argument("--silent", action="store_true")
    lis.add_argument("--personality", default=argparse.SUPPRESS)
    pre = sub.add_parser("preview")
    pre.add_argument("--silent", action="store_true")
    pre.add_argument("--personality", default=argparse.SUPPRESS)

    live = sub.add_parser("live")
    live.add_argument("--personality", default=argparse.SUPPRESS)
    live.add_argument("--log", help="Write a metrics CSV for tuning.")
    live.add_argument("--silent", action="store_true", help="Print messages instead of speaking.")
    live.add_argument("--no-mic", action="store_true", help="Turn off voice commands.")
    live.add_argument("--record", action="store_true",
                      help="Save every reading and pictures of this match to the recordings folder.")

    rep = sub.add_parser("replay")
    rep.add_argument("video")
    rep.add_argument("--personality", default=argparse.SUPPRESS)
    rep.add_argument("--log", help="Write a metrics CSV for tuning.")
    rep.add_argument("--speak", action="store_true", help="Speak messages during replay.")

    ed = sub.add_parser("editor")
    ed.add_argument("--port", type=int, default=8765)
    ed.add_argument("--no-browser", action="store_true")
    pa = sub.add_parser("panel")
    pa.add_argument("--port", type=int, default=8765)
    pa.add_argument("--no-browser", action="store_true")

    cal = sub.add_parser("calibrate")
    cal.add_argument("--video")
    cal.add_argument("--at", type=float, default=0.0, help="Seconds into the video.")
    cal.add_argument("--delay", type=int, default=5)
    cal.add_argument("--out", default="calibration.png")

    args = p.parse_args()
    from vcoach.util import ensure_config

    ensure_config(args.config)  # Creates config.yaml the first time, and adds new settings after an update.
    if args.mode in ("editor", "panel"):
        run_editor(args, panel=args.mode == "panel")
        return
    if args.mode == "install-voice-model":
        from vcoach.vosk_setup import download_model

        path = load_yaml(args.config).get("voice_control", {}).get("model_path", "models/vosk-model-small-en-us-0.15")
        raise SystemExit(0 if download_model(path) else 1)
    if args.mode == "self-test":
        raise SystemExit(run_self_test())
    if args.mode in ("update", "check-update"):
        from vcoach import updater

        root = os.path.dirname(os.path.abspath(__file__))
        if args.mode == "update":
            raise SystemExit(updater.update(root))
        info = updater.check(root)
        if info["remote"] is None:
            raise SystemExit("Could not reach GitHub.")
        print(f"Your version: {info['local']}. Version on GitHub: {info['remote']}."
              + (" A newer version is ready. Run the update from the Control Panel." if info["newer"] else " You are up to date."))
        return
    if args.mode == "analyze":
        from vcoach.ai import AIClient, load_secrets
        from vcoach.analysis import analyze_report

        cfg = load_yaml(args.config)
        ai_cfg = cfg.get("ai", {}) or {}
        client = AIClient(ai_cfg, load_secrets(ai_cfg.get("secrets_file", "secrets.yaml")))
        if not client.enabled:
            raise SystemExit(f"The AI coach is off: {client.reason} Set it up on the AI coach page first.")
        folder = cfg.get("reports_dir", "reports")
        names = sorted(n for n in os.listdir(folder) if n.endswith(".md")) if os.path.isdir(folder) else []
        name = os.path.basename(args.report) if args.report else (names[-1] if names else None)
        if not name or not os.path.isfile(os.path.join(folder, name)):
            raise SystemExit("No report found.")
        print(f"Asking {client.model} for a review of {name}. This can take up to a minute...")
        review = analyze_report(os.path.join(folder, name), client, timeout=float(ai_cfg.get("analysis_timeout_s", 90)))
        if not review:
            raise SystemExit("The AI gave no usable review. Try again later.")
        print("\n" + review + "\n\nThe review is at the end of the report.")
        return
    if args.mode == "text-test":
        from vcoach.texttest import run_text_test

        raise SystemExit(run_text_test(load_yaml(args.config)))
    if args.mode == "install-tesseract":
        from vcoach.tesseract_setup import install_tesseract

        raise SystemExit(0 if install_tesseract() else 1)
    cfg = load_yaml(args.config)
    if args.mode == "calibrate":
        run_calibrate(cfg, args)
    elif args.mode == "personalities":
        for key, name, desc in available():
            print(f"{key:10} {name}: {desc}")
        print(f"{'random':10} Picks a different personality at the start of each match.")
        print(f"{'mix':10} Picks a different personality each round. It does not say which one.")
    elif args.mode == "voices":
        if args.json:
            import json

            from vcoach.voice import voice_names

            print(json.dumps(voice_names()))
        else:
            list_voices()
    elif args.mode == "ai-setup":
        run_ai_setup(cfg, args)
    elif args.mode == "ai-test":
        run_ai_test(cfg, args)
    elif args.mode == "mics":
        if args.json:
            import json

            import sounddevice as sd

            print(json.dumps([{"index": i, "name": d["name"]} for i, d in enumerate(sd.query_devices())
                              if d["max_input_channels"] > 0]))
        else:
            from vcoach.listen import list_mics

            list_mics()
    elif args.mode == "listen":
        run_listen(cfg, args)
    elif args.mode == "preview":
        run_preview(cfg, args)
    elif args.mode == "live":
        run_live(cfg, args)
    else:
        run_replay(cfg, args)


if __name__ == "__main__":
    main()
