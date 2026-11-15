import csv
import os
from datetime import datetime

from .ai import AIClient, AIVoiceWriter, build_request, load_secrets, system_prompt
from .checks import Analyzer
from .commands import INSTANT, parse
from .engine import Coach
from .personality import PersonalityPicker, available
from .vision import EnemyDetector, MotionMeter


class Report:
    def __init__(self, out_dir="reports"):
        self.out_dir = out_dir
        self.rows = []
        self.started = datetime.now()

    def add(self, n, won, credit, stats, message):
        self.rows.append([n, won, credit, stats.summary(), message])
        return len(self.rows) - 1

    def set_message(self, index, message):
        if 0 <= index < len(self.rows):
            self.rows[index][4] = message

    def write(self, result=None, closing=None, personality=None):
        if not self.rows:
            return None
        os.makedirs(self.out_dir, exist_ok=True)
        path = os.path.join(self.out_dir, f"match_{self.started:%Y%m%d_%H%M%S}.md")
        lines = [f"# Match report {self.started:%Y-%m-%d %H:%M}", ""]
        if result:
            lines += [f"Result: {result}", ""]
        if personality:
            lines += [f"Coach personality: {personality}", ""]
        if closing:
            lines += [f"Coach: {closing}", ""]
        lines += [
            "| Round | Result | Fights | Crosshair median (px, + = low) | Bursts | Moving | Long sprays | Bad reloads | Coach said |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for n, won, credit, s, msg in self.rows:
            res = ("Won" if won else "Lost") + (f" ({credit})" if credit else "")
            ch = "" if s["crosshair_median"] is None else s["crosshair_median"]
            lines.append(f"| {n} | {res} | {s['fights']} | {ch} | {s['bursts']} | {s['moving_bursts']} | "
                         f"{s['long_sprays']} | {s['bad_reloads']} | {msg or ''} |")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path


class Session:
    """Connects capture signals, the analyzer, the coach, the voice and the report."""

    def __init__(self, cfg, picker, voice, log_path=None, ai=None, banner_log=None):
        self.cfg = cfg
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
        self.detector = EnemyDetector(cfg)
        self.analyzer = Analyzer(cfg)
        self.coach = Coach(picker.next(), cfg)
        self._apply_personality()
        self.report = Report()
        self.last_round_end_t = None
        self.pending = []
        self.ai = ai if ai is not None else self._make_ai()
        if self.ai.enabled:
            print(f"AI coach on: {self.ai.client.provider} ({self.ai.client.model}).")
        elif cfg.get("ai", {}).get("provider", "off") != "off":
            print(f"AI coach off: {self.ai.client.reason} The coach uses scripted lines.")
        self.log = None
        if log_path:
            self._log_file = open(log_path, "w", newline="", encoding="utf-8")
            self.log = csv.writer(self._log_file)
            self.log.writerow(["t", "kind", "motion", "enemy_pixels", "head_offset", "ammo", "health", "banner"])

    def frame(self, t, frame):
        motion = self.meter.update(frame)
        enemy = self.detector.detect(frame)
        self.analyzer.on_frame(t, motion, enemy)
        if self.log:
            ho = "" if enemy["head_offset"] is None else round(enemy["head_offset"], 1)
            self.log.writerow([round(t, 3), "frame", "" if motion is None else round(motion, 3), enemy["pixels"], ho, "", "", ""])

    def hud(self, t, ammo, health, banner):
        if self.banner_log and banner and banner != self.last_banner:
            self.last_banner = banner
            self.banner_log.write(f"{datetime.now():%H:%M:%S}  ammo={ammo} health={health}  {banner}\n")
            self.banner_log.flush()
        if self.log:
            self.log.writerow([round(t, 3), "hud", "", "", "", ammo, health, banner or ""])
        for kind, data, stats in self.analyzer.on_hud(t, ammo, health, banner):
            if kind == "round_end":
                msg = self.coach.round_end(data["won"], data["credit"], stats)
                n = self.coach.round_no
                print(f"[banner] \"{data.get('text', '')}\"")
                print(f"Round {n}: {'won' if data['won'] else 'lost'} | {stats.summary()}")
                row = self.report.add(n, data["won"], data["credit"], stats, msg[1] if msg else None)
                if msg:
                    kind = msg[0]
                    extra = f"Highlight: {data['credit']}." if data["credit"] else None
                    report = self.report
                    self._speak(kind, msg[1], stats=stats, issue=self.coach.last_issue, extra=extra,
                                after=lambda text, r=row, rep=report: rep.set_message(r, text))
                self.last_round_end_t = t
                for intent, question in self.pending:  # Answers to questions asked during the round.
                    self._reply(intent, question)
                self.pending = []
            else:
                self.end_match(data["result"])

    def _apply_personality(self):
        lines = self.coach.lines
        c = self.cfg["coach"]
        voice = lines.get("voice", {})
        self.voice.set_style(c.get("voice_rate") or voice.get("rate", 180),
                             c.get("voice_name") or voice.get("name_contains", ""))
        print(f"Personality: {lines.get('name', '?')}")
        if self.picker.is_random:
            self._say(f"Your coach this match: {lines.get('name', 'Coach')}.")

    def end_match(self, result=None, next_match=True):
        closing = self.coach.match_end(self.analyzer.rounds) if self.analyzer.rounds else None
        if closing and next_match:
            self._speak("match_end", closing, extra=f"Result: {result or 'unknown'}. Positive found: {closing}")
        elif closing:
            self._say(closing)
        path = self.report.write(result, closing, self.coach.lines.get("name"))
        if path:
            print(f"Report saved: {path}")
        self.coach.new_match()
        self.analyzer.new_match()
        self.report = Report()
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
        if use_for:
            groups = {"tips": {"tip", "focus"}, "mental": {"reset", "credit"},
                      "answers": {"answer"}, "match_end": {"match_end"}}
            kinds = set().union(*(groups.get(str(g), set()) for g in use_for))
        return AIVoiceWriter(client, max_words=int(ai_cfg.get("max_words", 25)),
                             max_wait_s=float(ai_cfg.get("timeout_s", 4)) * 2, kinds=kinds)

    def _speak(self, kind, fallback, stats=None, issue=None, question=None, extra=None, after=None):
        """Say a coaching line. The AI writes it when it is on. The scripted line is the fallback."""
        def done(text, from_ai=False):
            self._say(text)
            if after:
                after(text)

        if not self.ai.wants(kind):
            done(fallback)
            return
        lines = self.coach.lines
        user = build_request(kind, self.coach, lines, stats=stats, issue=issue, question=question,
                             extra=extra, recent=self.recent)
        self.ai.submit(system_prompt(lines, self.ai.max_words), user, fallback, done)

    def _reply(self, intent, question):
        """Answer a coaching question, with the AI when it is on."""
        fallback = self._answer(intent)
        worst = self._worst_issue()
        extra = f"Main thing to work on now: {self.coach.lines['focus_names'][worst]}." if worst else \
            "No common mistake yet."
        self._speak("answer", fallback, question=question or intent, extra=extra)

    def answer_window_open(self, t):
        if self.last_round_end_t is None:
            return True  # Before the first round result (buy phase or warm-up).
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
        if intent == "doing":
            if c.round_no == 0 and not focus:
                return a["doing_no_data"]
            if focus:
                return a["doing_focus"].format(won=c.won, lost=c.lost, focus=focus)
            return a["doing_clean"].format(won=c.won, lost=c.lost)
        return a["unknown"]

    def command(self, t, text):
        """Handle one spoken command. Text is what came after the wake word."""
        names = [n for n, _, _ in available()]
        intent, arg = parse(text, names)
        print(f"[heard] '{text}' -> {intent}" + (f" ({arg})" if arg and intent != "unknown" else ""))
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
            self._say(a["help"])
        elif intent == "repeat":
            self.voice.say(self.last_said or a["repeat_none"])
        elif intent == "personality":
            self.picker = PersonalityPicker(arg, pool=self.cfg["coach"].get("random_pool"))
            self.coach.set_lines(self.picker.next())
            self._apply_personality()
            if not self.picker.is_random:
                self._say(self.coach.lines["answers"]["switched"].format(name=self.coach.lines["name"]))
        elif intent == "unknown" and not self.ai.wants("answer"):
            self._say(a["unknown"])
        elif self.answer_window_open(t):
            self._reply(intent, text)
        else:
            # Coaching answers wait for the round to end, so the coach gives no live guidance.
            self.pending.append((intent, text))
            self._say(a["queued"])
        return intent

    def close(self):
        self.end_match(next_match=False)
        if self.log:
            self._log_file.close()
        if self.banner_log:
            self.banner_log.close()
