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
    stops: int = 0             # First shots after a strafe that you stopped before you shot (from the keys).
    counter_stops: int = 0     # The stops that used a tap on the opposite key.
    stop_gaps_ms: list = field(default_factory=list)
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
        kc = c.get("counter_strafe") or {}
        if kc.get("enabled", True) and self.stops >= kc.get("min_stops", 3):
            missed = (self.stops - self.counter_stops) / self.stops
            if missed > kc.get("max_missed_ratio", 0.6):
                out["no_counter_strafe"] = missed / kc.get("max_missed_ratio", 0.6)
        rc = c["reload"]
        if self.bad_reloads >= rc["min_count"]:
            out["bad_reload"] = self.bad_reloads / rc["min_count"]
        return out

    def summary(self):
        d = asdict(self)
        d.pop("damage_times")
        gaps = d.pop("stop_gaps_ms")
        d["stop_gap_ms"] = round(median(gaps)) if gaps else None
        d["crosshair_median"] = round(median(self.crosshair_offsets), 1) if self.crosshair_offsets else None
        d["fights"] = len(d.pop("crosshair_offsets"))
        return d


def spray_limit(lc, mag):
    """The longest normal burst for the weapon with this magazine size. None: no spray tip.

    The magazine size tells the weapon: 12 Classic or Guardian, 25 Vandal, 30 Phantom or Spectre,
    100 Odin. Unknown sizes use max_shots.
    """
    table = lc.get("per_magazine") or {}
    if mag is not None:
        for key, value in table.items():
            if int(key) == int(mag):
                return None if value in (None, 0, "none") else int(value)
    return lc["max_shots"]


def describe_shot(keys, stat):
    """One log line about the first shot of a burst: how you stopped, and the Shooting Error stat."""
    if keys is None:
        text = "first shot: standing still"
    elif keys["moving"]:
        text = "first shot: moving"
    elif keys["counter"]:
        text = f"first shot: counter-strafe, {keys['gap_ms']} ms before the shot"
    elif keys.get("both"):
        text = (f"first shot: stop by holding both keys (no counter-strafe), {keys['gap_ms']} ms before the shot")
    else:
        text = f"first shot: slow stop (no counter-strafe), {keys['gap_ms']} ms before the shot"
    if stat is None:
        return text + ". Stat: no reading in time."
    if stat is False:
        return text + ". Stat: not on the screen."
    return text + f". Stat: {stat:.2f} degrees."


STAT_WAIT_S = 0.35        # How long a first shot waits for its Shooting Error value.
INFINITE_AMMO_FIRES = 8   # Fire presses with no change in the ammo count before the hint.


class Analyzer:
    """Combines frame and HUD signals into per-round mechanical stats."""

    def __init__(self, cfg, inputs=None):
        self.c = cfg["checks"]
        self.inputs = inputs             # keys.InputLog, or None (replays and tests)
        self.motion_lookup = None        # (start, end) -> motion scores, measured on demand (live coach).
        self.on_shot = None              # Called for each first shot: (keys, stat, keys_on). The Range challenge uses it.
        self.ammo = AmmoTracker(burst_gap=self.c["long_spray"]["burst_gap_s"])
        if inputs is not None:
            # With the key reader on, an ammo change with no fire click is a weapon change, and a
            # rise with no reload key is a weapon change too (Chamber's Headhunter, for example).
            self.ammo.fired = lambda a, b: not self._keys_on() or self.inputs.fire_active(a, b)
            self.ammo.reloaded = lambda a, b: not self._keys_on() or self.inputs.reload_pressed(a, b)
            self.ammo.switched = lambda a, b: self._keys_on() and self.inputs.switch_pressed(a, b)
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
        self.pending_shots = []
        self.last_click = False          # Set by the key reader for each first shot
        self.last_shot_t = None          # First shots that wait (up to STAT_WAIT_S) for their stat value
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
            # Only with your gun out (ammo on screen): abilities, Cypher's camera and the map
            # make many false outlines. A head far below the crosshair is almost always a
            # false outline (a close enemy's legs, an ability), so it is not used.
            cc = self.c["crosshair"]
            if (not self.sampled and enemy["head_offset"] is not None
                    and t - self.enemy_appear_t <= 0.3 and self._gun_out(t)
                    and enemy["head_offset"] >= cc.get("min_offset_px", -60)
                    # A standing enemy is tall. A wide or square outline is a teammate's marker icon
                    # or a dead body (19 Nov video: these gave +46 to +126 px "too low" readings).
                    and (enemy.get("aspect") or 9.0) >= cc.get("min_aspect", 1.15)
                    # Most highlight colour near the crosshair must be this person. Else the scene is
                    # busy (yellow plants on Lotus, 19 Nov custom game: +117 to +267 px readings).
                    and (enemy.get("share") or 1.0) >= cc.get("min_share", 0.5)):
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
                start = prev_t if prev_t is not None else shot_t
                stat = self._first_shot_error(start, t)
                keys = self._judge_keys(prev_t, shot_t)
                if stat is None:
                    # The stat is on the screen, but the value of this shot has not shown yet. It shows
                    # for about 0.1 s, sometimes after the ammo changes. Wait a moment for it (17 Nov:
                    # 13 of 40 shots in an unrated match had "no reading in time").
                    self.pending_shots.append({"start": start, "shot_t": shot_t, "prev_t": prev_t, "keys": keys,
                                               "mag": self.ammo.mag, "click": getattr(self, "last_click", None),
                                               "click_t": getattr(self, "last_shot_t", None), "until": t + STAT_WAIT_S})
                    continue
                self._judge_first_shot(shot_t, prev_t, keys, stat, self.ammo.mag)
            elif kind == "burst_end":
                limit = spray_limit(lc, data.get("mag"))
                if limit is not None and data["shots"] > limit:
                    self.round.long_sprays += 1
            elif kind == "reload_done":
                had_ammo = data["before"] >= rc["min_ammo_left_ratio"] * data["after"]
                in_fight = any(t - rc["window_s"] <= d <= t for d in self.round.damage_times)
                if had_ammo and in_fight:
                    self.round.bad_reloads += 1

    def _check_infinite_ammo(self, ammo):
        """The Range starts with Infinite Ammo on. Then the ammo count never goes down, and the
        coach finds no shots. Many fire presses with the same ammo count: tell the player once."""
        if ammo is None or not self._keys_on() or getattr(self, "_inf_ammo_said", False) or self.match.in_match:
            # In a match, buy-menu clicks also count as fire presses. The Range never starts a match.
            return
        n = getattr(self.inputs, "fire_count", 0)
        if ammo != getattr(self, "_inf_ammo_value", None):
            self._inf_ammo_value, self._inf_ammo_fires = ammo, n
        elif n - self._inf_ammo_fires >= INFINITE_AMMO_FIRES:
            self._inf_ammo_said = True
            print(f"[shot] You pressed fire {n - self._inf_ammo_fires} times, but the ammo count stayed at {ammo}. "
                  "Is Infinite Ammo on? In the Range, turn it off in the Range settings. The coach finds "
                  "shots from the ammo count.")

    def _keys_on(self):
        return self.inputs is not None and self.inputs.active

    def _judge_keys(self, prev_t, shot_t):
        """Judge the first shot from your keys. The fire-button press gives the exact shot time."""
        if not self._keys_on():
            return None
        start = (prev_t if prev_t is not None else shot_t) - 0.1   # The screen shows the shot a bit late.
        fire = self.inputs.first_fire(start, shot_t)
        when = fire if fire is not None else ((prev_t if prev_t is not None else shot_t) + shot_t) / 2.0
        self.last_shot_t = when
        self.last_click = fire is not None
        return self.inputs.judge(when, self.c.get("counter_strafe") or {})

    def _judge_first_shot(self, shot_t, prev_t, keys, stat, mag, click=True, click_t=None):
        """Judge the first shot of a burst: from the stat, else the keys, else the motion score."""
        mc = self.c["moving_shots"]
        if keys is not None and not keys["moving"]:
            self.round.stops += 1
            self.round.counter_stops += int(keys["counter"])
            if keys["gap_ms"] is not None:
                self.round.stop_gaps_ms.append(keys["gap_ms"])
        judged, moving = self.round.judged_bursts, self.round.moving_bursts
        line = None
        if self._keys_on():
            line = ("[shot] " + describe_shot(keys, stat) + f" Keys (ms before the shot): {self.inputs.timeline(click_t if click_t is not None else self.last_shot_t)}."
                    + ("" if click else " No click found: the shot time is a guess."))
        try:
            self._count_first_shot(shot_t, prev_t, keys, stat, mag)
        finally:
            if line:
                # The stat decides when it is on the screen, so the key word above can differ (19 Nov).
                if self.round.judged_bursts == judged:
                    line += " Counted: not judged."
                else:
                    line += " Counted: " + ("moving." if self.round.moving_bursts > moving else "still.")
                print(line)

    def _count_first_shot(self, shot_t, prev_t, keys, stat, mag):
        mc = self.c["moving_shots"]
        if stat and mag in mc.get("stat_skip_magazines", (2, 5, 7)):
            stat = None        # Shotguns and sniper rifles have a wide spread when you stand still.
        if self.on_shot:
            self.on_shot(keys, None if stat is False else stat, self._keys_on())
        if stat is not False and (stat is not None or not self._keys_on()):
            # Valorant's own Shooting Error stat is on the screen. It is better than the motion guess.
            if stat is not None:
                self.round.judged_bursts += 1
                if stat > mc.get("error_threshold_deg", 1.0):
                    self.round.moving_bursts += 1
            return
        # No stat value for this shot: your keys decide (when the key reader is on).
        if keys is not None or self._keys_on():
            # Your movement keys: no key near the shot means you stood still.
            self.round.judged_bursts += 1
            self.round.moving_bursts += int(bool(keys and keys["moving"]))
            return
        start = (prev_t if prev_t is not None else shot_t) - mc["lookback_s"]
        values = [m for mt, m in self.motion if start <= mt <= shot_t and m is not None]
        if not values and self.motion_lookup:
            values = self.motion_lookup(start, shot_t)
        if values:
            self.round.judged_bursts += 1
            if median(values) > mc["motion_threshold"]:
                self.round.moving_bursts += 1

    def _resolve_pending(self, t, force=False):
        """Judge the shots that wait for their stat value."""
        keep = []
        for p in self.pending_shots:
            stat = self._first_shot_error(p["start"], p["shot_t"] + STAT_WAIT_S)
            if stat is None and not force and t < p["until"]:
                keep.append(p)
                continue
            self._judge_first_shot(p["shot_t"], p["prev_t"], p["keys"], stat, p["mag"], p["click"], p["click_t"])
        self.pending_shots = keep

    def on_shot_error(self, t, value):
        """A Shooting Error reading. The live coach reads it on every frame: one value shows for only 0.1 s."""
        if value is None:
            return
        self.shot_errors.append((t, value))
        self.shot_error_seen_t = t
        while self.shot_errors and t - self.shot_errors[0][0] > 4.0:
            self.shot_errors.popleft()
        if self.pending_shots:
            self._resolve_pending(t)

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
            self.on_shot_error(t, shot_error)
        if self.pending_shots:
            self._resolve_pending(t)
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
        self._check_infinite_ammo(ammo)
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

    def _gun_out(self, t):
        return self.ammo.last is not None and self.ammo.last_t is not None and t - self.ammo.last_t <= 0.5

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
        self._resolve_pending(t, force=True)
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
        elif kind == "timer":
            events = self.match.on_timer(t, value)
            if not self.health.alive and self.match.clock.phase == "buy":
                self.health.reset()            # You are alive again in the buy phase.
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
            if name == "score_sync":
                self._close_round(t)           # Rounds were missed. Start the count again.
                self.rounds.pop()
            out.append((name, data, self._close_round(t) if name == "round_end" else None))
        return out

    def new_match(self):
        self.rounds = []
        self.round = RoundStats()

    def clear_round(self):
        """Drop what was counted before the first round, for example in agent select."""
        self.round = RoundStats()
        self.ammo.reset()
