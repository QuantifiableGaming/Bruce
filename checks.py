from collections import deque
from dataclasses import asdict, dataclass, field

from .trackers import AmmoTracker, BannerTracker, HealthTracker, MatchTracker
from .util import median


@dataclass
class RoundStats:
    crosshair_offsets: list = field(default_factory=list)
    bursts: int = 0
    judged_bursts: int = 0
    moving_bursts: int = 0
    long_sprays: int = 0
    bad_reloads: int = 0
    damage_times: list = field(default_factory=list)
    deaths: int = 0

    def issues(self, c):
        """Return {issue: severity}. A severity of 1.0 or more means the check failed."""
        out = {}
        cc = c["crosshair"]
        tol = cc["tolerance_px_at_1080"]
        if len(self.crosshair_offsets) >= cc["min_samples"]:
            med = median(self.crosshair_offsets)
            if med > tol:
                out["crosshair_low"] = med / tol
            elif med < -tol:
                out["crosshair_high"] = -med / tol
        mc = c["moving_shots"]
        if self.judged_bursts >= mc["min_bursts"]:
            ratio = self.moving_bursts / self.judged_bursts
            if ratio > mc["max_ratio"]:
                out["moving_shots"] = ratio / mc["max_ratio"]
        lc = c["long_spray"]
        if self.long_sprays >= lc["min_count"]:
            out["long_spray"] = self.long_sprays / lc["min_count"]
        rc = c["reload"]
        if self.bad_reloads >= rc["min_count"]:
            out["bad_reload"] = self.bad_reloads / rc["min_count"]
        return out

    def summary(self):
        d = asdict(self)
        d.pop("damage_times")
        d["crosshair_median"] = round(median(self.crosshair_offsets), 1) if self.crosshair_offsets else None
        d["fights"] = len(d.pop("crosshair_offsets"))
        return d


class Analyzer:
    """Combines frame and HUD signals into per-round mechanical stats."""

    def __init__(self, cfg):
        self.c = cfg["checks"]
        self.ammo = AmmoTracker(burst_gap=self.c["long_spray"]["burst_gap_s"])
        self.health = HealthTracker()
        self.banner = BannerTracker(cfg["banner_keywords"], confirm_reads=int(cfg.get("banner_confirm_reads", 2)))
        self.live_window = float(cfg.get("banner_needs_hud_s", 20))
        self.last_hud_ok_t = None
        self.warned_no_hud = False
        self.match = MatchTracker()
        self.hud_seen = False   # True after the first ammo or health read. Replays with no HUD still work.
        self.motion = deque()
        self.shot_errors = deque()       # (time, degrees) from Valorant's "Shooting Error" stat
        self.shot_error_seen_t = None
        self.enemy_visible = False
        self.enemy_last_seen = -1e9
        self.enemy_appear_t = None
        self.sampled = True
        self.prev_hud_t = None
        self.hud_gone_t = None     # Time when ammo and health went off the screen.
        self.last_damage_t = None
        self.round_closed_t = -1e9
        self.round = RoundStats()
        self.rounds = []

    def on_frame(self, t, motion, enemy):
        self.motion.append((t, motion))
        while self.motion and t - self.motion[0][0] > 3.0:
            self.motion.popleft()
        if not self.health.alive:
            return  # You are spectating a teammate.
        if self.hud_seen and (self.last_hud_ok_t is None or t - self.last_hud_ok_t > 4.0):
            return  # No ammo or health on screen: a menu, agent select, or you are dead and spectate.
        if not self.c["crosshair"].get("enabled", True):
            return
        if enemy["visible"]:
            if not self.enemy_visible and t - self.enemy_last_seen > 0.5:
                self.enemy_appear_t = t
                self.sampled = False
            self.enemy_visible = True
            self.enemy_last_seen = t
            # Crosshair placement is where your aim was when the enemy first showed up.
            if (not self.sampled and enemy["head_offset"] is not None
                    and t - self.enemy_appear_t <= 0.3):
                self.round.crosshair_offsets.append(enemy["head_offset"])
                self.sampled = True
        else:
            self.enemy_visible = False

    def _ammo_events(self, t, events):
        mc, lc, rc = self.c["moving_shots"], self.c["long_spray"], self.c["reload"]
        for kind, data in events:
            if kind == "burst_start":
                self.round.bursts += 1
                shot_t = data["t"] if isinstance(data, dict) else t
                prev_t = data.get("prev_t", shot_t) if isinstance(data, dict) else self.prev_hud_t
                stat = self._first_shot_error(prev_t if prev_t is not None else shot_t, t)
                if stat is not False:
                    # Valorant's own Shooting Error stat is on the screen. It is better than the motion guess.
                    if stat is not None:
                        self.round.judged_bursts += 1
                        if stat > mc.get("error_threshold_deg", 1.0):
                            self.round.moving_bursts += 1
                    continue
                start = (prev_t if prev_t is not None else shot_t) - mc["lookback_s"]
                values = [m for mt, m in self.motion if start <= mt <= shot_t and m is not None]
                if values:
                    self.round.judged_bursts += 1
                    if median(values) > mc["motion_threshold"]:
                        self.round.moving_bursts += 1
            elif kind == "burst_end":
                if data["shots"] > lc["max_shots"]:
                    self.round.long_sprays += 1
            elif kind == "reload_done":
                had_ammo = data["before"] >= rc["min_ammo_left_ratio"] * data["after"]
                in_fight = any(t - rc["window_s"] <= d <= t for d in self.round.damage_times)
                if had_ammo and in_fight:
                    self.round.bad_reloads += 1

    def _first_shot_error(self, start, now):
        """The Shooting Error of the first shot of a burst.

        Returns False when the stat is not on the screen (use the motion guess),
        None when the stat is on but showed no shot in time, else the value in degrees.
        """
        if self.shot_error_seen_t is None or now - self.shot_error_seen_t > 5.0:
            return False
        for et, value in self.shot_errors:
            if start - 0.02 <= et <= now + 0.02 and value > 0:
                return value
        return None

    def on_hud(self, t, ammo, health, banner_text=None, spectating=None, shot_error=None):
        out = []
        if shot_error is not None:
            self.shot_errors.append((t, shot_error))
            self.shot_error_seen_t = t
            while self.shot_errors and t - self.shot_errors[0][0] > 2.0:
                self.shot_errors.popleft()
        if spectating:
            # "SWITCH PLAYER" shows: you are dead, and the HUD shows a teammate's ammo and health.
            if self.health.alive and not self._between_rounds(t):
                self.health.alive = False
                self.round.deaths += 1
                self._ammo_events(t, self.ammo.flush())
            self.last_hud_ok_t = t
            ammo = health = None
        for kind, et in self.health.update(t, health):
            if kind == "damage":
                self.round.damage_times.append(et)
                self.last_damage_t = et
            elif kind == "death":
                if self._between_rounds(t):
                    self.health.alive = True   # A 0 between rounds is not a death in the next round.
                else:
                    self.round.deaths += 1
        self._check_death(t, ammo, health)
        if self.health.alive:
            self._ammo_events(t, self.ammo.update(t, ammo))
        self.prev_hud_t = t

        # The game HUD shows ammo and health during a match. Menus and the lobby do not.
        if ammo is not None or (health is not None and 0 < health <= 150):
            self.last_hud_ok_t = t
            self.hud_seen = True
        in_match = self.last_hud_ok_t is not None and t - self.last_hud_ok_t <= self.live_window
        if self.match.score_healthy(t):
            return out   # The score at the top of the screen gives the round results.
        # Fallback when the score cannot be read: use the WON / LOST banner text.
        event = self.banner.update(t, banner_text) if in_match else None
        if not in_match and banner_text and not self.warned_no_hud:
            probe = BannerTracker(self.banner.kw, confirm_reads=1)
            if probe.update(t, banner_text):
                self.warned_no_hud = True
                print("[banner] Result text seen, but no ammo or health reading. Ignored (menu or lobby). "
                      "If this shows during a match, check the Ammo and Health boxes in Screen regions.")
        if event is None:
            return out
        kind, data = event
        if kind == "round_end":
            out.append(("round_end", data, self._close_round(t)))
        else:
            out.append(("match_end", data, None))
        return out

    def _between_rounds(self, t):
        return self.match.in_buy(t, window=6.0) or t - self.round_closed_t < 10.0

    def _check_death(self, t, ammo, health):
        """Find a death. The game does not show health 0. It hides ammo and health.

        A death is: you took damage, then ammo and health stay off the screen for more
        than 1.2 seconds. After that, the HUD shows the teammate that you spectate,
        so the round statistics stop until the next round.
        """
        if ammo is not None or health is not None:
            self.hud_gone_t = None
            return
        if self.hud_gone_t is None:
            self.hud_gone_t = self.prev_hud_t if self.prev_hud_t is not None else t
        if not self.health.alive or self.last_damage_t is None:
            return
        if (t - self.hud_gone_t >= 1.2 and self.hud_gone_t - self.last_damage_t <= 3.0
                and not self._between_rounds(t)):
            self.health.alive = False
            self.round.deaths += 1
            self._ammo_events(t, self.ammo.flush())

    def _close_round(self, t):
        self._ammo_events(t, self.ammo.flush())
        stats = self.round
        self.rounds.append(stats)
        self.round = RoundStats()
        self.ammo.reset()
        self.health.reset()
        self.last_damage_t = None
        self.round_closed_t = t
        self.enemy_visible = False
        self.sampled = True
        return stats

    def on_scene(self, kind, t, value):
        """Handle a score, banner, screen or agent reading. Returns events like on_hud."""
        if kind == "score":
            events = self.match.on_score(t, value)
        elif kind == "banner":
            events = self.match.on_banner(t, value)
            if value and not self.match.score_healthy(t):
                return self._wrap(t, events) + self.on_hud(t, None, None, value)
        elif kind == "screen":
            events = self.match.on_screen(t, value)
        elif kind == "agent":
            events = self.match.on_agent(t, value)
        else:
            return []
        return self._wrap(t, events)

    def _wrap(self, t, events):
        out = []
        for name, data in events:
            out.append((name, data, self._close_round(t) if name == "round_end" else None))
        return out

    def new_match(self):
        self.rounds = []
        self.round = RoundStats()

    def clear_round(self):
        """Drop what was counted before the first round, for example in agent select."""
        self.round = RoundStats()
        self.ammo.reset()
