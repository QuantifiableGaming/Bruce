"""Your numbers over many matches.

After each match the coach adds one line to reports/history.jsonl. The debrief then compares
this match with your last matches, and the Control Panel shows the list on the Reports page.
"""
import json
import os
from datetime import datetime
from statistics import median

PATH = os.path.join("reports", "history.jsonl")
COMPARE_LAST = 5        # The debrief compares with this many earlier matches.
MIN_EARLIER = 2         # It needs at least this many earlier matches.

# name: (label, how to compute it, smaller is better, minimum data to use it)
METRICS = {
    "moving": ("first shots while moving", lambda m: _rate(m["moving"], m["judged"]), True, ("judged", 5)),
    "long_sprays": ("long sprays", lambda m: _rate(m["long_sprays"], m["bursts"]), True, ("bursts", 5)),
    "deaths": ("deaths per round", lambda m: _rate(m["deaths"], m["rounds"], pct=False), True, ("rounds", 5)),
    "no_counter": ("stops without a counter-strafe", lambda m: _rate(m["stops"] - m["counter"], m["stops"]), True, ("stops", 5)),
    "bad_reloads": ("bad reloads per round", lambda m: _rate(m["bad_reloads"], m["rounds"], pct=False), True, ("rounds", 5)),
}
PERCENT = ("moving", "long_sprays", "no_counter")


def _rate(part, whole, pct=True):
    if not whole:
        return None
    return round(100.0 * part / whole) if pct else round(part / float(whole), 2)


def summarize(rounds, result, won, lost, personality=None, when=None):
    """One history line from the RoundStats of one match."""
    s = lambda k: sum(getattr(r, k) for r in rounds)
    offsets = [o for r in rounds for o in r.crosshair_offsets]
    return {
        "date": (when or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        "result": result or "", "won": won, "lost": lost, "rounds": len(rounds),
        "deaths": s("deaths"), "bursts": s("bursts"), "judged": s("judged_bursts"),
        "moving": s("moving_bursts"), "long_sprays": s("long_sprays"), "bad_reloads": s("bad_reloads"),
        "stops": s("stops"), "counter": s("counter_stops"),
        "fights": len(offsets), "crosshair": round(median(offsets), 1) if offsets else None,
        "personality": personality or "",
    }


def load(path=PATH):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass   # A broken line (for example a crash during writing) is skipped.
    return out


def add(entry, path=PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def add_season(name, path=PATH, when=None):
    """Mark the start of a new competitive season. Trends then compare only matches after it."""
    add({"season": str(name).strip()[:40] or "New season", "date": (when or datetime.now()).strftime("%Y-%m-%d %H:%M")}, path)


def matches(entries):
    return [e for e in entries if "season" not in e]


def this_season(entries):
    """The matches after the newest season mark (all matches when there is no mark)."""
    for i in range(len(entries) - 1, -1, -1):
        if "season" in entries[i]:
            return matches(entries[i + 1:])
    return matches(entries)


def rates(entry):
    """The metrics of one match, as numbers (percent or per round). None when there is too little data."""
    out = {}
    for name, (_, fn, _, (need_key, need)) in METRICS.items():
        out[name] = fn(entry) if entry.get(need_key, 0) >= need else None
    return out


def trend(current, earlier):
    """One sentence about the biggest change against your earlier matches, or None.

    earlier is the history list. Only matches in the current season count.
    """
    earlier = this_season(earlier)[-COMPARE_LAST:]
    if len(earlier) < MIN_EARLIER:
        return None
    now = rates(current)
    best = None
    for name, (label, _, smaller_better, _) in METRICS.items():
        before = [rates(e)[name] for e in earlier]
        before = [b for b in before if b is not None]
        if now[name] is None or len(before) < MIN_EARLIER:
            continue
        avg = sum(before) / len(before)
        change = now[name] - avg
        # Compare changes of different metrics on one scale: percent points, or deaths x 100.
        size = abs(change) if name in PERCENT else abs(change) * 100
        if size < 8:
            continue
        better = (change < 0) == smaller_better
        if best is None or size > best[0]:
            best = (size, name, label, now[name], avg, better, len(before))
    if best is None:
        return None
    _, name, label, value, avg, better, n = best
    if name in PERCENT:
        text = f"{value} percent this match, against {round(avg)} percent in your last {n} matches"
    else:
        text = f"{value:.1f} this match, against {avg:.1f} in your last {n} matches"
    first = "Progress:" if better else "Watch this:"
    return f"{first} {label.capitalize()}, {text}."


def table(entries, last=20):
    """Rows for the Control Panel: newest first."""
    rows = []
    for e in reversed(entries[-last:]):
        if "season" in e:
            rows.append({"season": e["season"], "date": e.get("date", "")})
            continue
        r = rates(e)
        rows.append({"date": e.get("date", ""), "result": e.get("result", ""),
                     "score": f"{e.get('won', 0)}-{e.get('lost', 0)}", "rounds": e.get("rounds", 0), **r,
                     "crosshair": e.get("crosshair"), "combat": e.get("board_score"),
                     "kda": (f"{e['board_kills']}/{e['board_deaths']}/{e['board_assists']}"
                             if "board_kills" in e else None)})
    return rows


def update_last(fields, path=PATH):
    """Add fields (for example the scoreboard numbers) to the newest match in the history."""
    entries = load(path)
    for i in range(len(entries) - 1, -1, -1):
        if "season" not in entries[i]:
            entries[i].update(fields)
            break
    else:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp, path)
