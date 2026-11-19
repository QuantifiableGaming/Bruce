import random
from collections import defaultdict


class LinePicker:
    """Picks random lines and does not repeat one until the list is used up."""

    def __init__(self, rng):
        self.rng = rng
        self.used = defaultdict(set)

    def pick(self, key, options):
        free = [o for o in options if o not in self.used[key]]
        if not free:
            self.used[key].clear()
            free = list(options)
        line = self.rng.choice(free)
        self.used[key].add(line)
        return line

    def reset(self):
        self.used.clear()


class Coach:
    """Chooses at most one message per round: a reset, credit, focus or tip."""

    def __init__(self, lines, cfg, rng=None):
        self.lines = lines
        self.cfg = cfg["coach"]
        self.checks = cfg["checks"]
        self.rng = rng or random.Random()
        self.picker = LinePicker(self.rng)
        self.new_match()

    def set_lines(self, lines):
        self.lines = lines
        self.picker.reset()

    def new_match(self):
        self.won = 0
        self.lost = 0
        self.loss_streak = 0
        self.peak_lead = 0
        self.round_no = 0
        self.credits = 0
        self.last_tip_round = {}
        self.issue_counts = {}
        self.last_issue = None
        self.picker.reset()

    def _top_issue(self, stats):
        cooldown = self.cfg["tip_cooldown_rounds"]
        issues = {
            k: v for k, v in stats.issues(self.checks).items()
            if self.round_no - self.last_tip_round.get(k, -99) > cooldown
        }
        return max(issues, key=issues.get) if issues else None

    def sync_score(self, score):
        """Take the real score from the screen, for example when the coach starts mid-match."""
        self.won, self.lost = score
        self.round_no = self.won + self.lost

    def pregame(self):
        """A line for the agent select screen."""
        return self.picker.pick("agent_select", self.lines["agent_select"])

    def agent_line(self, agent, role):
        """A line about the agent the player selected."""
        options = self.lines["agent_role"].get(role) or self.lines["agent_any"]
        return self.picker.pick("agent_" + str(role), options).format(agent=agent)

    def _best_thing(self, rounds):
        """Return (kind, values) for the best thing in the match, or (None, {})."""
        tol = self.checks["crosshair"]["tolerance_px_at_1080"]
        offsets = [o for r in rounds for o in r.crosshair_offsets]
        bursts = sum(r.judged_bursts for r in rounds)
        still = bursts - sum(r.moving_bursts for r in rounds)
        candidates = []
        if len(offsets) >= 5:
            good = sum(1 for o in offsets if abs(o) <= tol)
            candidates.append((good / len(offsets), "crosshair", {"good": good, "total": len(offsets)}))
        if bursts >= 10:
            candidates.append((still / bursts, "strafe", {"good": still, "total": bursts}))
        candidates = [c for c in candidates if c[0] >= 0.5]
        if candidates:
            _, kind, values = max(candidates)
            return kind, values
        if self.credits:
            return "credit", {"count": self.credits}
        return None, {}

    def debrief(self, rounds, result=None, score=None):
        """Return the end-of-match debrief: result, one good thing, one thing to work on, sign-off."""
        d = self.lines["debrief"]
        if score:
            self.sync_score(score)
        key = {"VICTORY": "victory", "DEFEAT": "defeat", "DRAW": "draw"}.get(str(result or "").upper())
        if key is None:
            key = "victory" if self.won > self.lost else "defeat" if self.lost > self.won else "unknown"
            if self.won == self.lost == 0:
                key = "unknown"
        parts = [self.picker.pick("debrief_" + key, d[key]).format(won=self.won, lost=self.lost)]
        kind, values = self._best_thing(rounds)
        if kind:
            parts.append(d["good_" + kind].format(**values))
        worst = max(self.issue_counts, key=self.issue_counts.get) if self.issue_counts else None
        if worst:
            parts.append(d["work_on"].format(focus=self.lines["focus_names"][worst]))
        elif rounds:
            parts.append(d["clean"])
        parts.append(self.picker.pick("debrief_signoff", d["signoff"]))
        return " ".join(parts)

    def round_end(self, won, credit, stats, score=None):
        """Return (kind, text) or None. Mental messages win over tips after losses."""
        self.round_no += 1
        self.last_issue = None
        prev_streak = self.loss_streak
        if won:
            self.won += 1
            self.loss_streak = 0
        else:
            self.lost += 1
            self.loss_streak += 1
        if score:
            self.sync_score(score)   # The score on the screen is the truth.
        lead = self.won - self.lost
        self.peak_lead = max(self.peak_lead, lead)
        if credit:
            self.credits += 1
        for issue in stats.issues(self.checks):
            self.issue_counts[issue] = self.issue_counts.get(issue, 0) + 1

        if not won:
            swing = self.peak_lead >= self.cfg["swing_lead"] and self.peak_lead - lead >= 3
            if self.loss_streak >= self.cfg["loss_streak_reset"] or swing:
                if swing:
                    self.peak_lead = lead
                return ("reset", self.picker.pick("reset", self.lines["reset"]))

        if credit or (won and prev_streak >= 2):
            return ("credit", self.picker.pick("credit", self.lines["credit"]))

        issue = self._top_issue(stats)
        if issue is None:
            return None  # Stay quiet when nothing needs attention.
        self.last_tip_round[issue] = self.round_no
        self.last_issue = issue
        if self.loss_streak == 2:
            template = self.picker.pick("focus", self.lines["focus"])
            return ("focus", template.format(focus=self.lines["focus_names"][issue]))
        return ("tip", self.picker.pick(issue, self.lines["tips"][issue]))

    def match_end(self, rounds):
        """Return one good thing from the match."""
        good_lines = self.lines["match_good"]
        tol = self.checks["crosshair"]["tolerance_px_at_1080"]
        offsets = [o for r in rounds for o in r.crosshair_offsets]
        bursts = sum(r.judged_bursts for r in rounds)
        still = bursts - sum(r.moving_bursts for r in rounds)

        candidates = []
        if len(offsets) >= 5:
            good = sum(1 for o in offsets if abs(o) <= tol)
            candidates.append((good / len(offsets),
                               good_lines["crosshair"].format(good=good, total=len(offsets))))
        if bursts >= 10:
            candidates.append((still / bursts, good_lines["strafe"].format(good=still, total=bursts)))
        candidates = [c for c in candidates if c[0] >= 0.5]
        if candidates:
            return max(candidates)[1]
        if self.credits:
            return good_lines["credit"].format(count=self.credits)
        return good_lines["fallback"]
