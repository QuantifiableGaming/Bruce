import csv
import os
from datetime import datetime

from . import history
from .ai import AIClient, AIVoiceWriter, build_request, load_secrets, system_prompt
from .checks import Analyzer
from .commands import CREATOR, HELP_TEXT, parse
from .util import coach_name
from .engine import Coach
from .personality import PersonalityPicker, adult_allowed, available, is_adult, load

ADULT_OFF = ("The 18 plus coach is off. Turn it on in the Control Panel, Settings, Coach. "
             "It swears, so it is not safe for streams.")
from .vision import EnemyDetector, MotionMeter


STAT_TIP = ("Quick setup tip. In Valorant, go to Settings, Video, Stats, and show the Shooting Error text. "
            "Then I can see how accurate your first shots are.")

class Report:
    def __init__(self, out_dir="reports", show_crosshair=True):
        self.out_dir = out_dir
        self.show_crosshair = show_crosshair
        self.surrender = False
        self.mode = None            # The game mode name for the report ("Swiftplay").
        self.marks = []   # Clips: (clock, round, score, moment). Moment: "Marked", "Replay", "ACE", "CLUTCH"...
        self.creator_name = ""
        self.rows = []
        self.started = datetime.now()

    def add(self, n, won, credit, stats, message):
        self.rows.append([n, won, credit, stats.summary(), message])
        return len(self.rows) - 1

    def set_message(self, index, message):
        if 0 <= index < len(self.rows):
            self.rows[index][4] = message

    def write(self, result=None, closing=None, personality=None, score=None):
        if not self.rows:
            return None
        os.makedirs(self.out_dir, exist_ok=True)
        path = os.path.join(self.out_dir, f"match_{self.started:%Y%m%d_%H%M%S}.md")
        lines = [f"# Match report {self.started:%Y-%m-%d %H:%M}", ""]
        if result or score:
            sur = ""
            if self.surrender:
                sur = " (the other team surrendered)" if result == "VICTORY" else " (your team surrendered)"
            lines += [f"Result: {result or 'unknown'}" + (f" {score}" if score else "") + sur, ""]
        if getattr(self, "mode", None):
            lines += [f"Mode: {self.mode}", ""]
        if personality:
            lines += [f"Coach personality: {personality}", ""]
        if closing:
            lines += [f"Coach: {closing}", ""]
        heads = ["Round", "Result", "Deaths"]
        if self.show_crosshair:
            heads += ["Fights", "Crosshair median (px, + = low)"]
        heads += ["Bursts", "Moving", "Counter-strafes", "Long sprays", "Bad reloads", "Coach said"]
        lines += ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
        for n, won, credit, s, msg in self.rows:
            res = ("Won" if won else "Lost") + (f" ({credit})" if credit else "")
            cells = [n, res, s.get("deaths", "")]
            if self.show_crosshair:
                cells += [s["fights"], "" if s["crosshair_median"] is None else s["crosshair_median"]]
            stops = f'{s.get("counter_stops", 0)} of {s["stops"]}' if s.get("stops") else ""
            cells += [s["bursts"], s["moving_bursts"], stops, s["long_sprays"], s["bad_reloads"], msg or ""]
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        if not self.show_crosshair:
            lines += ["", "The crosshair check is off, so the report has no crosshair columns. "
                          "To try it, set checks.crosshair.enabled to true in config.yaml."]
        if self.marks:
            lines += ["", CLIPS_HEADING, "",
                      "The clock time helps you find each moment in your recording. Each line has a starter caption and hashtags.", "",
                      "| Clock time | Round | Score | Moment | Caption | Hashtags |", "|---|---|---|---|---|---|"]
            for i, m in enumerate(self.marks):
                clock, rnd, sc = m[:3]
                moment = m[3] if len(m) > 3 else "Marked"
                caption, tags = shorts_caption(moment, i, self.creator_name)
                lines.append(f"| {clock} | {rnd} | {sc} | {moment} | {caption} | {tags} |")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path


CLIPS_HEADING = "## Clips to post"
NO_ENEMY = {"visible": False, "head_offset": None, "pixels": 0}

# Starter captions for the Shorts pack. The AI review writes better ones when an AI is on.
SHORTS = {
    "ACE": (["One round. Five kills. No notes.", "Ace. That's the whole caption.", "Five for five. Clip of the night."],
            "#valorant #ace #gamingclips #shorts"),
    "TEAM ACE": (["Full team wipe. Everyone ate.", "Team ace. This is what comms do.", "Five down, zero stress."],
                 "#valorant #teamace #gamingclips #shorts"),
    "CLUTCH": (["Down bad, then this happened.", "The clutch nobody expected.", "Heart rate: yes. Round: won."],
               "#valorant #clutch #gamingclips #shorts"),
    "FLAWLESS": (["Zero deaths. Flawless round.", "Not one of us went down.", "Clean. Every. Time."],
                 "#valorant #flawless #gamingclips #shorts"),
    "THRIFTY": (["Won it on a budget.", "Eco round, full win.", "Pistols beat rifles today."],
                "#valorant #thrifty #gamingclips #shorts"),
    "Replay": (["Watch that again.", "Run it back.", "One more time, slower."], "#valorant #highlights #gamingclips #shorts"),
    "Marked": (["Wait for it...", "You have to see this round.", "Did that just happen?"], "#valorant #gaming #gamingclips #shorts"),
}


def shorts_caption(moment, index=0, name=""):
    """A starter caption and hashtags for one clip. The same clip always gets the same caption."""
    lines, tags = SHORTS.get(moment, SHORTS["Marked"])
    return lines[index % len(lines)], tags


def load_creator_lines():
    from .util import load_yaml

    return load_yaml(os.path.join(os.path.dirname(os.path.abspath(__file__)), "creator_lines.yaml"))


def rate_round(won, credit, stats, checks, reasons):
    """A score out of 10 for one round, and a short reason."""
    score = 5 + (2 if won else -1)
    why = [reasons["won"] if won else reasons["lost"]]
    if credit:
        score += 2
        why.append(reasons["credit"].format(credit=str(credit).lower()))
    if stats.deaths == 0 and stats.bursts:
        score += 1
        why.append(reasons["no_death"])
    issues = stats.issues(checks)
    if "moving_shots" in issues:
        score -= 1
        why.append(reasons["moving"])
    if "long_spray" in issues:
        score -= 1
        why.append(reasons["long_spray"])
    if "no_counter_strafe" in issues:
        why.append(reasons["no_counter"])
    if "bad_reload" in issues:
        score -= 1
        why.append(reasons["bad_reload"])
    if not stats.bursts and not credit:
        why.append(reasons["quiet"])
    return max(1, min(10, score)), " ".join(why[:2])


def parse_hotkey(spec):
    """The keys of a combination such as "alt+f10". Raises ValueError for an unknown key."""
    from pynput.keyboard import Key

    names = {"alt": Key.alt, "ctrl": Key.ctrl, "control": Key.ctrl, "shift": Key.shift, "win": Key.cmd,
             "windows": Key.cmd, "cmd": Key.cmd}
    keys = []
    for part in spec.lower().replace(" ", "").split("+"):
        if part in names:
            keys.append(names[part])
        elif hasattr(Key, part):
            keys.append(getattr(Key, part))          # f1 ... f12, home, end and so on
        elif len(part) == 1:
            keys.append(part)
        else:
            raise ValueError(f"unknown key '{part}'")
    return keys


def press_hotkey(spec):
    """Press a key combination such as "alt+f10" (NVIDIA instant replay) or "win+alt+g" (Xbox Game Bar)."""
    from pynput.keyboard import Controller

    keys = parse_hotkey(spec)
    kb = Controller()
    for k in keys:
        kb.press(k)
    for k in reversed(keys):
        kb.release(k)


SUMMARY_REMINDER = ("Before you close me, open the SUMMARY tab for 5 seconds, "
                    "so I can read your scoreboard for the report.")
SUMMARY_GOT = ("Got your scoreboard: combat score {score}, {kills} kills, {deaths} deaths, {assists} assists. "
               "It is in the report.")
SUMMARY_UNREAD = "I can see the scoreboard, but I cannot read your numbers. I saved a picture in the logs folder."
SUMMARY_REMINDERS = 3      # The reminder stops after the coach has read this many scoreboards.


def _state_path(cfg):
    return os.path.join(cfg.get("reports_dir", "reports"), "coach_state.json")


def load_state(cfg):
    import json
    try:
        with open(_state_path(cfg), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(cfg, state):
    import json
    try:
        os.makedirs(os.path.dirname(_state_path(cfg)) or ".", exist_ok=True)
        with open(_state_path(cfg), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError as exc:
        print(f"[coach] Could not save {_state_path(cfg)}: {exc}")


def scoreboard_markdown(n):
    kda = f"{n['kills']} / {n['deaths']} / {n['assists']}"
    cells = [n["score"], kda] + [n.get(k, "") for k in ("trades", "first_bloods", "plants", "defuses")]
    return ("\n## Your scoreboard\n\n"
            "| Combat score | Kills / deaths / assists | Trades | First bloods | Plants | Defuses |\n"
            "|---|---|---|---|---|---|\n| " + " | ".join(str(c) for c in cells) + " |\n")


class Session:
    """Connects capture signals, the analyzer, the coach, the voice and the report."""

    def __init__(self, cfg, picker, voice, log_path=None, ai=None, banner_log=None, inputs=None, obs=None, chat=None):
        self.cfg = cfg
        self.chat = chat               # twitch_chat.TwitchChat, or None
        self.votes = None
        self.last_chat_reply = -1e9
        self.obs = obs                 # obs_replay.ObsReplay, or None (instant replay off)
        self.voice = voice
        self.picker = picker
        self.last_said = None
        self.recent = []
        self.banner_log = None
        self.last_banner = None
        if banner_log:
            os.makedirs(os.path.dirname(banner_log) or ".", exist_ok=True)
            self.banner_log = open(banner_log, "w", encoding="utf-8")
            self.banner_log.write("Every text the coach read in the Round banner box (time, ammo, health, text).\n")
        self.meter = MotionMeter(max_turn_px=cfg["checks"]["moving_shots"]["max_turn_px"])
        self.crosshair_on = bool(cfg["checks"]["crosshair"].get("enabled", True))   # Off: no enemy search.
        self.detector = EnemyDetector(cfg)
        self.analyzer = Analyzer(cfg, inputs=inputs)
        self.analyzer.on_shot = self._challenge_shot
        self.challenge = None          # challenge.Challenge while a Range challenge runs
        self.clock_phase = None
        self.last_auto_clip = None
        self.speech_cut = False
        self.gun_time = 0.0            # Seconds with your gun out, for the Shooting Error stat check.
        self.gun_last_t = None
        self.stat_tip = "due_never"    # "due_never", "due", or "said"
        first = picker.next()
        if first.get("adult") and not adult_allowed(cfg):
            print("[coach] " + ADULT_OFF + " The coach uses Calm.")
            picker = PersonalityPicker("calm")
            first = picker.next()
        self.picker = picker
        self.coach = Coach(first, cfg)
        self._apply_personality()
        self.report = Report(cfg.get("reports_dir", "reports"),
                             show_crosshair=cfg["checks"]["crosshair"].get("enabled", True))
        self.last_round_end_t = None
        self.last_match = None
        self.last_round = None
        self.voice.allowed = lambda: self.speech_allowed(self.now)
        self.creator = load_creator_lines()
        self.now = 0.0
        self.pending = []
        vc = cfg.get("voice_control", {}) or {}
        self.follow_up_s = float(vc.get("follow_up_s", 8))      # 0: off
        self.follow_up_due = False     # main.py opens the follow-up window when the answer has been spoken
        self.talk_mode = "coach"       # "chat" after "Hey Bruce"
        from collections import deque
        self.talk = deque(maxlen=int((cfg.get("ai", {}) or {}).get("conversation_memory", 3)) or 1)
        from .profile import PlayerProfile
        self.profile_on = bool((cfg.get("ai", {}) or {}).get("player_profile", True))
        self.profile = PlayerProfile(str((cfg.get("ai", {}) or {}).get("profile_file", "player_profile.md")))
        self.ai = ai if ai is not None else self._make_ai()
        from .analysis import Analyst

        ai_cfg = cfg.get("ai", {}) or {}
        self.analyst = Analyst(self.ai.client, float(ai_cfg.get("analysis_timeout_s", 90)))
        if self.ai.enabled and self.ai.kinds == set():
            print(f"AI match review on: {self.ai.client.provider} ({self.ai.client.model}). No spoken AI lines.")
        elif self.ai.enabled:
            print(f"AI coach on: {self.ai.client.provider} ({self.ai.client.model}).")
        elif cfg.get("ai", {}).get("provider", "off") != "off":
            print(f"AI coach off: {self.ai.client.reason} The coach uses scripted lines.")
        self.log = None
        if log_path:
            self._log_file = open(log_path, "w", newline="", encoding="utf-8")
            self.log = csv.writer(self._log_file)
            self.log.writerow(["t", "kind", "motion", "enemy_pixels", "head_offset", "ammo", "health", "banner"])

    def frame(self, t, frame):
        if self.log:
            motion = self.meter.update(frame)        # A tuning log wants the score of every frame.
        else:
            # Optical flow is slow (about 11 ms a frame). Keep small frames, and measure only
            # around a shot that the Shooting Error stat and the keys cannot judge.
            if not self.analyzer._keys_on():      # With the key reader on, the keys judge each shot.
                self.meter.push(t, frame)
                self.analyzer.motion_lookup = self.meter.between
            motion = None
        enemy = self.detector.detect(frame) if self.crosshair_on else NO_ENEMY
        self.analyzer.on_frame(t, motion, enemy)
        if self.log:
            ho = "" if enemy["head_offset"] is None else round(enemy["head_offset"], 1)
            self.log.writerow([round(t, 3), "frame", "" if motion is None else round(motion, 3), enemy["pixels"], ho, "", "", ""])

    def shot_error(self, t, value):
        """A Shooting Error reading (on every frame)."""
        self.analyzer.on_shot_error(t, value)

    def hud(self, t, ammo, health, banner, spectating=None, shot_error=None):
        self.now = t
        self._cut_speech(t)
        if self.banner_log and banner and banner != self.last_banner:
            self.last_banner = banner
            self.banner_log.write(f"{datetime.now():%H:%M:%S}  ammo={ammo} health={health}  {banner}\n")
            self.banner_log.flush()
        if self.log:
            self.log.writerow([round(t, 3), "hud", "", "", "", ammo, health, banner or ""])
        self._check_stat_shown(t, ammo)
        self._handle(t, self.analyzer.on_hud(t, ammo, health, banner, spectating, shot_error))

    def scene(self, kind, t, value):
        """Handle a score, banner, screen or agent reading from the screen reader."""
        self.now = t
        self._cut_speech(t)
        if kind == "scoreboard":
            return self._scoreboard(t, value)
        if kind == "banner" and self.banner_log and value and value != self.last_banner:
            self.last_banner = value
            self.banner_log.write(f"{datetime.now():%H:%M:%S}  {value}\n")
            self.banner_log.flush()
        self._handle(t, self.analyzer.on_scene(kind, t, value))
        phase = self.analyzer.match.clock.phase
        if kind == "timer" and phase != self.clock_phase:
            self.clock_phase = phase
            if self.analyzer.match.in_match:
                print({"live": "[timer] The round is live.", "buy": "[timer] Buy phase.",
                       "post": "[timer] The round is over."}.get(phase, "[timer] Timer lost."))

    def _mark_surrender(self):
        """A late "SURRENDERED" read: add it to the result line of the report just saved."""
        path = (self.last_match or {}).get("report")
        if not path or not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            text = f.read()
        lines = text.split("\n")
        before = self.last_match.get("extra_round")
        for i, line in enumerate(lines):
            if line.startswith("Result:") and "surrendered" not in line:
                if before:
                    # The round counted at the end was not played: the match ended by a surrender.
                    fixed = f"{before[0]}-{before[1]}"
                    line = line.replace(self.last_match.get("final", ""), fixed)
                    self.last_match["extra_round"] = None
                    if self.last_match.get("in_history"):
                        try:
                            history.update_last({"won": before[0], "lost": before[1]}, self.last_match["history"])
                        except OSError:
                            pass
                    print(f"[match] Surrender: the score is {fixed}, not {self.last_match.get('final')}.")
                lines[i] = line + (" (the other team surrendered)" if "VICTORY" in line else " (your team surrendered)")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                print("[match] The match ended by a surrender.")
                break

    def _scoreboard(self, t, board):
        """Your line from the end-of-match scoreboard. Add it to the last report and the history.

        The same numbers must show in 2 of the last 5 readings, so one misread number is not saved.
        (Before 19 Nov it had to be 2 readings in a row, and one misread digit started again.)
        """
        last = self.last_match
        if not last or last.get("board_saved") or t - last["t"] > 600:
            return
        if "unread" in board:
            if not last.get("board_unread_said"):
                last["board_unread_said"] = True
                print(f"[scoreboard] Your line is on the screen, but its numbers could not be read: "
                      f"'{board['unread']}'. A picture is in logs\\scoreboard_unread.png.")
                self._say(SUMMARY_UNREAD)
            return
        numbers = {k: v for k, v in board.items() if k != "row"}
        seen = last.setdefault("board_reads", [])
        seen.append(numbers)
        del seen[:-5]
        if seen.count(numbers) < 2:
            return
        last["board_saved"] = True
        print(f"[scoreboard] Your line: {numbers}")
        self._say(SUMMARY_GOT.format(score=numbers["score"], kills=numbers["kills"],
                                     deaths=numbers["deaths"], assists=numbers["assists"]))
        state = load_state(self.cfg)
        state["scoreboards_read"] = int(state.get("scoreboards_read", 0)) + 1
        save_state(self.cfg, state)
        if last.get("report"):
            try:
                with open(last["report"], "a", encoding="utf-8") as f:
                    f.write(scoreboard_markdown(numbers))
            except OSError as exc:
                print(f"[scoreboard] Could not add it to the report: {exc}")
        if last.get("history"):
            try:
                # Scoreboard names start with "board_", so they never replace the coach's own counts.
                history.update_last({"board_" + k: v for k, v in numbers.items()}, last["history"])
            except OSError as exc:
                print(f"[scoreboard] Could not add it to the history: {exc}")

    def _summary_reminder_on(self):
        """The end-of-match reminder to open SUMMARY. It stops after SUMMARY_REMINDERS scoreboards,
        or when coach.summary_reminder is false (Settings > Coaching)."""
        if not (self.cfg.get("coach") or {}).get("summary_reminder", True):
            return False
        return int(load_state(self.cfg).get("scoreboards_read", 0)) < SUMMARY_REMINDERS

    def _handle(self, t, events):
        for kind, data, stats in events:
            if kind == "round_end":
                msg = self.coach.round_end(data["won"], data["credit"], stats, data.get("score"))
                n = self.coach.round_no
                score = f" | score {self.coach.won}-{self.coach.lost}" if data.get("score") else ""
                credit = f" ({data['credit']})" if data["credit"] else ""
                print(f"Round {n}: {'won' if data['won'] else 'lost'}{credit}{score} | {stats.summary()}")
                row = self.report.add(n, data["won"], data["credit"], stats, msg[1] if msg else None)
                self.last_round = (data["won"], data["credit"], stats)
                if data["won"] and data["credit"]:
                    self._auto_clip(t, data["credit"], stats)
                if msg:
                    kind = msg[0]
                    extra = f"Highlight: {data['credit']}." if data["credit"] else None
                    report = self.report
                    self._speak(kind, msg[1], stats=stats, issue=self.coach.last_issue, extra=extra,
                                after=lambda text, r=row, rep=report: rep.set_message(r, text))
                self.last_round_end_t = t
                if self.obs:
                    self.obs.round_over()      # Replays saved during the round play now.
                for intent, question in self.pending:  # Answers to questions asked during the round.
                    self._reply(intent, question)
                self.pending = []
                if self.stat_tip == "due":
                    self._say_stat_tip(t)
                if self.picker.is_mix:             # Mix: a new personality for the next round, with no announcement.
                    self.coach.set_lines(self.picker.next())
                    self._apply_personality()
                self._apply_chat_vote()
            elif kind == "surrender":
                self._mark_surrender()
            elif kind == "match_end":
                self.report.surrender = bool(data.get("surrender"))
                self.end_match(data["result"], score=data.get("score"))
            elif kind == "agent_select":
                if self.analyzer.rounds:           # The last match ended without a results screen.
                    self.end_match(speak=False)
                else:
                    self.coach.new_match()
                print("[match] Agent select.")
                self._speak("agent_select", self.coach.pregame())
            elif kind == "agent":
                print(f"[match] Agent: {data['agent']} ({data['role']}).")
                self._speak("agent", self.coach.agent_line(data["agent"], data["role"]),
                            extra=f"Agent: {data['agent']}. Role: {data['role']}.")
            elif kind == "match_start":
                if self.analyzer.rounds and sum(data["score"]) == 0:
                    self.end_match(speak=False)
                self.coach.sync_score(data["score"])
                self.analyzer.clear_round()
                print(f"[match] Match on. Score {data['score'][0]}-{data['score'][1]}.")
            elif kind == "match_reset":
                if self.analyzer.rounds:
                    self.end_match(speak=False)
            elif kind == "score_sync":
                self.coach.sync_score(data["score"])
                print(f"[match] Score now {data['score'][0]}-{data['score'][1]}.")

    def _check_stat_shown(self, t, ammo):
        """Look for the Shooting Error stat. If it is off the screen for a while, ask for it once.

        Time counts only with your gun out. Seeing the stat starts the count again, so turning
        the stat off in the middle of a session also gives the tip. The tip plays at once when
        speech is allowed (Range, menus, buy phase), else at the next round end.
        """
        after = float(self.cfg["checks"]["moving_shots"].get("stat_check_after_s", 60))
        if self.stat_tip == "due" and self.speech_allowed(t):
            self._say_stat_tip(t)
            return
        if self.stat_tip != "due_never" or after <= 0:
            return
        seen = self.analyzer.shot_error_seen_t
        if seen is not None and t - seen < 2.0:
            self.gun_time = 0.0
            self.gun_last_t = None
            return
        if ammo is None:
            self.gun_last_t = None
            return
        if self.gun_last_t is not None:
            self.gun_time += min(0.5, max(0.0, t - self.gun_last_t))
        self.gun_last_t = t
        if self.gun_time >= after:
            self.stat_tip = "due"
            print("[stat] The Shooting Error stat is not on the screen. Turn it on in Valorant: "
                  "Settings > Video > Stats > Shooting Error (show the text). "
                  "The coach then reads how accurate each first shot was.")

    def _cut_speech(self, t):
        """Stop the voice when the round is live. Write the log line once for each cut."""
        if self.speech_allowed(t):
            self.speech_cut = False
        elif self.voice.busy() and not self.speech_cut:
            self.speech_cut = True
            print("[voice] The round is live: speech stopped.")
            self.voice.stop()

    def _say_stat_tip(self, t):
        self.stat_tip = "said"
        self._say(STAT_TIP)

    def _apply_personality(self):
        lines = self.coach.lines
        c = self.cfg["coach"]
        voice = lines.get("voice", {})
        self.voice.set_style(c.get("voice_rate") or voice.get("rate", 180),
                             c.get("voice_name") or voice.get("name_contains", ""), lines.get("key"))
        print(f"Personality: {lines.get('name', '?')}")
        if self.picker.is_random and not self.picker.is_mix:
            self._say(f"Your coach this match: {lines.get('name', 'Coach')}.")

    def end_match(self, result=None, next_match=True, score=None, speak=True):
        closing = None
        # The last round ends on the VICTORY or DEFEAT screen, so the score at the top never shows
        # it (19 Nov, Swiftplay: "DEFEAT 0-4" for a 0-5 loss). When a round went live more than 5 s
        # before the end and has no end, count it for the side that won. A surrender in the buy
        # phase ends before the round goes live, so it gets no extra round.
        clock = self.analyzer.match.clock
        played = clock.phase in ("buy", "live") and clock.live_t is not None and self.now > clock.live_t + 5
        extra_round = None
        if result in ("VICTORY", "DEFEAT") and played and not self.report.surrender and self.analyzer.rounds:
            if score:
                self.coach.sync_score(score)
            extra_round = (self.coach.won, self.coach.lost)       # the score before: for a late surrender
            if result == "VICTORY":
                self.coach.won += 1
            else:
                self.coach.lost += 1
            score = (self.coach.won, self.coach.lost)
            print(f"[match] The last round ended on the {result} screen: counted it "
                  f"({self.coach.won}-{self.coach.lost}). A surrender found later takes it back.")
        if self.analyzer.rounds:
            w, l = self.coach.won, self.coach.lost
            # No result on the screen, and the score is not a final score (13, or 2 ahead in overtime):
            # the coach was stopped in the middle of the match. 17 Nov: a restart at 4-1 saved
            # "VICTORY 4-1" and counted it in the history.
            finished = max(w, l) >= 13 and abs(w - l) >= 2 or (max(w, l) >= 13 and min(w, l) < 12)
            stopped = not result and not finished
            closing = self.coach.debrief(self.analyzer.rounds, result, score)
            if stopped:
                result = "STOPPED"
                closing = (f"The coach was stopped at {w}-{l}, before the match ended. "
                           "This report has the rounds up to then. It is not in your match history.")
            elif not result and w != l:
                result = "VICTORY" if w > l else "DEFEAT"
            final = f"{self.coach.won}-{self.coach.lost}"
            print(f"[match] Match over: {result or 'result unknown'} {final}.")
            name = "Mix" if self.picker.is_mix else self.coach.lines.get("name")
            mode = getattr(self.analyzer.match, "mode", None)
            entry = history.summarize(self.analyzer.rounds, result, self.coach.won, self.coach.lost, name, mode=mode)
            self.report.mode = history.mode_name(mode) if mode else None
            hist_path = os.path.join(self.report.out_dir, "history.jsonl")
            try:
                change = None
                if not stopped:                  # A part of a match would spoil the trends.
                    change = history.trend(entry, history.load(hist_path))
                    history.add(entry, hist_path)
            except OSError as exc:
                change = None
                print(f"[history] Could not save the match history: {exc}")
            if change:
                closing = f"{closing} {change}"
            remind = (not stopped) and self._summary_reminder_on()
            if speak and next_match:
                self._speak("debrief", closing, max_words=70,
                            extra=f"Result: {result or 'unknown'}. Final score {final}. Scripted debrief: {closing}",
                            after=(lambda _text: self._say(SUMMARY_REMINDER)) if remind else None)
            else:
                print(f"[coach] (not spoken) {closing}")
            path = self.report.write(result, closing, "Mix" if self.picker.is_mix else self.coach.lines.get("name"), final)
            if path:
                print(f"Report saved: {path}")
                ai_cfg = self.cfg.get("ai", {}) or {}
                if ai_cfg.get("post_match_analysis", True) and self.ai.client.enabled:
                    # Wait a minute for the scoreboard numbers, then ask the AI for a written review.
                    self.analyst.start(path, hist_path, delay_s=float(ai_cfg.get("analysis_delay_s", 60)) if next_match else 0)
            # The scoreboard shows after the match. Its numbers are added to this report later.
            self.last_match = {"t": self.now, "report": path, "history": hist_path,
                               "extra_round": extra_round, "final": final, "in_history": not stopped}
        self.coach.new_match()
        self.analyzer.new_match()
        self.talk.clear()
        self.report = Report(self.cfg.get("reports_dir", "reports"),
                             show_crosshair=self.cfg["checks"]["crosshair"].get("enabled", True))
        self.last_round_end_t = None
        if next_match and self.picker.is_random:
            self.coach.set_lines(self.picker.next())
            self._apply_personality()

    # ---------- voice commands ----------

    def _say(self, text):
        self.last_said = text
        self.recent = (self.recent + [text])[-6:]
        self.voice.say(text)

    def _make_ai(self):
        ai_cfg = self.cfg.get("ai", {}) or {}
        client = AIClient(ai_cfg, load_secrets(ai_cfg.get("secrets_file", "secrets.yaml")))
        kinds = None
        use_for = ai_cfg.get("use_for")
        from .ai import DEFAULTS

        if DEFAULTS.get(client.provider, {}).get("review_only"):
            use_for = []                              # This model writes the match review only.
        if use_for is not None:                       # An empty list: no spoken AI lines.
            groups = {"tips": {"tip", "focus"}, "mental": {"reset", "credit", "agent_select", "agent"},
                      "answers": {"answer"}, "match_end": {"match_end", "debrief"}}
            kinds = set().union(*(groups.get(str(g), set()) for g in use_for))
        return AIVoiceWriter(client, max_words=int(ai_cfg.get("max_words", 25)),
                             max_wait_s=float(ai_cfg.get("timeout_s", 4)) * 2, kinds=kinds)

    def _speak(self, kind, fallback, stats=None, issue=None, question=None, extra=None, after=None,
               max_words=None, talk=None):
        """Say a coaching line. The AI writes it when it is on. The scripted line is the fallback."""
        def done(text, from_ai=False):
            self._say(text)
            if after:
                after(text)

        if not self.ai.wants(kind):
            done(fallback)
            return
        lines = self.coach.lines
        profile = self.profile.facts() if self.profile_on and kind in ("chat", "answer") else None
        user = build_request(kind, self.coach, lines, stats=stats, issue=issue, question=question,
                             extra=extra, recent=self.recent, talk=talk, profile=profile)
        words = max_words or self.ai.max_words
        self.ai.submit(system_prompt(lines, words, coach_name(self.cfg)), user, fallback, done, max_words=words)

    # A known command goes to the AI in clean words, not as the misheard text
    # (17 Nov: "rate that matt are right that round" gave "Matt called it").
    CLEAN_QUESTIONS = {"rate": "Rate my last round out of ten, with one reason.", "recap": "Give me a recap so far.",
                       "doing": "How am I doing?", "focus": "What should I work on?", "score": "What is the score?",
                       "roast": "Roast my last round.", "hype_me": "Hype me up."}

    def _good_follow_up(self, t, text):
        """A follow-up has no wake word, so drop what is not a real question: short fillers, the coach's
        own voice picked up from speakers, and anything during a live round."""
        words = text.lower().split()
        if not words or text.lower() in self.IGNORE_FOLLOW_UPS or len(words) < 2:
            return False
        if not self.answer_window_open(t):
            return False
        said = set((self.last_said or "").lower().replace(",", " ").replace(".", " ").split())
        if said and sum(w in said for w in words) / len(words) >= 0.6:
            print(f"[heard] '{text}' -> ignored (the coach's own voice)")
            return False
        print(f"[heard] follow-up: '{text}'")
        return True

    def _reply(self, intent, question):
        """Answer a coaching question, with the AI when it is on."""
        if intent in self.CLEAN_QUESTIONS:
            question = self.CLEAN_QUESTIONS[intent]
        fallback = self._answer(intent)
        worst = self._worst_issue()
        extra = f"Main thing to work on now: {self.coach.lines['focus_names'][worst]}." if worst else \
            "No common mistake yet."
        asked = question or intent
        small_talk = intent == "chat"
        self.talk_mode = "chat" if small_talk else "coach"

        def remember(text, q=asked):
            self.talk.append((q, text))
            if self.follow_up_s > 0:
                self.follow_up_due = True        # Listen for a follow-up without the wake word.
        # A chat gets only the score and the talk so far. A coaching answer also gets the last
        # round's numbers, so "rate that round" and "how am I doing" are not a guess.
        self._speak("chat" if small_talk else "answer", fallback, question=asked,
                    extra=None if small_talk else extra,
                    stats=None if small_talk or not self.last_round else self.last_round[2],
                    talk=list(self.talk), after=remember)

    def speech_allowed(self, t):
        """False from 5 seconds after the buy phase ends until the round ends. No speech then, AI lines too."""
        m = self.analyzer.match
        if not m.in_match:
            return True       # agent select, menus, before and after the match
        vc = self.cfg.get("voice_control", {}) or {}
        cutoff = float(vc.get("stop_after_round_start_s", 5))
        by_timer = m.clock.speech_ok(t, cutoff)       # The round timer is the best signal.
        if by_timer is not None:
            return by_timer
        start = m.break_start_t
        if m.buy_t >= start:
            # The banner is read about every 0.5 s, so the last BUY PHASE reading can be 0.5 s late.
            return t <= m.buy_t + cutoff - 0.5
        return t <= start + float(vc.get("silent_after_round_end_s", 25))

    def answer_window_open(self, t):
        if self.analyzer.match.in_buy(t):
            return True  # The buy phase banner is on screen.
        if self.last_round_end_t is None:
            return not self.analyzer.match.in_match  # Menus, agent select, or before the match.
        return t - self.last_round_end_t <= self.cfg["voice_control"]["answer_window_s"]

    def _worst_issue(self):
        counts = dict(self.coach.issue_counts)
        for issue in self.analyzer.round.issues(self.cfg["checks"]):
            counts[issue] = counts.get(issue, 0) + 1
        return max(counts, key=counts.get) if counts else None

    def _answer(self, intent):
        a = self.coach.lines["answers"]
        c = self.coach
        worst = self._worst_issue()
        focus = c.lines["focus_names"][worst] if worst else None
        if intent == "score":
            return a["score"].format(won=c.won, lost=c.lost)
        if intent == "focus":
            return a["focus"].format(focus=focus) if focus else a["focus_none"]
        if intent in CREATOR:
            return self._creator_line(intent)
        if intent == "doing":
            if c.round_no == 0 and not focus:
                return a["doing_no_data"]
            if focus:
                return a["doing_focus"].format(won=c.won, lost=c.lost, focus=focus)
            return a["doing_clean"].format(won=c.won, lost=c.lost)
        return a["unknown"]

    def _creator_name(self):
        return str((self.cfg.get("creator") or {}).get("name") or "").strip() or "you"

    def _creator_line(self, intent):
        """Lines for the content-creator commands (see creator_lines.yaml)."""
        import random

        cl = self.creator
        pick = lambda items: (random.choice(items).replace("{name}", self._creator_name())  # noqa: E731
                              .replace("{coach}", coach_name(self.cfg)))
        c = self.coach
        if intent in ("intro", "outro"):
            return pick(cl[intent])
        if intent == "roast":
            worst = self._worst_issue()
            savage = load("savage")
            if worst:
                return random.choice(savage["tips"][worst])
            if c.loss_streak >= 2:
                return random.choice(savage["reset"])      # The loss lines fit only when you lose.
            return pick(cl["roast_none"]).format(won=c.won, lost=c.lost)
        if intent == "hype_me":
            hype = load("hype")
            return random.choice(hype["reset"] if c.loss_streak else hype["credit"])
        if intent == "rate":
            if not self.last_round:
                return "No round to rate yet. Go play one."
            score, reason = rate_round(*self.last_round, self.cfg["checks"], cl["reasons"])
            band = "top" if score >= 9 else "good" if score >= 6 else "meh" if score >= 4 else "bad"
            return pick(cl["rate"][band]).format(score=score, reason=reason)
        if intent == "recap":
            worst = self._worst_issue()
            fl = cl["focus_line"]["some"].format(focus=c.lines["focus_names"][worst]) if worst else cl["focus_line"]["none"]
            deaths = sum(r.deaths for r in self.analyzer.rounds) + self.analyzer.round.deaths
            return pick(cl["recap"]).format(won=c.won, lost=c.lost, deaths=deaths, focus_line=fl)
        return c.lines["answers"]["unknown"]

    def _replay(self, t):
        """Instant replay on your stream (OBS). It also marks the moment in the report."""
        self._mark(t, press_clip=False, moment="Replay")
        if not self.obs:
            print("[replay] Instant replay is off. Turn it on in the Control Panel: Settings > Stream instant replay (OBS).")
            self._say("Instant replay is off. Turn it on in the Control Panel settings.")
            return
        live = not self.speech_allowed(t)
        if self.obs.request(live) == "later":
            print("[replay] The round is live. The replay plays on stream at the end of the round.")

    # ---------- Twitch chat ----------

    def chat_message(self, t, user, text):
        """One Twitch chat message: !vote <personality> or !coach."""
        tc = self.cfg.get("twitch") or {}
        low = text.lower().strip()
        if low.startswith("!vote") and tc.get("personality_votes", True):
            if self.votes is None:
                from .twitch_chat import Votes

                self.votes = Votes([n for n, _, _ in available()])
            choice = self.votes.add(user, low)
            if choice:
                print(f"[twitch] {user} votes {choice}.")
        elif low.split()[:1] == ["!coach"] and tc.get("stats_command", True) and self.chat:
            if t - self.last_chat_reply < float(tc.get("reply_cooldown_s", 30)):
                return
            from .twitch_chat import stats_line

            self.last_chat_reply = t
            rounds = self.analyzer.rounds + ([self.analyzer.round] if self.analyzer.round.bursts else [])
            line = stats_line(self.coach.won, self.coach.lost, rounds, self.coach.lines.get("name", "Coach"))
            if (self.cfg.get("twitch") or {}).get("personality_votes", True):
                line += " | Vote the next coach: !vote " + "/".join(n for n, _, _ in available())
            self.chat.send(line)

    def _apply_chat_vote(self):
        """At a round end: the personality with the most chat votes takes over."""
        if not self.votes:
            return
        win = self.votes.winner()
        if not win:
            return
        name, count = win
        self.coach.set_lines(PersonalityPicker(name).next())
        self._apply_personality()
        who = self.coach.lines.get("name", name.title())
        print(f"[twitch] Chat picked {who} ({count} vote{'s' if count != 1 else ''}).")
        self._say(f"Chat picked {who}.")
        if self.chat:
            self.chat.send(f"Chat picked {who} with {count} vote{'s' if count != 1 else ''}. Vote again with !vote <name>.")

    def _challenge_command(self, arg):
        from .challenge import Challenge

        if arg == "stop":
            self._say("Challenge stopped." if self.challenge else "No challenge is running.")
            self.challenge = None
            return
        if self.analyzer.match.in_match:
            self._say("The challenge is for the Range. Start it there.")
            return
        cc = self.cfg.get("challenge") or {}
        n = int(cc.get("shots", 20))
        self.challenge = Challenge(n, float(self.cfg["checks"]["moving_shots"].get("error_threshold_deg", 1.0)))
        print(f"[challenge] Started: {n} stops.")
        self._say(f"Challenge on. Strafe, stop, and shoot {n} times. Go.")

    def _challenge_shot(self, keys, stat, keys_on):
        c = self.challenge
        if not c:
            return
        if self.analyzer.match.in_match:
            self.challenge = None               # A match started: the challenge is over.
            return
        if not c.add(keys, stat, keys_on):
            return
        n = len(c.shots)
        print(f"[challenge] {n} of {c.target}: {c.shots[-1]['kind']}")
        if n == c.target // 2 and not c.done:
            self._say("Halfway.")
        if c.done:
            self._finish_challenge()

    def _finish_challenge(self):
        from .challenge import load_results, make_card, save_result, summary_line

        res = self.challenge.result()
        self.challenge = None
        folder = self.report.out_dir
        earlier = load_results(os.path.join(folder, "challenges.jsonl"))
        text = summary_line(res, earlier)
        try:
            save_result(res, folder)
            name = str((self.cfg.get("creator") or {}).get("name") or "")
            card = os.path.join(folder, "challenges", f"challenge_{datetime.now():%Y%m%d_%H%M%S}.png")
            make_card(res, card, name, earlier)
            print(f"[challenge] {text} Card saved: {card}")
        except OSError as exc:
            print(f"[challenge] Could not save the result: {exc}")
        self._say(text)

    def _auto_clip(self, t, word, stats):
        """A won round with a highlight banner (ACE, CLUTCH...): clip it without a voice command.

        The banners also show for the enemy team and for teammates, so the coach clips only
        rounds that your team won. An ACE or a CLUTCH counts only when you did not die in the
        round: if you died, a teammate made the play. The clip comes at the round end, a few
        seconds after the banner. Recorders keep the last 20 seconds or more, so the play is in it.
        """
        cc = self.cfg.get("creator") or {}
        if not cc.get("auto_clip", True) or not self.analyzer.match.in_match:
            return
        if word not in [str(w).upper() for w in cc.get("auto_clip_moments", ["ACE", "TEAM ACE", "CLUTCH"])]:
            return
        if word in ("ACE", "CLUTCH") and stats.deaths:
            print(f"[clip] {word} by a teammate (you died this round): no clip.")
            return
        print(f"[clip] {word}: saved as a clip.")
        self._mark(t, moment=word, round_no=self.coach.round_no)   # The round that just ended.
        mode = str(cc.get("auto_clip_obs", "save"))
        if self.obs and mode == "save":
            self.obs.save_only()
        elif self.obs and mode == "replay":
            self.obs.request(not self.speech_allowed(t))

    def _mark(self, t, press_clip=True, moment="Marked", round_no=None):
        """Save a moment for your video. Optional: press your recording app's clip hotkey."""
        clock = datetime.now().strftime("%H:%M:%S")
        rnd = self.coach.round_no + 1 if round_no is None else round_no
        self.report.marks.append((clock, rnd, f"{self.coach.won}-{self.coach.lost}", moment))
        print(f"[mark] {clock}, round {rnd}, {moment}")
        hotkey = str((self.cfg.get("creator") or {}).get("clip_hotkey") or "").strip()
        if hotkey and press_clip:
            try:
                press_hotkey(hotkey)
            except Exception as exc:
                print(f"[mark] Could not press the clip hotkey '{hotkey}': {exc}")

    FOLLOW_UP = "\x00follow:"            # WakeListener marks a follow-up (no wake word) with this.
    CHAT = "\x00chat:"                   # ... and "Hey Bruce" (chat mode) with this.
    # In chat mode these still work as commands. Anything else is chat.
    CHAT_COMMANDS = {"wake", "mute", "unmute", "help", "repeat", "replay", "challenge", "mark", "personality",
                     "remember", "forget", "profile"}
    IGNORE_FOLLOW_UPS = {"ok", "okay", "yeah", "yes", "no", "thanks", "thank you", "cheers", "cool", "nice", "huh",
                         "right", "alright", "sure", "mm", "the", "a"}

    def command(self, t, text):
        """Handle one spoken command. Text is what came after the wake word."""
        self.now = max(self.now, t)
        chat = False
        if text.startswith(self.FOLLOW_UP):
            text = text[len(self.FOLLOW_UP):].strip()
            if not self._good_follow_up(t, text):
                return None
            chat = self.talk_mode == "chat"    # A follow-up keeps the mode of the talk.
        if text.startswith(self.CHAT):
            text = text[len(self.CHAT):].strip()
            chat = True
        names = [n for n, _, _ in available(include_adult=True)]   # Heard always, then checked below.
        intent, arg = parse(text, names)
        if chat and (self.cfg.get("ai", {}) or {}).get("small_talk", True) and intent not in self.CHAT_COMMANDS:
            intent, arg = "chat", None          # "Hey Bruce, ...": a chat, about anything
        print(f"[heard] '{text}' -> {intent}" + (f" ({arg})" if arg and intent not in ("unknown", "chat") else ""))
        a = self.coach.lines["answers"]
        if intent == "wake":
            self._say(a["wake"])
        elif intent == "mute":
            self._say(a["muted"])
            self.voice.set_muted(True)
        elif intent == "unmute":
            self.voice.set_muted(False)
            self._say(a["unmuted"])
        elif intent == "help":
            self._say(HELP_TEXT.replace("{coach}", coach_name(self.cfg)))
        elif intent == "repeat":
            self.voice.say(self.last_said or a["repeat_none"])
        elif intent == "replay":
            self._replay(t)
        elif intent == "challenge":
            self._challenge_command(arg)
        elif intent in ("remember", "forget", "profile"):
            self._profile_command(intent, arg)
        elif intent == "mark":
            self._mark(t)
            if (self.cfg.get("creator") or {}).get("confirm_mark", True):
                import random

                self._say(random.choice(self.creator["marked"]))
        elif intent in ("intro", "outro"):
            if self.speech_allowed(t):
                self._say(self._creator_line(intent))
            else:
                if intent not in [i for i, _ in self.pending]:
                    self.pending.append((intent, text))
        elif intent == "personality" and is_adult(arg) and not adult_allowed(self.cfg):
            self._say(ADULT_OFF)
        elif intent == "personality":
            self.picker = PersonalityPicker(arg, pool=self.cfg["coach"].get("random_pool"))
            self.coach.set_lines(self.picker.next())
            self._apply_personality()
            if self.picker.is_mix:
                self._say("Mix mode. A different coach each round.")
            elif not self.picker.is_random:
                self._say(self.coach.lines["answers"]["switched"].format(name=self.coach.lines["name"]))
        elif intent in ("unknown", "chat") and not self.ai.wants("answer"):
            self._say(a["unknown"])
        elif self.answer_window_open(t):
            self._reply(intent, text)
        else:
            # Coaching answers wait for the round to end, so the coach gives no live guidance.
            if intent not in [i for i, _ in self.pending]:   # The same question twice gets one answer.
                self.pending.append((intent, text))
            self._say(a["queued"])
        return intent

    def _profile_command(self, intent, arg):
        """Facts about the player in player_profile.md. Not about the game, so they work at any time."""
        if not self.profile_on:
            self._say("The player profile is off. Turn it on in the settings.")
        elif intent == "remember":
            fact = self.profile.add(arg)
            print(f"[profile] Saved: {fact}" if fact else f"[profile] Nothing to save in '{arg}'.")
            self._say("Got it. I will remember that." if fact else "What should I remember? Say remember that, then the fact.")
        elif intent == "forget":
            fact = self.profile.forget_last()
            print(f"[profile] Removed: {fact}" if fact else "[profile] The profile is empty.")
            self._say(f"Forgotten: {fact}" if fact else "I have nothing to forget.")
        else:
            facts = self.profile.facts()
            if not facts:
                self._say("I know nothing about you yet. Say remember that, then a fact.")
            else:
                self._say("You told me: " + " ".join(facts[-6:]))

    def close(self):
        self.end_match(next_match=False)
        self.analyst.wait()
        if self.log:
            self._log_file.close()
        if self.banner_log:
            self.banner_log.close()
