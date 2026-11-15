from collections import deque


class AmmoTracker:
    """Turns ammo readings into shot bursts and reloads."""

    def __init__(self, burst_gap=0.35, max_drop=6):
        self.burst_gap = burst_gap
        self.max_drop = max_drop  # Bigger drops are weapon swaps or OCR errors.
        self.last = None
        self.burst = None
        self.pending_up = None

    def reset(self):
        self.last = None
        self.burst = None
        self.pending_up = None

    def update(self, t, ammo):
        events = []
        if self.burst is not None and t - self.burst["end"] > self.burst_gap:
            events.append(("burst_end", self.burst))
            self.burst = None
        if ammo is None:
            return events

        if self.pending_up is not None:
            # An increase counts as a reload only if the next reading agrees.
            before, after = self.pending_up
            self.pending_up = None
            if ammo == after:
                events.append(("reload_done", {"t": t, "before": before, "after": after}))
            elif self.last is not None and ammo <= before:
                self.last = before  # The increase was an OCR error.

        if self.last is not None:
            drop = self.last - ammo
            if 0 < drop <= self.max_drop:
                if self.burst is None:
                    self.burst = {"start": t, "end": t, "shots": 0}
                    events.append(("burst_start", t))
                self.burst["shots"] += drop
                self.burst["end"] = t
            elif drop < 0:
                self.pending_up = (self.last, ammo)
        self.last = ammo
        return events

    def flush(self):
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
