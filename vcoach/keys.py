"""Your movement keys and your fire button, with the time of each press and release.

The coach uses this to judge the first shot of each burst:
- moving: a movement key was held when you shot, or you let go of it just before the shot.
- counter-strafe: you stopped by a short tap on the opposite key (A then D, or D then A).
- slow stop: you let go of the key and waited for your agent to stop.

The listener only records which of these keys are down and when. It does not record
other keys, and it does not send keys to the game. Times use time.perf_counter(),
the same clock as the screen capture.
"""
import threading
import time
from collections import deque

AXES = {"side": ("left", "right"), "front": ("back", "forward")}


class InputLog:
    """Key and fire-button times for the last few seconds."""

    def __init__(self, keep_s=5.0, clock=time.perf_counter):
        self.keep_s = keep_s
        self.clock = clock
        self.lock = threading.Lock()
        self.down = {}               # name -> press time, for keys that are down now
        self.spans = {n: deque() for a in AXES.values() for n in a}   # name -> (start, end)
        self.fires = deque()
        self.fire_count = 0          # All fire presses since the start (the deque keeps only 5 s).
        self.fire_spans = deque()    # (press, release) of the fire button. Release None: held now.
        self.reloads = deque()       # Reload key presses.
        self.switches = deque()      # Weapon and ability key presses (1, 2, 3, 4, Q, E, C, X, mouse wheel).
        self.abilities = deque()     # (time, slot) for the ability keys: slot is c, q, e or x (the death review).
        self.active = False          # True after the first key or click. Replays never set it.

    # --- Input from the listener (or from tests) ---

    def press(self, name, t=None):
        t = self.clock() if t is None else t
        with self.lock:
            self.active = True
            if name == "reload":
                self.reloads.append(t)
                self._trim(t)
                return
            if name == "switch" or name.startswith("ability_"):
                self.switches.append(t)
                if name.startswith("ability_"):
                    slot = name[len("ability_"):]
                    self.abilities.append((t, slot))
                    # Debug log only: shows which key each agent's ability uses (25 Nov test of agent_abilities.yaml).
                    print(f"[ability] {slot.upper()} pressed")
                self._trim(t)
                return
            if name == "fire":
                self.fires.append(t)
                self.fire_count += 1
                if not self.fire_spans or self.fire_spans[-1][1] is not None:
                    self.fire_spans.append((t, None))
                self._trim(t)
                return
            if name in self.down:
                return               # Windows repeats a held key. Keep the first press time.
            self.down[name] = t

    def release(self, name, t=None):
        t = self.clock() if t is None else t
        with self.lock:
            if name == "fire":
                if self.fire_spans and self.fire_spans[-1][1] is None:
                    self.fire_spans[-1] = (self.fire_spans[-1][0], t)
                return
            start = self.down.pop(name, None)
            if start is not None and name in self.spans:
                self.spans[name].append((start, t))
                self._trim(t)

    def _trim(self, t):
        for q in list(self.spans.values()) + [self.fires, self.reloads, self.switches, self.fire_spans]:
            while q and q[0] is not None and (q[0][1] if isinstance(q[0], tuple) else q[0]) is not None \
                    and t - (q[0][1] if isinstance(q[0], tuple) else q[0]) > self.keep_s:
                q.popleft()
        while self.abilities and t - self.abilities[0][0] > self.keep_s:
            self.abilities.popleft()

    # --- Questions from the analyzer ---

    def first_fire(self, start, end):
        """The first fire-button press in [start, end], or None."""
        with self.lock:
            for f in self.fires:
                if start <= f <= end:
                    return f
        return None

    def fire_active(self, start, end):
        """True when the fire button was down at some moment in [start, end]."""
        with self.lock:
            return any(a <= end and (b is None or b >= start) for a, b in self.fire_spans)

    def switch_pressed(self, start, end):
        """True when a weapon or ability key (or the mouse wheel) was used in [start, end]."""
        with self.lock:
            return any(start <= x <= end for x in self.switches)

    def abilities_used(self, start, end):
        """The ability slots (c, q, e, x) pressed in [start, end]."""
        with self.lock:
            return {slot for x, slot in self.abilities if start <= x <= end}

    def reload_pressed(self, start, end):
        with self.lock:
            return any(start <= r <= end for r in self.reloads)

    def _key_spans(self, name, until):
        out = list(self.spans[name])
        if name in self.down and self.down[name] <= until:
            out.append((self.down[name], None))   # Still held.
        return out

    def _segments(self, axis, until):
        """The movement direction on one axis as segments (start, end, +1 or -1). end None: still on.

        Both keys held at the same time cancel out, as in the game.
        """
        neg, pos = AXES[axis]
        points = set()
        spans = {neg: self._key_spans(neg, until), pos: self._key_spans(pos, until)}
        for lst in spans.values():
            for a, b in lst:
                points.add(a)
                if b is not None and b <= until:
                    points.add(b)
        points = sorted(p for p in points if p <= until) + [until]

        def held(name, x):
            return any(a <= x and (b is None or x < b) for a, b in spans[name])

        segs = []
        for a, b in zip(points, points[1:]):
            if b <= a:
                continue
            mid = (a + b) / 2.0
            d = (1 if held(pos, mid) else 0) - (1 if held(neg, mid) else 0)
            if segs and segs[-1][2] == d and abs(segs[-1][1] - a) < 1e-9:
                segs[-1] = (segs[-1][0], b, d)
            elif d:
                segs.append((a, b, d))
        # The last segment is "still on" when it reaches the shot time.
        return [(a, None if abs(b - until) < 1e-9 else b, d) for a, b, d in segs]

    def timeline(self, shot_t, window=0.8):
        """The keys near a shot, for the log. Times are in ms before the shot, for example
        "A 640-120, D 110-40" (A held from 640 ms to 120 ms before the shot, then a D tap).
        "now" means the key was still down at the shot."""
        short = {"forward": "W", "left": "A", "back": "S", "right": "D"}
        parts = []
        with self.lock:
            for name, letter in short.items():
                for a, b in self._key_spans(name, shot_t):
                    if b is not None and shot_t - b > window:
                        continue
                    start = int(round((shot_t - a) * 1000))
                    end = "now" if b is None or b > shot_t else str(int(round((shot_t - b) * 1000)))
                    parts.append((a, f"{letter} {start}-{end}"))
        return ", ".join(p for _, p in sorted(parts)) or "none"

    def judge(self, shot_t, cfg):
        """How you stopped before a shot.

        Returns None when no movement key was used near the shot, else a dict:
          moving   True when you were still moving at the shot.
          counter  True when you stopped with a tap on the opposite key.
          gap_ms   Time from the stop to the shot (only for stops).
          both     True when you stopped by holding both keys of one axis (for example A and D).
                   That cancels the movement like a key release. It is not a counter-strafe.
        """
        recent = cfg.get("recent_s", 0.4)            # A stop this long before the shot counts.
        counter_max = cfg.get("counter_tap_max_s", 0.16)
        counter_min = cfg.get("counter_min_s", 0.06)  # Less time than this from the tap to the shot: still moving.
        coast = cfg.get("release_still_moving_s", 0.09)
        with self.lock:
            if not self.active:
                return None
            results = []
            for axis in AXES:
                segs = self._segments(axis, shot_t)
                if not segs:
                    continue
                a, b, d = segs[-1]
                prev = segs[-2] if len(segs) >= 2 else None
                follows_opposite = prev is not None and prev[2] == -d and prev[1] is not None and a - prev[1] <= 0.05
                if b is None:
                    # A key is down at the shot.
                    if follows_opposite and counter_min <= shot_t - a <= counter_max:
                        results.append({"moving": False, "counter": True, "gap_ms": 0})
                    else:
                        results.append({"moving": True, "counter": False, "gap_ms": None})
                    continue
                if shot_t - b > recent:
                    continue                          # You stood still before the shot.
                gap = int(round((shot_t - b) * 1000))
                neg, pos = AXES[axis]
                both = all(any(x <= shot_t and (y is None or y >= shot_t) for x, y in self._key_spans(n, shot_t))
                           for n in (neg, pos))
                if follows_opposite and b - a <= counter_max and shot_t - a >= counter_min:
                    # The last segment was the short tap on the opposite key.
                    results.append({"moving": False, "counter": True, "gap_ms": gap})
                else:
                    results.append({"moving": shot_t - b < coast, "counter": False, "gap_ms": gap, "both": both})
        if not results:
            return None
        moving = any(r["moving"] for r in results)
        stops = [r for r in results if not r["moving"]]
        out = {"moving": moving,
               "counter": (not moving) and any(r["counter"] for r in stops),
               "gap_ms": None if moving else min(r["gap_ms"] for r in stops)}
        if not moving and not out["counter"] and any(r.get("both") for r in stops):
            out["both"] = True
        return out


def _key_name(value):
    return str(value or "").strip().lower()


def start_listener(cfg, log):
    """Start the keyboard and mouse listeners. Returns a list of listeners, or [] when off."""
    kc = cfg.get("keys", {}) or {}
    if not kc.get("enabled", True):
        return []
    try:
        from pynput import keyboard, mouse
    except ImportError:
        print("[keys] pynput is not installed. The movement-key check is off.")
        return []
    names = {_key_name(kc.get(n, d)): n for n, d in
             (("forward", "w"), ("left", "a"), ("back", "s"), ("right", "d"), ("reload", "r"))}
    fire_button = _key_name(kc.get("fire", "mouse_left"))
    # Keys that change what is in your hands: weapons 1-4 and the abilities Q, E, C, X
    # (Chamber's Headhunter and Tour de Force, for example). The coach only notes the time.
    # The ability keys also keep their slot (C, Q, E, X), so the death review can see a flash or a recon ability.
    for slot, k in (kc.get("abilities") or {"c": "c", "q": "q", "e": "e", "x": "x"}).items():
        names.setdefault(_key_name(k), "ability_" + _key_name(slot))
    for k in kc.get("switch", ["1", "2", "3", "4", "q", "e", "c", "x"]) or []:
        names.setdefault(_key_name(k), "switch")

    def to_name(key):
        char = getattr(key, "char", None)
        if char:
            return names.get(char.lower())
        return names.get(getattr(key, "name", "") or "")

    def on_press(key):
        n = to_name(key)
        if n:
            log.press(n)

    def on_release(key):
        n = to_name(key)
        if n:
            log.release(n)

    button = {"mouse_left": mouse.Button.left, "mouse_right": mouse.Button.right}.get(fire_button)

    def on_click(x, y, b, pressed):
        if button is not None and b == button:
            (log.press if pressed else log.release)("fire")

    def on_scroll(x, y, dx, dy):
        if kc.get("switch_on_scroll", True):
            log.press("switch")      # The mouse wheel changes weapon in Valorant by default.

    out = []
    for listener in (keyboard.Listener(on_press=on_press, on_release=on_release),
                     mouse.Listener(on_click=on_click, on_scroll=on_scroll)):
        listener.daemon = True
        listener.start()
        out.append(listener)
    print("[keys] Movement, reload, weapon and ability keys and the fire button are tracked (times only).")
    return out
