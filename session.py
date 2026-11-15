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
    def __init__(self, out_dir="reports", show_crosshair=True):
        self.out_dir = out_dir
        self.show_crosshair = show_crosshair
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
            lines += [f"Result: {result or 'unknown'}" + (f" {score}" if score else ""), ""]
        if personality:
            lines += [f"Coach personality: {personality}", ""]
        if closing:
            lines += [f"Coach: {closing}", ""]
        heads = ["Round", "Result", "Deaths"]
        if self.show_crosshair:
            heads += ["Fights", "Crosshair median (px, + = low)"]
        heads += ["Bursts", "Moving", "Long sprays", "Bad reloads", "Coach said"]
        lines += ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
        for n, won, credit, s, msg in self.rows:
            res = ("Won" if won else "Lost") + (f" ({credit})" if credit else "")
            cells = [n, res, s.get("deaths", "")]
            if self.show_crosshair:
                cells += [s["fights"], "" if s["crosshair_median"] is None else s["crosshair_median"]]
            cells += [s["bursts"], s["moving_bursts"], s["long_sprays"], s["bad_reloads"], msg or ""]
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        if not self.show_crosshair:
            lines += ["", "The crosshair check is off, so the report has no crosshair columns. "
                          "To try it, set checks.crosshair.enabled to true in config.yaml."]
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
        self.report = Report(show_crosshair=cfg["checks"]["crosshair"].get("enabled", True))
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

    def hud(self, t, ammo, health, banner, spectating=None, shot_error=None):
        if self.banner_log and banner and banner != self.last_banner:
            self.last_banner = banner
            self.banner_log.write(f"{datetime.now():%H:%M:%S}  ammo={ammo} health={health}  {banner}\n")
            self.banner_log.flush()
        if self.log:
            self.log.writerow([round(t, 3), "hud", "", "", "", ammo, health, banner or ""])
        self._handle(t, self.analyzer.on_hud(t, ammo, health, banner, spectating, shot_error))

    def scene(self, kind, t, value):
        """Handle a score, banner, screen or agent reading from the screen reader."""
        if kind == "banner" and self.banner_log and value and value != self.last_banner:
            self.last_banner = value
            self.banner_log.write(f"{datetime.now():%H:%M:%S}  {value}\n")
            self.banner_log.flush()
        self._handle(t, self.analyzer.on_scene(kind, t, value))

    def _handle(self, t, events):
        for kind, data, stats in events:
            if kind == "round_end":
                msg = self.coach.round_end(data["won"], data["credit"], stats, data.get("score"))
                n = self.coach.round_no
                score = f" | score {self.coach.won}-{self.coach.lost}" if data.get("score") else ""
                credit = f" ({data['credit']})" if data["credit"] else ""
                print(f"Round {n}: {'won' if data['won'] else 'lost'}{credit}{score} | {stats.summary()}")
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
                if self.picker.is_mix:             # Mix: a new personality for the next round, with no announcement.
                    self.coach.set_lines(self.picker.next())
                    self._apply_personality()
            elif kind == "match_end":
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
        if self.analyzer.rounds:
            closing = self.coach.debrief(self.analyzer.rounds, result, score)
            if not result and self.coach.won != self.coach.lost:
                result = "VICTORY" if self.coach.won > self.coach.lost else "DEFEAT"
            final = f"{self.coach.won}-{self.coach.lost}"
            print(f"[match] Match over: {result or 'result unknown'} {final}.")
            if speak and next_match:
                self._speak("debrief", closing, max_words=70,
                            extra=f"Result: {result or 'unknown'}. Final score {final}. Scripted debrief: {closing}")
            else:
                print(f"[coach] (not spoken) {closing}")
            path = self.report.write(result, closing, "Mix" if self.picker.is_mix else self.coach.lines.get("name"), final)
            if path:
                print(f"Report saved: {path}")
        self.coach.new_match()
        self.analyzer.new_match()
        self.report = Report(show_crosshair=self.cfg["checks"]["crosshair"].get("enabled", True))
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
            groups = {"tips": {"tip", "focus"}, "mental": {"reset", "credit", "agent_select", "agent"},
                      "answers": {"answer"}, "match_end": {"match_end", "debrief"}}
            kinds = set().union(*(groups.get(str(g), set()) for g in use_for))
        return AIVoiceWriter(client, max_words=int(ai_cfg.get("max_words", 25)),
                             max_wait_s=float(ai_cfg.get("timeout_s", 4)) * 2, kinds=kinds)

    def _speak(self, kind, fallback, stats=None, issue=None, question=None, extra=None, after=None,
               max_words=None):
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
        words = max_words or self.ai.max_words
        self.ai.submit(system_prompt(lines, words), user, fallback, done, max_words=words)

    def _reply(self, intent, question):
        """Answer a coaching question, with the AI when it is on."""
        fallback = self._answer(intent)
        worst = self._worst_issue()
        extra = f"Main thing to work on now: {self.coach.lines['focus_names'][worst]}." if worst else \
            "No common mistake yet."
        self._speak("answer", fallback, question=question or intent, extra=extra)

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
            if self.picker.is_mix:
                self._say("Mix mode. A different coach each round.")
            elif not self.picker.is_random:
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
