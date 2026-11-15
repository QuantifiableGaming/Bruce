import math
import os
import random
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vcoach.checks import Analyzer, RoundStats  # noqa: E402
from vcoach.engine import Coach  # noqa: E402
from vcoach.trackers import AmmoTracker, BannerTracker, HealthTracker  # noqa: E402
from vcoach.vision import EnemyDetector, MotionMeter  # noqa: E402

from vcoach.util import load_yaml  # noqa: E402

CFG = load_yaml(os.path.join(ROOT, "config.yaml"))
CFG["banner_confirm_reads"] = 1  # Most tests send each banner one time. The confirm rule has its own test.
os.chdir(ROOT)
from vcoach.personality import PersonalityPicker, available, load  # noqa: E402

LINES = load("calm")


def texture(h=360, w=640, seed=0):
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (h, w), dtype=np.uint8)
    return cv2.cvtColor(cv2.GaussianBlur(noise, (0, 0), 3), cv2.COLOR_GRAY2BGR)


# ---------- trackers ----------

def test_ammo_burst_and_reload():
    tr = AmmoTracker(burst_gap=0.35)
    events = []
    seq = [(0.0, 25), (0.1, 24), (0.2, 23), (0.3, 22), (0.4, 21), (1.0, 21), (3.5, 25), (3.6, 25)]
    for t, a in seq:
        events += tr.update(t, a)
    kinds = [e[0] for e in events]
    assert kinds == ["burst_start", "burst_end", "reload_done"]
    assert events[1][1]["shots"] == 4
    assert events[2][1]["before"] == 21 and events[2][1]["after"] == 25


def test_ammo_ocr_glitch_up_is_not_reload():
    tr = AmmoTracker()
    events = []
    for t, a in [(0.0, 20), (0.1, 28), (0.2, 20), (0.3, 20)]:
        events += tr.update(t, a)
    assert not any(e[0] == "reload_done" for e in events)


def test_health_damage_and_death():
    tr = HealthTracker()
    events = []
    for t, hp in [(0, 100), (0.1, 100), (0.2, 60), (0.3, 60), (0.4, 0), (0.5, 0), (0.6, 100), (0.7, 100)]:
        events += tr.update(t, hp)
    assert [e[0] for e in events] == ["damage", "damage", "death"]
    assert not tr.alive


def test_banner_round_and_match():
    bt = BannerTracker(CFG["banner_keywords"], confirm_reads=1)
    assert bt.update(0, "CLUTCH") is None
    ev = bt.update(1, "ROUND WON")
    assert ev[0] == "round_end" and ev[1]["won"] is True and ev[1]["credit"] == "CLUTCH"
    assert bt.update(3, "ROUND WON") is None  # cooldown
    assert bt.update(12, "ROUND LOST")[1]["won"] is False
    assert bt.update(14, "DEFEAT")[0] == "match_end"


# ---------- vision ----------

def test_enemy_head_above_crosshair_means_aim_low():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    # Red outline of a body whose top (head) is 80 px above screen center.
    cv2.rectangle(frame, (940, 460), (980, 700), (0, 0, 255), 3)
    res = EnemyDetector(CFG).detect(frame)
    assert res["visible"]
    assert 50 < res["head_offset"] < 90


def test_enemy_not_visible_on_empty_frame():
    res = EnemyDetector(CFG).detect(np.zeros((1080, 1920, 3), np.uint8))
    assert not res["visible"] and res["head_offset"] is None


def rotate_frame(img, yaw_deg, hfov_deg=103.0):
    h, w = img.shape[:2]
    f = (w / 2) / math.tan(math.radians(hfov_deg) / 2)
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]])
    a = math.radians(yaw_deg)
    R = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    H = K @ R @ np.linalg.inv(K)
    return cv2.warpPerspective(img, H, (w, h), borderMode=cv2.BORDER_REFLECT)


def test_motion_meter_ignores_rotation_and_sees_parallax():
    base = texture(seed=1)
    near = texture(seed=2)

    meter = MotionMeter()
    meter.update(base)
    rot_score = meter.update(rotate_frame(base, 1.5))  # a fast camera turn

    def scene(bg_shift, fg_shift):
        bg = np.roll(base, bg_shift, axis=1)
        out = bg.copy()
        fg = np.roll(near, fg_shift, axis=1)
        out[:, 0:220] = fg[:, 0:220]  # a near wall on the left side
        return out

    meter2 = MotionMeter()
    meter2.update(scene(0, 0))
    strafe_score = meter2.update(scene(2, 10))  # strafing: near wall moves more than far scene

    assert strafe_score > 3 * max(rot_score, 0.05), (rot_score, strafe_score)

    meter3 = MotionMeter()
    meter3.update(base)
    assert meter3.update(rotate_frame(base, 6)) is None  # a flick is not judged


# ---------- analyzer ----------

def test_analyzer_counts_moving_burst_and_long_spray():
    an = Analyzer(CFG)
    no_enemy = {"visible": False, "head_offset": None, "pixels": 0}
    t = 0.0
    hud = 0
    ammo = 25
    for i in range(40):  # 40 frames at ~30 fps with high motion
        an.on_frame(t, 2.0, no_enemy)
        if i % 3 == 0:
            if hud >= 2 and ammo > 15:
                ammo -= 1
            an.on_hud(t, ammo, 100, None)
            hud += 1
        t += 1 / 30
    out = an.on_hud(t + 0.5, ammo, 100, "ROUND LOST")
    assert out and out[0][0] == "round_end"
    stats = out[0][2]
    assert stats.bursts == 1 and stats.judged_bursts == 1 and stats.moving_bursts == 1
    assert stats.long_sprays == 1


def test_analyzer_bad_reload():
    an = Analyzer(CFG)
    seq = [(0.0, 20, 100), (0.1, 20, 100), (0.5, 20, 70), (0.6, 20, 70), (2.0, 25, 70), (2.1, 25, 70)]
    for t, a, hp in seq:
        an.on_hud(t, a, hp, None)
    assert an.round.bad_reloads == 1


# ---------- coach ----------

def stats_with(**kw):
    s = RoundStats()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_coach_tip_then_cooldown():
    coach = Coach(LINES, CFG, random.Random(1))
    s = stats_with(bursts=4, judged_bursts=4, moving_bursts=3)
    kind, text = coach.round_end(True, None, s)
    assert kind == "tip" and text in LINES["tips"]["moving_shots"]
    assert coach.round_end(True, None, s) is None  # same issue is in cooldown


def test_coach_loss_streak_reset_beats_tip():
    coach = Coach(LINES, CFG, random.Random(2))
    bad = stats_with(long_sprays=3)
    coach.round_end(False, None, RoundStats())
    kind, text = coach.round_end(False, None, bad)
    assert kind == "focus" and "short bursts" in text
    kind, _ = coach.round_end(False, None, bad)
    assert kind == "reset"


def test_coach_credit_after_streak_and_quiet_when_fine():
    coach = Coach(LINES, CFG, random.Random(3))
    coach.round_end(False, None, RoundStats())
    coach.round_end(False, None, RoundStats())
    assert coach.round_end(True, None, RoundStats())[0] == "credit"
    assert coach.round_end(True, None, RoundStats()) is None


def test_coach_no_repeat_lines():
    coach = Coach(LINES, CFG, random.Random(4))
    seen = [coach.round_end(True, "ACE", RoundStats())[1] for _ in range(len(LINES["credit"]))]
    assert len(set(seen)) == len(seen)


def test_match_end_one_good_thing():
    coach = Coach(LINES, CFG, random.Random(5))
    rounds = [stats_with(crosshair_offsets=[2, -5, 30, 4, 1, 3])]
    assert "5 of 6" in coach.match_end(rounds)
    assert coach.match_end([RoundStats()]) == LINES["match_good"]["fallback"]


# ---------- personalities ----------

def test_every_personality_is_complete():
    issues = ["crosshair_low", "crosshair_high", "moving_shots", "long_spray", "bad_reload"]
    for key, _, _ in available():
        lines = load(key)
        for issue in issues:
            assert lines["tips"][issue], (key, issue)
            for t in lines["focus"]:
                t.format(focus=lines["focus_names"][issue])
        g = lines["match_good"]
        g["crosshair"].format(good=1, total=2)
        g["strafe"].format(good=1, total=2)
        g["credit"].format(count=1)
        assert lines["voice"]["rate"] > 0


def test_random_picks_a_new_personality_each_match():
    picker = PersonalityPicker("random", rng=random.Random(7))
    assert picker.is_random
    names = [picker.next()["name"] for _ in range(10)]
    assert all(a != b for a, b in zip(names, names[1:]))
    assert len(set(names)) > 1


def test_random_pool_limits_choices():
    picker = PersonalityPicker("random", pool=["hype", "mate"], rng=random.Random(1))
    assert {picker.next()["name"] for _ in range(8)} == {"Hype", "Mate"}


def test_session_switches_personality_after_match():
    from vcoach.session import Session
    from vcoach.voice import Voice

    s = Session(CFG, PersonalityPicker("random", rng=random.Random(3)), Voice(silent=True))
    first = s.coach.lines["name"]
    frame = np.zeros((1080, 1920, 3), np.uint8)
    s.frame(0, frame)
    s.hud(1, 25, 100, "ROUND WON")
    s.hud(3, 25, 100, "VICTORY")
    assert s.coach.lines["name"] != first


# ---------- voice commands ----------

from vcoach.commands import parse, split_wake  # noqa: E402

NAMES = [n for n, _, _ in available()]
WAKE = [CFG["voice_control"]["wake_word"]] + CFG["voice_control"]["wake_aliases"]


def test_split_wake():
    assert split_wake("hey bruce how am i doing", WAKE) == (True, "how am i doing")
    assert split_wake("Bruise what's the score", WAKE) == (True, "what's the score")
    assert split_wake("bruce", WAKE) == (True, "")
    assert split_wake("rotate b they are pushing", WAKE) == (False, "")


def test_parse_intents():
    cases = {
        "how am i going": "doing", "how am i doing": "doing", "what should i work on": "focus",
        "what's the score": "score", "mute": "mute", "be quiet": "mute", "unmute": "unmute",
        "say that again": "repeat", "help": "help", "switch to sassy": "personality",
        "be hype": "personality", "go random": "personality", "": "wake", "banana": "unknown",
    }
    for text, intent in cases.items():
        assert parse(text, NAMES)[0] == intent, text
    assert parse("switch to sassy", NAMES)[1] == "sassy"


def make_session(personality="calm"):
    from vcoach.session import Session
    from vcoach.voice import Voice

    class Recorder(Voice):
        def __init__(self):
            super().__init__(silent=True)
            self.said = []

        def say(self, text):
            super().say(text)
            if not self.muted:
                self.said.append(text)

    voice = Recorder()
    return Session(CFG, PersonalityPicker(personality), voice), voice


def test_coaching_question_waits_until_round_ends():
    s, v = make_session()
    s.hud(10, 25, 100, "ROUND LOST")               # round 1 ends at t=10
    s.command(20, "how am i doing")                # buy phase: answered now
    assert "0 to 1" in v.said[-1]
    s.command(80, "what's the score")              # mid-round: queued
    assert v.said[-1] == LINES["answers"]["queued"]
    s.hud(110, 25, 100, "ROUND WON")               # round 2 ends: queued answer is spoken
    assert v.said[-1] == LINES["answers"]["score"].format(won=1, lost=1)


def test_instant_commands_work_mid_round():
    s, v = make_session()
    s.hud(10, 25, 100, "ROUND LOST")
    s.command(90, "mute")
    assert s.voice.muted
    s.command(91, "how am i doing")                # queued, and silent while muted
    s.command(92, "unmute")
    assert not s.voice.muted and v.said[-1] == LINES["answers"]["unmuted"]
    s.command(93, "switch to sassy")
    assert s.coach.lines["name"] == "Sassy"
    assert "Sassy" in v.said[-1]
    s.command(94, "repeat")
    assert "Sassy" in v.said[-1]


def test_focus_answer_uses_most_common_issue():
    s, v = make_session()
    bad = RoundStats(long_sprays=3)
    s.coach.round_end(False, None, bad)
    s.coach.round_end(False, None, bad)
    s.command(0, "what should i work on")
    assert "short bursts" in v.said[-1]


# ---------- region editor ----------

def test_write_regions_keeps_comments(tmp_path):
    import shutil

    from vcoach.editor_server import validate_regions, write_regions

    cfg = tmp_path / "config.yaml"
    shutil.copy(os.path.join(ROOT, "config.yaml"), cfg)
    before = cfg.read_text(encoding="utf-8")
    write_regions(str(cfg), {"ammo": [0.5, 0.9, 0.03, 0.04]})
    after = cfg.read_text(encoding="utf-8")
    assert "ammo: [0.5, 0.9, 0.03, 0.04]" in after
    assert after.count("#") == before.count("#")
    assert load_yaml(str(cfg))["regions"]["health"] == CFG["regions"]["health"]
    for bad in ({"ammo": [0.99, 0.9, 0.05, 0.04]}, {"ammo": [0, 0, 0, 0.1]}, {"nope": [0, 0, 0.1, 0.1]}):
        try:
            validate_regions(bad)
            assert False, bad
        except ValueError:
            pass


def test_editor_server_endpoints(tmp_path):
    import json
    import shutil
    import threading
    import urllib.request

    from vcoach.editor_server import EditorServer

    shutil.copy(os.path.join(ROOT, "config.yaml"), tmp_path / "config.yaml")
    shutil.copy(os.path.join(ROOT, "editor.html"), tmp_path / "editor.html")
    cv2.imwrite(str(tmp_path / "calibration_raw.png"), np.zeros((1080, 1920, 3), np.uint8))
    srv = EditorServer(str(tmp_path), port=8790)
    url = srv.start().rstrip("/")
    t = threading.Thread(target=srv.serve, daemon=True)
    t.start()

    def call(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    assert call("/")[0] == 200
    state = json.loads(call("/api/state")[1])
    assert state["has_image"] and "ammo" in state["regions"]
    assert call("/api/regions", {"regions": {"banner": [0.3, 0.1, 0.4, 0.2]}})[0] == 200
    assert load_yaml(str(tmp_path / "config.yaml"))["regions"]["banner"] == [0.3, 0.1, 0.4, 0.2]
    assert call("/api/regions", {"regions": {"banner": [0.9, 0.1, 0.4, 0.2]}})[0] == 400
    code, body = call("/api/test", {"regions": state["regions"]})
    result = json.loads(body)
    assert code == 200 and "enemy" in result and result["enemy_mask"].startswith("data:image/png;base64,")
    call("/api/shutdown", {})
    t.join(3)
    assert not t.is_alive()


# ---------- AI wording (mock Claude and ChatGPT servers) ----------

import json as _json  # noqa: E402
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from vcoach.ai import AIClient, AIVoiceWriter, build_request, clean_line, system_prompt  # noqa: E402


class MockAI:
    """A fake AI service that speaks the Anthropic and OpenAI formats."""

    def __init__(self, reply="Breathe. Stop before you shoot.", delay=0.0, status=200, reject_reasoning=False):
        self.reply, self.delay, self.status, self.reject_reasoning = reply, delay, status, reject_reasoning
        self.requests = []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                _time.sleep(mock.delay)
                if mock.status != 200:
                    return self._send(mock.status, {"error": {"message": "bad key"}})
                if self.path == "/v1/messages":
                    return self._send(200, {"content": [{"type": "text", "text": mock.reply}]})
                if mock.reject_reasoning and "reasoning_effort" in body:
                    return self._send(400, {"error": {"message": "Unsupported parameter: 'reasoning_effort'"}})
                return self._send(200, {"choices": [{"message": {"content": mock.reply}}]})

            def _send(self, code, obj):
                data = _json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        _threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def cfg(self, provider, timeout=2):
        return {"provider": provider, f"{provider}_base_url": self.url, "timeout_s": timeout}

    def close(self):
        self.httpd.shutdown()


def test_claude_request_format():
    mock = MockAI(reply='"Crosshair up, love."')
    c = AIClient(mock.cfg("claude"), {"anthropic_api_key": "sk-test"})
    assert clean_line(c.complete("sys", "hi")) == "Crosshair up, love."
    path, headers, body = mock.requests[0]
    assert path == "/v1/messages" and headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert body["model"] == "claude-haiku-5-5" and body["system"] == "sys" and body["max_tokens"] > 0
    mock.close()


def test_chatgpt_request_and_reasoning_retry():
    mock = MockAI(reply="Burst, reset, burst.", reject_reasoning=True)
    c = AIClient(mock.cfg("chatgpt"), {"openai_api_key": "sk-o"})
    assert c.complete("sys", "hi") == "Burst, reset, burst."
    (p1, h1, b1), (p2, h2, b2) = mock.requests
    assert p1 == "/v1/chat/completions" and h1["authorization"] == "Bearer sk-o"
    assert b1["reasoning_effort"] == "none" and "reasoning_effort" not in b2
    assert "max_completion_tokens" not in b1
    assert b1["messages"][0]["role"] == "system"
    mock.close()


def test_no_key_means_ai_off(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = AIClient({"provider": "claude"}, {})
    assert not c.enabled and "key" in c.reason.lower()
    assert not AIClient({"provider": "off"}, {}).enabled


def test_clean_line():
    assert clean_line("**Coach:** Stop before you shoot! 🔥\nMore text") == "Stop before you shoot!"
    long = " ".join(["word"] * 60)
    assert len(clean_line(long, 25).split()) <= 25
    assert clean_line("   ") is None


def collect(writer, n, timeout=6):
    out = []
    writer_done = _threading.Event()

    def done(text, from_ai):
        out.append((text, from_ai))
        if len(out) >= n:
            writer_done.set()
    return out, done, writer_done


def test_writer_uses_ai_then_falls_back_on_error_and_timeout():
    good = MockAI(reply="Nice reset. Next one is yours.")
    w = AIVoiceWriter(AIClient(good.cfg("claude"), {"anthropic_api_key": "k"}))
    out, done, ev = collect(w, 1)
    w.submit("s", "u", "SCRIPTED", done)
    assert ev.wait(5) and out[0] == ("Nice reset. Next one is yours.", True)
    good.close()

    bad = MockAI(status=401)
    w = AIVoiceWriter(AIClient(bad.cfg("chatgpt"), {"openai_api_key": "k"}))
    out, done, ev = collect(w, 1)
    w.submit("s", "u", "SCRIPTED", done)
    assert ev.wait(5) and out[0] == ("SCRIPTED", False)
    bad.close()

    slow = MockAI(delay=2.5)
    w = AIVoiceWriter(AIClient(slow.cfg("claude", timeout=1), {"anthropic_api_key": "k"}))
    out, done, ev = collect(w, 1)
    start = _time.monotonic()
    w.submit("s", "u", "SCRIPTED", done)
    assert ev.wait(5) and out[0] == ("SCRIPTED", False)
    assert _time.monotonic() - start < 2.4
    slow.close()


def test_prompt_has_rules_and_facts():
    coach = Coach(LINES, CFG, random.Random(1))
    coach.round_end(False, None, RoundStats(long_sprays=3))
    sys_p = system_prompt(load("sassy"), 25)
    assert "Never give tactical calls" in sys_p and "Sassy" in sys_p
    user = build_request("tip", coach, LINES, stats=RoundStats(long_sprays=3), issue="long_spray",
                         recent=["old line"])
    assert "spraying too long" in user and "lost 1" in user and "old line" in user


def test_session_speaks_ai_line_and_answers_free_questions():
    mock = MockAI(reply="Short bursts, champ. You've got this.")
    cfg = dict(CFG)
    cfg["ai"] = mock.cfg("claude")
    from vcoach.ai import AIClient as _C

    writer = AIVoiceWriter(_C(cfg["ai"], {"anthropic_api_key": "k"}))
    s, v = make_session()
    s.ai = writer
    s.hud(10, 25, 100, "ROUND LOST")
    s.analyzer.round = RoundStats()
    s.coach.round_end(False, None, RoundStats(long_sprays=3))  # make a known issue
    s.command(12, "should i buy an operator")                  # free question, buy phase
    for _ in range(50):
        if v.said and v.said[-1].startswith("Short bursts"):
            break
        _time.sleep(0.1)
    assert v.said[-1] == "Short bursts, champ. You've got this."
    _, _, body = mock.requests[-1]
    assert "should i buy an operator" in body["messages"][0]["content"]
    mock.close()


def test_find_tesseract_uses_setting_or_returns_none(tmp_path, monkeypatch):
    from vcoach.hud import find_tesseract

    fake = tmp_path / "tesseract.exe"
    fake.write_text("x")
    assert find_tesseract(str(fake)) == str(fake)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "none"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "none"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    assert find_tesseract("") is None


def test_gun_area_is_ignored():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    # A red "gun" in the bottom right of the search area, and no enemy.
    cv2.rectangle(frame, (1150, 600), (1300, 800), (0, 0, 255), -1)
    cfg = dict(CFG)
    cfg["regions"] = dict(CFG["regions"], gun_ignore=[0.58, 0.5, 0.42, 0.5])
    res = EnemyDetector(cfg).detect(frame)
    assert not res["visible"] and res["ignored"] > 0
    cfg["regions"]["gun_ignore"] = [0.9, 0.9, 0.05, 0.05]   # gun area elsewhere: the gun counts
    assert EnemyDetector(cfg).detect(frame)["visible"]


def test_write_enemy_color(tmp_path):
    import shutil

    from vcoach.editor_server import write_enemy_color

    cfg = tmp_path / "config.yaml"
    shutil.copy(os.path.join(ROOT, "config.yaml"), cfg)
    write_enemy_color(str(cfg), "purple")
    assert load_yaml(str(cfg))["enemy_highlight"]["color"] == "purple"
    try:
        write_enemy_color(str(cfg), "green")
        assert False
    except ValueError:
        pass


def test_gun_side_and_mirror(tmp_path):
    import shutil

    from vcoach.editor_server import mirror_region, write_gun_side

    assert mirror_region([0.58, 0.5, 0.42, 0.5]) == [0.0, 0.5, 0.42, 0.5]
    assert mirror_region(mirror_region([0.6, 0.5, 0.3, 0.4])) == [0.6, 0.5, 0.3, 0.4]
    cfg = tmp_path / "config.yaml"
    shutil.copy(os.path.join(ROOT, "config.yaml"), cfg)
    write_gun_side(str(cfg), "left")
    assert load_yaml(str(cfg))["gun_side"] == "left"


# ---------- voice model download ----------

def _serve_dir(directory):
    import functools
    from http.server import SimpleHTTPRequestHandler

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *a: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_vosk_model_download_and_unzip(tmp_path):
    import zipfile

    from vcoach.vosk_setup import download_model, model_ready

    name = "vosk-model-small-en-us-0.15"
    web = tmp_path / "web"
    web.mkdir()
    with zipfile.ZipFile(web / f"{name}.zip", "w") as zf:
        zf.writestr(f"{name}/am/final.mdl", "x" * 1000)
        zf.writestr(f"{name}/conf/model.conf", "conf")
    httpd, url = _serve_dir(web)
    target = tmp_path / "models" / name
    assert not model_ready(str(target))
    assert download_model(str(target), url=f"{url}/{name}.zip", log=lambda m: None)
    assert model_ready(str(target)) and (target / "am" / "final.mdl").exists()
    assert download_model(str(target), url="http://127.0.0.1:1/none.zip", log=lambda m: None)  # already there
    httpd.shutdown()


def test_vosk_model_download_failures(tmp_path):
    import zipfile

    from vcoach.vosk_setup import download_model

    web = tmp_path / "web"
    web.mkdir()
    with zipfile.ZipFile(web / "evil.zip", "w") as zf:
        zf.writestr("../outside.txt", "bad")
    (web / "notzip.zip").write_text("hello")
    httpd, url = _serve_dir(web)
    quiet = lambda m: None  # noqa: E731
    assert not download_model(str(tmp_path / "m1"), url=f"{url}/evil.zip", log=quiet)
    assert not (tmp_path / "outside.txt").exists()
    assert not download_model(str(tmp_path / "m2"), url=f"{url}/notzip.zip", log=quiet)
    assert not download_model(str(tmp_path / "m3"), url=f"{url}/missing.zip", log=quiet)
    httpd.shutdown()


# ---------- control panel ----------

def test_panel_endpoints(tmp_path):
    import json
    import shutil
    import urllib.request

    from vcoach.panel_server import PanelServer

    for name in ("config.yaml", "editor.html", "panel.html", "main.py", "QUICKSTART.md", "README.md",
                 "secrets.example.yaml", "requirements.txt"):
        shutil.copy(os.path.join(ROOT, name), tmp_path / name)
    for folder in ("vcoach", "personalities", "icons"):
        shutil.copytree(os.path.join(ROOT, folder), tmp_path / folder,
                        ignore=shutil.ignore_patterns("__pycache__"))
    srv = PanelServer(str(tmp_path), port=8795)
    url = srv.start().rstrip("/")
    t = _threading.Thread(target=srv.serve, daemon=True)
    t.start()

    def call(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"null") if "json" in r.headers.get("Content-Type", "") else r.read()
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    assert call("/")[0] == 200 and call("/editor")[0] == 200
    code, st = call("/api/status")
    assert code == 200 and st["personality"] == "calm" and len(st["personalities"]) >= 5
    assert call("/api/personality", {"name": "mate"})[0] == 200
    assert call("/api/personality", {"name": "nope"})[0] == 400
    assert load_yaml(str(tmp_path / "config.yaml"))["coach"]["personality"] == "mate"

    code, groups = call("/api/settings")
    assert code == 200 and groups[0]["items"]
    assert call("/api/settings", {"changes": [{"path": ["checks", "long_spray", "max_shots"], "value": 9}]})[0] == 200
    assert call("/api/settings", {"changes": [{"path": ["checks", "long_spray", "max_shots"], "value": 99}]})[0] == 400
    assert call("/api/settings", {"changes": [{"path": ["regions", "ammo"], "value": 1}]})[0] == 400
    assert load_yaml(str(tmp_path / "config.yaml"))["checks"]["long_spray"]["max_shots"] == 9

    assert call("/api/ai", {"provider": "chatgpt", "key": "sk-xyz", "use_for": ["tips", "bad"]})[0] == 200
    cfg = load_yaml(str(tmp_path / "config.yaml"))
    assert cfg["ai"]["provider"] == "chatgpt" and cfg["ai"]["use_for"] == ["tips"]
    assert load_yaml(str(tmp_path / "secrets.yaml"))["openai_api_key"] == "sk-xyz"
    code, st = call("/api/status")
    assert st["ai_keys"]["chatgpt"] and "sk-xyz" not in json.dumps(st)   # the key never goes back to the page

    assert call("/api/voice", {"voice_name": "Zira", "voice_rate": 200})[0] == 200
    assert call("/api/voice", {"voice_name": "", "voice_rate": 999})[0] == 400
    assert call("/api/mic", {"mic_device": "3"})[0] == 200
    assert load_yaml(str(tmp_path / "config.yaml"))["voice_control"]["mic_device"] == 3

    code, task = call("/api/run", {"kind": "preview", "personality": "hype"})
    assert code == 200
    for _ in range(100):
        code, info = call(f"/api/task/{task['id']}")
        if not info["running"]:
            break
        _time.sleep(0.2)
    assert any("Hype" in line for line in info["lines"])
    assert call("/api/run", {"kind": "rm -rf /"})[0] == 400

    os.makedirs(tmp_path / "reports")
    (tmp_path / "reports" / "match_1.md").write_text("# Match report\n")
    code, reps = call("/api/reports")
    assert reps[0]["name"] == "match_1.md"
    assert call("/api/report/match_1.md")[1]["text"].startswith("# Match")
    assert call("/api/report/..%2Fconfig.yaml")[0] == 404

    call("/api/shutdown", {})
    t.join(5)


# ---------- wake-word listener with a fake microphone and fake Vosk ----------

def test_listener_starts_and_hears_bruce(tmp_path, monkeypatch):
    """Runs the real listener code with fake sounddevice and vosk modules."""
    import sys as _sys
    import types

    from vcoach.listen import WakeListener

    model = tmp_path / "model"
    (model / "am").mkdir(parents=True)

    phrases = iter(["hello there", "bruce how am i doing", "bruce", "what's the score"])

    class FakeRecognizer:
        def __init__(self, *a):
            self.text = ""

        def AcceptWaveform(self, data):
            self.text = next(phrases, "")
            return True

        def Result(self):
            return _json.dumps({"text": self.text})

    fake_vosk = types.SimpleNamespace(SetLogLevel=lambda n: None, Model=lambda p: object(),
                                      KaldiRecognizer=FakeRecognizer)

    class FakeStream:
        def __init__(self, callback=None, **kw):
            self.cb = callback

        def __enter__(self):
            def feed():
                for _ in range(6):
                    _time.sleep(0.05)
                    self.cb(b"\0" * 8000, 4000, None, None)
            _threading.Thread(target=feed, daemon=True).start()
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(_sys.modules, "vosk", fake_vosk)
    monkeypatch.setitem(_sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=FakeStream))

    cfg = dict(CFG)
    cfg["voice_control"] = dict(CFG["voice_control"], model_path=str(model))
    heard = []
    lis = WakeListener(cfg, on_text=heard.append)
    lis.start()
    assert lis.ready.wait(5)
    _time.sleep(0.8)
    lis.stop()
    assert lis.error is None, lis.error
    assert heard == ["how am i doing", "", "what's the score"]


# ---------- speech engines ----------

def test_voice_falls_back_to_next_speech_engine(capsys):
    from vcoach.voice import Voice

    spoken = []

    class Broken:
        name = "Broken"

        def __init__(self):
            raise RuntimeError("no COM")

    class FailsOnSpeak:
        name = "FailsOnSpeak"

        def apply(self, rate, name):
            pass

        def speak(self, text):
            raise RuntimeError("silent failure made loud")

    class Good:
        name = "Good"

        def apply(self, rate, name):
            spoken.append(("style", rate, name))

        def speak(self, text):
            spoken.append(text)

    v = Voice(rate=200, voice_name="Zira", backends=[Broken, FailsOnSpeak, Good])
    v.say("Hello")
    v.wait()
    v.set_style(150, "")
    v.say("Again")
    v.wait()
    assert spoken == [("style", 200, "Zira"), "Hello", ("style", 150, ""), "Again"]
    out = capsys.readouterr().out
    assert "Broken is not available" in out and "ERROR in FailsOnSpeak" in out and "Speech engine: Good" in out


def test_voice_with_no_engine_does_not_hang(capsys):
    from vcoach.voice import Voice, _sapi_rate

    class Broken:
        name = "Broken"

        def __init__(self):
            raise RuntimeError("x")

    v = Voice(backends=[Broken])
    v.say("Hello")
    v.wait()
    assert "no speech engine works" in capsys.readouterr().out
    assert _sapi_rate(180) == 0 and _sapi_rate(300) == 8 and _sapi_rate(20) == -10


# ---------- false round alarms from menu text ----------

def test_banner_needs_whole_words_and_two_reads():
    bt = BannerTracker(CFG["banner_keywords"], confirm_reads=2)
    for i, menu in enumerate(["WINDOWED MODE", "SHOWING TWINS", "WONDERFUL SKINS", "GLOSSY LOSSES"]):
        assert bt.update(i, menu) is None and bt.candidate is None      # substrings do not count
    assert bt.update(10, "ROUND WON") is None                           # first read: wait
    assert bt.update(10.5, "ROUND WON")[0] == "round_end"               # second read: confirmed
    assert bt.update(30, "ROUND LOST") is None
    assert bt.update(40, "ROUND LOST") is None                          # too far apart: not confirmed
    assert bt.update(40.5, "ROUND LOST")[1]["won"] is False


def test_no_rounds_from_menus_without_hud(capsys):
    cfg = dict(CFG, banner_confirm_reads=2)
    an = Analyzer(cfg)
    for i in range(6):                                                   # lobby: result words, no HUD numbers
        assert an.on_hud(i * 0.5, None, None, "WIN 5 ROUNDS") == []
    assert "Ignored (menu or lobby)" in capsys.readouterr().out
    an.on_hud(100, 25, 100, None)                                        # match starts: HUD is readable
    assert an.on_hud(110, 24, 100, "ROUND WON") == []
    out = an.on_hud(110.5, 24, 100, "ROUND WON")
    assert out and out[0][0] == "round_end"


# ---------- misheard commands (from a real match log) ----------

def test_misheard_commands_from_real_log():
    cases = {
        "what's the school": "score", "our to the school": "score", "what's the score": "score",
        "how did i go that round": "doing", "give me an update": "doing",
        "what can i say": "help", "what can it": "help", "what can i improve on it": "focus",
        "switch to sasha": "personality", "headed guy that round": "unknown", "banana": "unknown",
    }
    for text, intent in cases.items():
        assert parse(text, NAMES)[0] == intent, (text, parse(text, NAMES))


def test_banner_reader_fixes_lookalike_digits():
    from vcoach.hud import HudReader

    assert "W0N L05T".translate(HudReader.LOOKALIKE) == "WON LOST"


def test_banner_read_log(tmp_path):
    from vcoach.session import Session
    from vcoach.voice import Voice

    log = tmp_path / "logs" / "banner_reads.txt"
    s = Session(CFG, PersonalityPicker("calm"), Voice(silent=True), banner_log=str(log))
    s.hud(1, 25, 100, "SOME TEXT")
    s.hud(2, 25, 100, "SOME TEXT")
    s.hud(3, 25, 100, "OTHER")
    s.close()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and "ammo=25 health=100  SOME TEXT" in lines[1] and lines[2].endswith("OTHER")


# ---------- match recording ----------

def test_match_recorder(tmp_path):
    from vcoach.recorder import MatchRecorder

    rec = MatchRecorder(CFG, root=str(tmp_path), full_every_s=0.0)
    frame = np.full((1080, 1920, 3), 90, np.uint8)
    rec.save(frame, 25, 100, True, "ROUND WON")
    rec.save(frame, None, 100, False, None)
    rec.save(frame, 24, None, True, "NOISE TEXT")
    rec.save(frame, 24, 100, True, "ROUND WON")
    rec.close()
    rec.save(frame, 1, 1, True, "LATE")                      # ignored after close
    folder = rec.folder
    rows = open(os.path.join(folder, "readings.csv"), encoding="utf-8").read().splitlines()
    assert len(rows) == 5 and "ROUND WON" in rows[1]
    assert len(os.listdir(os.path.join(folder, "banner"))) == 3
    assert len(os.listdir(os.path.join(folder, "hud"))) == 3
    assert len(os.listdir(os.path.join(folder, "full"))) == 4
    summary = open(os.path.join(folder, "summary.txt"), encoding="utf-8").read()
    assert "Ammo read: 75%" in summary and "WON: 2" in summary and "NOISE TEXT" in summary


def test_panel_passes_record_flag(tmp_path):
    from vcoach.panel_server import PanelServer

    srv = PanelServer(str(tmp_path))
    seen = {}

    class FakeTask:
        running = True

    def fake_run(args, env=None):
        seen["args"] = args
        return FakeTask()

    srv.run = fake_run
    srv.start_coach("sassy", record=True)
    assert seen["args"] == ["live", "--personality", "sassy", "--record"]
