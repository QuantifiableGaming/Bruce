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

CFG = load_yaml(os.path.join(ROOT, "config.default.yaml"))
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
    shutil.copy(os.path.join(ROOT, "config.default.yaml"), cfg)
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

    shutil.copy(os.path.join(ROOT, "config.default.yaml"), tmp_path / "config.yaml")
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
    shutil.copy(os.path.join(ROOT, "config.default.yaml"), cfg)
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
    shutil.copy(os.path.join(ROOT, "config.default.yaml"), cfg)
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

    for name in ("config.default.yaml", "editor.html", "panel.html", "main.py", "QUICKSTART.md", "README.md",
                 "secrets.example.yaml", "requirements.txt"):
        shutil.copy(os.path.join(ROOT, name), tmp_path / name)
    shutil.copy(os.path.join(ROOT, "config.default.yaml"), tmp_path / "config.yaml")
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
        assert an.on_hud(i * 0.5, None, None, "ROUNDS WON 5") == []
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
    rec.save_hud(frame, 25, 100)
    rec.save_hud(frame, None, 100)
    rec.save_scene(frame, {"score": (0, 0), "banner": "BUY PHASE PRESS BUY"})
    rec.save_scene(frame, {"score": None, "banner": None, "screen": "PEARL SWIFTPLAY", "agent": "SENTINEL CYPHER"})
    rec.save_scene(frame, {"score": (1, 0), "banner": "WON ENEMY TEAM ELIMINATED"})
    rec.close()
    rec.save_hud(frame, 1, 1)                                 # ignored after close
    folder = rec.folder
    rows = open(os.path.join(folder, "readings.csv"), encoding="utf-8").read().splitlines()
    assert len(rows) == 6 and "0-0" in rows[3] and "PEARL SWIFTPLAY" in rows[4]
    assert len(os.listdir(os.path.join(folder, "banner"))) == 3
    assert len(os.listdir(os.path.join(folder, "full"))) == 3
    summary = open(os.path.join(folder, "summary.txt"), encoding="utf-8").read()
    assert "Ammo read: 50%" in summary and "Score read: 67%" in summary and "1-0" in summary
    assert "WON ENEMY TEAM ELIMINATED" in summary


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
    srv.coach = None
    srv.start_coach("sassy")
    assert seen["args"] == ["live", "--personality", "sassy"]


# ---------- settings survive updates ----------

def test_ensure_config_creates_and_keeps_user_settings(tmp_path):
    import shutil

    from vcoach.util import ensure_config, set_config_value

    default = tmp_path / "config.default.yaml"
    shutil.copy(os.path.join(ROOT, "config.default.yaml"), default)
    cfg = tmp_path / "config.yaml"
    assert ensure_config(str(cfg))                          # first start: created
    assert load_yaml(str(cfg))["regions"]["ammo"] == CFG["regions"]["ammo"]
    set_config_value(str(cfg), ["coach", "personality"], "sassy")
    set_config_value(str(cfg), ["regions", "ammo"], [0.66, 0.92, 0.03, 0.04])
    assert not ensure_config(str(cfg))                      # nothing new: no change

    # An update brings a new setting and a changed default. The player's values stay.
    text = default.read_text(encoding="utf-8").replace("personality: calm", "personality: hype")
    default.write_text(text + "\nnew_feature:\n  enabled: true\n", encoding="utf-8")
    assert ensure_config(str(cfg))
    after = load_yaml(str(cfg))
    assert after["new_feature"]["enabled"] is True
    assert after["coach"]["personality"] == "sassy" and after["regions"]["ammo"] == [0.66, 0.92, 0.03, 0.04]


# ---------- score-based match tracking (built from a real recorded match) ----------

def test_match_tracker_follows_the_score():
    from vcoach.trackers import MatchTracker, find_agent

    assert find_agent("SENTINEL BYPHER") == ("Cypher", "sentinel")     # a real misread from agent select
    assert find_agent("MAP BREEZE") == (None, None)
    m = MatchTracker()
    ev = []
    ev += m.on_screen(80, "PEARL SWIFTPLAY")
    ev += m.on_agent(81, "SENTINEL BYPHER") + m.on_agent(85, "SENTINEL BYPHER") + m.on_agent(90, "SENTINEL CYPHER")
    ev += m.on_score(240, (0, 0)) + m.on_score(241, (0, 0))
    ev += m.on_banner(242, "BUY PHASE PRESS BUY")
    assert m.in_buy(243) and not m.in_buy(260)
    ev += m.on_score(346, (0, 1)) + m.on_score(347, None) + m.on_score(348, (0, 1))    # one unreadable frame
    ev += m.on_score(360, (0, 1))
    ev += m.on_score(500, (1, 1)) + m.on_score(501, (1, 1)) + m.on_banner(503, "CLUTCH") + m.on_score(506, (1, 1))
    ev += m.on_score(600, (7, 1))                                       # one wrong read does not count
    ev += m.on_score(601, (1, 1))
    ev += m.on_score(700, (2, 1)) + m.on_score(700.5, (2, 1)) + m.on_banner(701, "TEAM ACE")
    ev += m.on_screen(720, "VICTORY")
    assert not m.on_screen(722, "VICTORY PROGRESS ROUNDS WON")           # only one match end
    names = [e[0] for e in ev]
    assert names == ["agent_select", "agent", "match_start", "round_end", "round_end", "round_end", "match_end"]
    assert ev[1][1] == {"agent": "Cypher", "role": "sentinel"}
    assert ev[3][1] == {"won": False, "credit": None, "score": (0, 1)}
    assert ev[4][1] == {"won": True, "credit": "CLUTCH", "score": (1, 1)}
    assert ev[5][1] == {"won": True, "credit": "TEAM ACE", "score": (2, 1)}
    assert ev[6][1] == {"result": "VICTORY", "score": (2, 1)}


def test_match_tracker_syncs_and_resets():
    from vcoach.trackers import MatchTracker

    m = MatchTracker()
    ev = m.on_score(1, (3, 4)) + m.on_score(2, (3, 4))                   # coach started mid-match
    assert ev == [("match_start", {"score": (3, 4)})]
    assert not m.on_score(50, (5, 4)) + m.on_score(51, (5, 4)) + m.on_score(52, (5, 4))   # a jump needs 4 reads
    assert m.on_score(53, (5, 4)) == [("score_sync", {"score": (5, 4)})]
    ev = []
    for t in (300, 301, 302, 303):                                       # next match
        ev += m.on_score(t, (0, 0))
    assert [e[0] for e in ev] == ["match_reset", "match_start"]


def test_match_tracker_ignores_misread_scores():
    """Score reads from a real match, with the wrong reads the reader made."""
    from vcoach.trackers import MatchTracker

    reads = [(98, (0, 0)), (100, (0, 0)), (107, (0, 4)), (108, (0, 4)), (109, (0, 0)), (180, (0, 4)), (184, (0, 0)),
             (213, (1, 0)), (215, (1, 0)), (258, (4, 0)), (259, (1, 0)), (291, (2, 1)), (295, (1, 1)), (297, (1, 1)),
             (344, (13, 1)), (348, (1, 1)), (361, (2, 1)), (363, (2, 1)), (390, (2, 4)), (392, (2, 1)),
             (422, (2, 2)), (424, (2, 2)), (425, (2, 3)), (426, (2, 3)), (427, (2, 2)),   # too soon after a change
             (518, (2, 3)), (520, (2, 3)), (589, (2, 4)), (591, (2, 4)), (653, (2, 5)), (655, (2, 5)), (670, (2, 5))]
    m = MatchTracker()
    ev = []
    for t, score in reads:
        ev += m.on_score(t, score)
    assert [e[0] for e in ev] == ["match_start"] + ["round_end"] * 7
    assert [e[1]["won"] for e in ev[1:]] == [True, False, True, False, False, False, False]
    assert ev[-1][1]["score"] == (2, 5)


def test_session_full_match_with_score(tmp_path):
    s, v = make_session("sassy")
    s.report.out_dir = str(tmp_path)
    s.scene("screen", 80, "PEARL SWIFTPLAY")
    assert v.said[-1] in load("sassy")["agent_select"]
    s.scene("agent", 81, "SENTINEL CYPHER")
    s.scene("agent", 90, "SENTINEL CYPHER")
    assert "Cypher" in v.said[-1]
    for t in (240, 241):
        s.scene("score", t, (0, 0))
    s.command(245, "what's the score")                                   # first round, not buy phase: queued
    assert v.said[-1] == load("sassy")["answers"]["queued"]
    for t in (300, 301, 306):
        s.scene("score", t, (0, 1))
    assert s.coach.won == 0 and s.coach.lost == 1
    assert "0" in v.said[-1] and "1" in v.said[-1]                        # the queued score answer
    s.scene("banner", 320, "MATCH POINT PRESS BUY")
    s.command(321, "what's the score")                                   # buy phase: answered now
    assert "won 0 and lost 1" in v.said[-1].lower() or "0" in v.said[-1]
    for t in (400, 401, 406):
        s.scene("score", t, (1, 1))
    s.scene("screen", 500, "VICTORY")
    debrief = v.said[-1]
    assert "1 to 1" in debrief
    assert os.listdir(tmp_path) and s.coach.won == 0                      # report saved, coach reset
    text = open(os.path.join(tmp_path, os.listdir(tmp_path)[0]), encoding="utf-8").read()
    assert "Result: VICTORY 1-1" in text


def test_debrief_and_agent_lines_for_every_personality():
    for key, _, _ in available():
        coach = Coach(load(key), CFG, random.Random(1))
        assert coach.pregame()
        for role in ("duelist", "initiator", "controller", "sentinel", None):
            assert "Jett" in coach.agent_line("Jett", role)
        coach.round_end(True, "ACE", RoundStats(long_sprays=3), score=(1, 0))
        coach.round_end(False, None, RoundStats(crosshair_offsets=[1, 2, 3, 2, 1]), score=(1, 1))
        for result in ("VICTORY", "DEFEAT", "DRAW", None):
            text = coach.debrief([RoundStats(crosshair_offsets=[1, 2, 3, 2, 1])], result, (1, 1))
            assert "{" not in text and "short bursts" in text
            assert result is None or "1 to 1" in text


def test_hud_worker_sends_events():
    from vcoach.hud import HudWorker

    class FakeReader:
        def ammo_health(self, frame):
            return 25, 100

        def score(self, frame):
            return (2, 1) if frame == "game" else None

        def banner(self, frame):
            return "BUY PHASE" if frame == "game" else None

        def top_left(self, frame):
            return "PEARL SWIFTPLAY"

        def agent_panel(self, frame):
            return "SENTINEL CYPHER"

    saved = []

    class FakeRecorder:
        def save_hud(self, frame, ammo, health, spectating=None, shot_error=None):
            saved.append("hud")

        def save_scene(self, frame, values):
            saved.append(sorted(values))

    w = HudWorker(FakeReader(), FakeRecorder(), min_period=0.01)
    w.start()
    w.publish(1.0, "game")
    _time.sleep(0.3)
    w.publish(2.0, "menu")
    _time.sleep(0.3)
    w.stop()
    w.join(1)
    kinds = [(k, v) for k, _, v in w.drain()]
    assert ("hud", (25, 100, None, None)) in kinds and ("score", (2, 1)) in kinds and ("banner", "BUY PHASE") in kinds
    assert ("screen", "PEARL SWIFTPLAY") not in kinds     # a score was read less than 3 seconds before
    assert "hud" in saved and ["banner", "score"] in saved


# ---------- fast digit reader ----------

def _digit_pictures():
    import glob
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    for path in sorted(glob.glob(os.path.join(folder, "hud_*.png"))):
        health, ammo = os.path.basename(path).split("_")[1:3]
        yield path, (None if health == "None" else int(health)), (None if ammo == "None" else int(ammo))


def test_digit_reader_reads_real_hud_pictures():
    import cv2
    from vcoach.digits import DigitReader
    reader = DigitReader()
    count = 0
    for path, health, ammo in _digit_pictures():
        img = cv2.imread(path)
        # Each picture has the health box (78 px), a gap (8 px), then the ammo box.
        assert reader.read(img[:, :78], 34)[0] == health, path
        assert reader.read(img[:, 86:], 34)[0] == ammo, path
        count += 1
    assert count >= 20


def test_digit_reader_reads_other_screen_sizes():
    import cv2
    from vcoach.digits import DigitReader
    reader = DigitReader()
    for path, health, ammo in _digit_pictures():
        img = cv2.imread(path)
        for scale in (0.667, 1.333, 2.0):
            method = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            big = cv2.resize(img[:, 86:], None, fx=scale, fy=scale, interpolation=method)
            got = reader.read(big, 34 * scale)[0]
            assert got in (ammo, None), (path, scale, got)


def test_digit_reader_returns_none_for_no_digits():
    import numpy as np
    from vcoach.digits import DigitReader
    reader = DigitReader()
    rng = np.random.default_rng(3)
    assert reader.read(np.zeros((52, 70, 3), np.uint8), 34) == (None, 0.0)
    assert reader.read(np.full((52, 70, 3), 255, np.uint8), 34) == (None, 0.0)
    for _ in range(20):
        noise = rng.integers(0, 255, (52, 70, 3)).astype(np.uint8)
        assert reader.read(noise, 34)[0] is None


def test_digit_reader_is_fast():
    import time
    import cv2
    from vcoach.digits import DigitReader
    reader = DigitReader()
    img = cv2.imread(next(_digit_pictures())[0])
    start = time.perf_counter()
    for _ in range(50):
        reader.read(img[:, 86:], 34)
    assert (time.perf_counter() - start) / 50 < 0.02


# ---------- mix personality ----------

def test_mix_changes_personality_each_round_with_no_announcement():
    s, v = make_session("mix")
    assert s.picker.is_mix
    assert not any("Your coach this match" in line for line in v.said)
    names = [s.coach.lines["name"]]
    for t in (240, 241):
        s.scene("score", t, (0, 0))
    score = [0, 0]
    t = 300
    for n in range(6):
        score[n % 2] += 1
        for dt in (0, 1, 6):
            s.scene("score", t + dt, tuple(score))
        names.append(s.coach.lines["name"])
        t += 60
    assert s.coach.round_no == 6
    assert all(a != b for a, b in zip(names, names[1:]))      # a new personality after every round
    assert len(set(names)) >= 3
    assert not any("Your coach" in line or name in line for line in v.said for name in set(names))


def test_bruce_can_switch_to_mix():
    from vcoach.commands import parse
    assert parse("switch to mix", ["calm", "hype"]) == ("personality", "mix")


# ---------- death without a health 0 reading ----------

def test_death_when_hud_hides_after_damage():
    from vcoach.checks import Analyzer
    a = Analyzer(CFG)
    t = 100.0
    for hp in (100, 100, 60, 60, 20, 20):
        a.on_hud(t, 25, hp)
        t += 0.07
    assert a.health.alive
    for _ in range(25):            # 1.75 s with no ammo and no health
        a.on_hud(t, None, None)
        t += 0.07
    assert not a.health.alive and a.round.deaths == 1


def test_no_death_when_hud_hides_with_no_damage():
    from vcoach.checks import Analyzer
    a = Analyzer(CFG)
    t = 100.0
    for _ in range(6):
        a.on_hud(t, 25, 100)
        t += 0.07
    for _ in range(60):            # A menu or the map: no damage first
        a.on_hud(t, None, None)
        t += 0.07
    assert a.health.alive and a.round.deaths == 0


# ---------- ElevenLabs ----------

def test_eleven_off_by_default_and_without_key(tmp_path, monkeypatch):
    from vcoach import eleven
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert eleven.from_config({"elevenlabs": {"enabled": False}}, quiet=True) is None
    cfg = {"elevenlabs": {"enabled": True, "voice_id": "abc"}, "ai": {"secrets_file": str(tmp_path / "none.yaml")}}
    assert eleven.from_config(cfg, quiet=True) is None
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    assert eleven.from_config(cfg, quiet=True) is not None


def test_eleven_voice_per_personality_and_cache(tmp_path, monkeypatch):
    from vcoach import eleven
    calls = []

    def fake_request(url, key, body=None, timeout=8):
        calls.append((url, body))
        return b"\x01\x00" * 500

    monkeypatch.setattr(eleven, "_request", fake_request)
    v = eleven.ElevenVoice({"voice_id": "base", "voices": {"savage": "mean"}}, "k", cache_dir=str(tmp_path))
    assert v.voice_for("calm") == "base" and v.voice_for("savage") == "mean"
    v.audio("Nice shot.", "base", 180)
    v.audio("Nice shot.", "base", 180)      # From the cache: no second request
    assert len(calls) == 1
    assert "/text-to-speech/base?output_format=pcm_22050" in calls[0][0]
    assert calls[0][1]["voice_settings"]["speed"] == 1.0
    assert eleven.speed_from_rate(300) == 1.2 and eleven.speed_from_rate(100) == 0.7


def test_voice_uses_windows_when_eleven_fails():
    from vcoach.eleven import ElevenError
    from vcoach.voice import Voice

    spoken = []

    class FakeBackend:
        name = "fake"

        def apply(self, rate, name):
            pass

        def speak(self, text):
            spoken.append(text)

    class BadEleven:
        def voice_for(self, p):
            return "x"

        def audio(self, text, voice_id, rate):
            raise ElevenError("HTTP 401", fatal=True)

    v = Voice(backends=[FakeBackend], eleven=BadEleven())
    v.say("one")
    v.say("two")
    v.wait()
    assert spoken == ["one", "two"] and v.eleven is None


def test_personality_has_key():
    from vcoach.personality import load
    assert load("savage")["key"] == "savage"


def test_spectating_counts_a_death_and_ignores_the_teammate_hud():
    from vcoach.checks import Analyzer
    a = Analyzer(CFG)
    t = 100.0
    for _ in range(4):
        a.on_hud(t, 25, 100)
        t += 0.07
    a.on_hud(t, 25, 100, spectating=True)       # One-shot headshot: no damage reading first
    assert not a.health.alive and a.round.deaths == 1
    for ammo in (30, 28, 26, 24, 22):            # The teammate shoots
        t += 0.07
        a.on_hud(t, ammo, 80, spectating=True)
    assert a.round.bursts == 0 and a.round.deaths == 1


def test_switch_player_detector_on_real_pictures():
    import cv2
    import numpy as np
    from vcoach.digits import DigitReader
    reader = DigitReader()
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    dead = cv2.imread(os.path.join(folder, "spectate_yes.png"))
    alive = cv2.imread(os.path.join(folder, "spectate_no.png"))
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[835:895, 80:330] = dead
    assert reader.spectating(frame) is True
    frame[835:895, 80:330] = alive
    assert reader.spectating(frame) is False
    small = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
    assert reader.spectating(small) is False


def test_shooting_error_stat_decides_moving_shots():
    from vcoach.checks import Analyzer
    a = Analyzer(CFG)
    t = 100.0
    # Stat on screen. Burst 1: still (0.25 degrees). Burst 2: moving (2.5 degrees).
    for ammo, err in [(25, 0.0), (25, 0.0), (24, 0.25), (23, 0.25), (23, 0.0), (23, 0.0), (23, 0.0),
                      (23, 0.0), (23, 0.0), (22, 2.5), (21, 2.5), (21, 0.0), (21, 0.0), (21, 0.0), (21, 0.0)]:
        a.on_frame(t, 5.0, {"visible": False, "head_offset": None})   # The motion guess says "moving" every time
        a.on_hud(t, ammo, 100, shot_error=err)
        t += 0.07
    assert a.round.bursts == 2 and a.round.judged_bursts == 2 and a.round.moving_bursts == 1


def test_shooting_error_reader_on_real_picture():
    import cv2
    import numpy as np
    from vcoach.digits import DigitReader
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[0:26, 0:400] = cv2.imread(os.path.join(folder, "shooting_error_190.png"))
    reader = DigitReader()
    assert reader.shooting_error(frame) == 1.9
    assert DigitReader().shooting_error(np.zeros((1080, 1920, 3), np.uint8)) is None
