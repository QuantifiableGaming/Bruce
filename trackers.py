from collections import deque


class AmmoTracker:
    """Turns ammo readings into shot bursts and reloads.

    A drop counts only when the next reading agrees (the same or lower). A single wrong
    reading, for example "1" when the ammo box is empty, then makes no burst.
    When the ammo is off the screen for more than stale_s (abilities, knife, spike),
    the next reading starts fresh, because you cannot shoot with the ammo hidden.
    """

    def __init__(self, burst_gap=0.35, max_drop=6, stale_s=0.5):
        self.burst_gap = burst_gap
        self.max_drop = max_drop  # Bigger drops are weapon swaps or OCR errors.
        self.stale_s = stale_s
        self.reset()

    def reset(self):
        self.last = None
        self.last_t = None
        self.burst = None
        self.pending_up = None
        self.pending_down = None

    def _shots(self, t, count, prev_t=None):
        events = []
        if self.burst is None:
            self.burst = {"start": t, "end": t, "shots": 0}
            # The first shot was between the previous reading (prev_t) and this one (t).
            events.append(("burst_start", {"t": t, "prev_t": prev_t if prev_t is not None else t}))
        self.burst["shots"] += count
        self.burst["end"] = t
        return events

    def update(self, t, ammo):
        events = []
        if self.burst is not None and self.pending_down is None and t - self.burst["end"] > self.burst_gap:
            events.append(("burst_end", self.burst))
            self.burst = None
        if ammo is None:
            if self.last_t is not None and t - self.last_t > self.stale_s:
                self.last = None
                self.pending_up = None
                self.pending_down = None
            return events

        if self.pending_down is not None:
            before, low, pt, prev_t = self.pending_down
            self.pending_down = None
            if ammo <= low:
                events += self._shots(pt, before - low, prev_t)
            else:
                self.last = before   # The drop was a reading error.

        if self.pending_up is not None:
            # An increase counts as a reload only if the next reading agrees.
            before, after = self.pending_up
            self.pending_up = None
            if ammo == after:
                events.append(("reload_done", {"t": t, "before": before, "after": after}))
            elif self.last is not None and ammo <= before:
                self.last = before  # The increase was an OCR error.

        # With slow reads, one read covers more shots. A rifle fires about 10 to 13 shots per second.
        gap = t - self.last_t if self.last_t is not None else 0.0
        limit = max(self.max_drop, int(13 * min(gap, 1.5)) + 3)
        prev_t, self.last_t = self.last_t, t
        if self.last is not None:
            drop = self.last - ammo
            if 0 < drop <= limit:
                self.pending_down = (self.last, ammo, t, prev_t)
            elif drop < 0:
                self.pending_up = (self.last, ammo)
        self.last = ammo
        return events

    def flush(self):
        self.pending_down = None
        if self.burst is not None:
            burst, self.burst = self.burst, None
            return [("burst_end", burst)]
        return []


class HealthTracker:
    """Turns health readings into damage and death events. Needs two equal readings."""

    def __init__(self, min_damage=5):
        self.min_damage = min_damage
        self.reads = deque(maxlen=2)
        self.value = None
        self.alive = True

    def reset(self):
        self.reads.clear()
        self.value = None
        self.alive = True

    def update(self, t, hp):
        events = []
        if hp is None or hp > 150:
            return events
        self.reads.append(hp)
        if len(self.reads) < 2 or self.reads[0] != self.reads[1]:
            return events
        if self.alive and self.value is not None and hp <= self.value - self.min_damage:
            events.append(("damage", t))
        if self.alive and hp == 0:
            self.alive = False
            events.append(("death", t))
        self.value = hp
        return events


class BannerTracker:
    """Reads round results and match results from banner text.

    To avoid false alarms from menu text, a result must match a whole word,
    and the same result must show in two banner reads in a row.
    """

    def __init__(self, keywords, round_cooldown=8.0, match_cooldown=60.0, confirm_reads=2):
        self.kw = keywords
        self.round_cooldown = round_cooldown
        self.match_cooldown = match_cooldown
        self.confirm_reads = confirm_reads
        self.last_round_t = -1e9
        self.last_match_t = -1e9
        self.pending_credit = None
        self.candidate = None  # (key, time of first read)

    @staticmethod
    def _find(text, words):
        """Return the first keyword that shows as a whole word (or whole phrase) in the text."""
        padded = f" {text} "
        return next((w for w in words if f" {w} " in padded), None)

    def _confirmed(self, key, t):
        if self.confirm_reads <= 1:
            return True
        if self.candidate and self.candidate[0] == key and t - self.candidate[1] <= 3.0:
            self.candidate = None
            return True
        self.candidate = (key, t)
        return False

    def update(self, t, text):
        if not text:
            return None
        text = " ".join(text.upper().split())
        credit = self._find(text, self.kw["credit"])
        if credit:
            self.pending_credit = (t, credit)

        result = self._find(text, self.kw["match_end"])
        if result and t - self.last_match_t > self.match_cooldown:
            if not self._confirmed(("match", result), t):
                return None
            self.last_match_t = t
            return ("match_end", {"result": result, "text": text})

        if t - self.last_round_t < self.round_cooldown:
            return None
        won = self._find(text, self.kw["round_won"]) is not None
        lost = self._find(text, self.kw["round_lost"]) is not None
        if won == lost:
            return None
        if not self._confirmed(("round", won), t):
            return None
        credit_word = None
        if self.pending_credit and t - self.pending_credit[0] < 6.0:
            credit_word = self.pending_credit[1]
        self.pending_credit = None
        self.last_round_t = t
        return ("round_end", {"won": won, "credit": credit_word, "text": text})


AGENTS = {
    "duelist": ["jett", "phoenix", "reyna", "raze", "yoru", "neon", "iso", "waylay"],
    "initiator": ["sova", "breach", "skye", "kayo", "fade", "gekko", "tejo"],
    "controller": ["brimstone", "omen", "viper", "astra", "harbor", "clove"],
    "sentinel": ["sage", "cypher", "killjoy", "chamber", "deadlock", "vyse", "veto"],
}
MODE_WORDS = ["SWIFTPLAY", "UNRATED", "COMPETITIVE", "DEATHMATCH", "SPIKE", "ESCALATION", "PREMIER", "CUSTOM",
              "REPLICATION", "SKIRMISH"]
RESULT_WORDS = ["VICTORY", "DEFEAT", "DRAW"]
CREDIT_WORDS = ["CLUTCH", "ACE", "FLAWLESS", "THRIFTY"]
BUY_WORDS = ["BUY PHASE", "MATCH POINT", "PRESS BUY"]


def find_agent(text):
    """Return (agent, role) from the agent select panel text. Either can be None."""
    import difflib

    if not text:
        return None, None
    words = text.lower().split()
    role = next((r for r in AGENTS if r in words or r + "s" in words), None)
    best, best_ratio = None, 0.72
    for word in words:
        if word in AGENTS or len(word) < 3:
            continue
        for r, names in AGENTS.items():
            if role and r != role:
                continue
            for name in names:
                ratio = difflib.SequenceMatcher(None, word, name).ratio()
                if ratio > best_ratio:
                    best, best_ratio = (name, r), ratio
    if best:
        return best[0].capitalize(), best[1]
    return None, role


class MatchTracker:
    """Follows a match from the score at the top of the screen.

    The score is on screen for the whole match, also when the player is dead, so it is
    the most reliable signal. A new score counts when two reads in a row agree.
    Events: agent_select, agent, match_start, round_end, score_sync, match_end.
    """

    def __init__(self, credit_wait=4.0):
        self.credit_wait = credit_wait
        self.reset()
        self.last_agent_select_t = -1e9

    def reset(self):
        self.score = None            # Confirmed (yours, enemy).
        self.candidate = None        # (score, first time, number of equal reads) for a score that differs.
        self.last_change_t = -1e9
        self.last_score_ok_t = None
        self.in_match = False
        self.pending = None          # (time, won, score) of a round end that waits for the credit banner.
        self.credit = None           # (time, word)
        self.buy_t = -1e9
        self.match_ended_t = -1e9
        self.agent = None            # (name, role, first seen time)
        self.agent_said = False

    # ---------- helpers ----------
    @staticmethod
    def _has(text, words):
        padded = f" {text} "
        return next((w for w in words if f" {w} " in padded), None)

    def score_healthy(self, t, window=30.0):
        return self.last_score_ok_t is not None and t - self.last_score_ok_t <= window

    def in_buy(self, t, window=4.0):
        return t - self.buy_t <= window

    def poll(self, t):
        """Release a round end after the short wait for a credit banner."""
        if self.pending and t - self.pending[0] >= self.credit_wait:
            pt, won, score = self.pending
            self.pending = None
            credit = self.credit[1] if (won and self.credit and abs(self.credit[0] - pt) <= 8.0) else None
            self.credit = None
            return [("round_end", {"won": won, "credit": credit, "score": score})]
        return []

    # ---------- inputs ----------
    def on_score(self, t, score):
        """Handle one score read.

        A normal round end (one side goes up by 1, at least 12 seconds after the last
        change) needs 2 equal reads in a row. Every other change (a jump, or a lower
        score) needs 4 equal reads in a row, because a misread digit looks like that.
        """
        events = self.poll(t)
        if score is None:
            return events
        self.last_score_ok_t = t
        if score == self.score:
            self.candidate = None
            return events
        if not self.candidate or self.candidate[0] != score or t - self.candidate[1] > 8.0:
            self.candidate = (score, t, 1)   # First read of a new score: wait for more reads.
            if self.score is not None:
                return events
        else:
            self.candidate = (score, self.candidate[1], self.candidate[2] + 1)
        old = self.score
        count = self.candidate[2]
        is_round = old is not None and score in ((old[0] + 1, old[1]), (old[0], old[1] + 1)) \
            and t - self.last_change_t >= 12.0
        need = 2 if (is_round or old is None) else 4
        if count < need:
            return events
        self.candidate = None
        self.score = score
        self.last_change_t = t
        if old is None or sum(score) < sum(old):
            # First score of a match, or the score went back: a new match started.
            if old is not None:
                events.append(("match_reset", {}))
            self.in_match = True
            self.pending = None
            events.append(("match_start", {"score": score}))
        elif is_round:
            events += self._flush()
            self.pending = (t, score[0] > old[0], score)
        else:
            events.append(("score_sync", {"score": score}))   # Rounds were missed. Take the real score.
        return events

    def _flush(self):
        if not self.pending:
            return []
        self.pending = (self.pending[0] - self.credit_wait, self.pending[1], self.pending[2])
        return self.poll(self.pending[0] + self.credit_wait)

    def on_banner(self, t, text):
        events = self.poll(t)
        if not text:
            return events
        text = " ".join(text.upper().split())
        if self._has(text, BUY_WORDS):
            self.buy_t = t
        word = self._has(text, CREDIT_WORDS)
        if word:
            self.credit = (t, "TEAM ACE" if word == "ACE" and "TEAM" in text.split() else word)
        return events

    def on_screen(self, t, text):
        """Text from the top-left area, read only when there is no score on screen."""
        events = self.poll(t)
        if not text:
            return events
        text = " ".join(text.upper().split())
        result = self._has(text, RESULT_WORDS)
        if result and self.in_match and t - self.match_ended_t > 60:
            events += self._flush()
            score = self.score
            self.match_ended_t = t
            self.in_match = False
            self.score = None
            self.candidate = None
            events.append(("match_end", {"result": result, "score": score}))
        elif self._has(text, MODE_WORDS) and not result and not self.score_healthy(t, 10.0):
            if t - self.last_agent_select_t > 180:
                self.last_agent_select_t = t
                self.agent, self.agent_said = None, False
                self.score, self.candidate, self.in_match = None, None, False
                events.append(("agent_select", {}))
            self.agent_select_seen_t = t
        return events

    def on_agent(self, t, text):
        """Text from the agent panel. Report an agent after it stays selected for 8 seconds."""
        events = self.poll(t)
        if t - self.last_agent_select_t > 150 or self.agent_said:
            return events
        name, role = find_agent(text)
        if not name:
            return events
        if not self.agent or self.agent[0] != name:
            self.agent = (name, role, t)
        elif t - self.agent[2] >= 8.0:
            self.agent_said = True
            events.append(("agent", {"agent": name, "role": role}))
        return events
