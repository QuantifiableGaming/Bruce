"""A written match review by the AI coach, added to the end of the match report.

One AI request for each match, after the match. It reads the report (the round table, the
scoreboard numbers and the marked moments) and your last matches from the history. It
writes a short review: what went well, the two most important things to work on, with a
drill for each, and a plan for the next session.

It works with each AI provider. With the free GLM model it uses one of the 50 free requests
of the day. Slow answers are fine here, because nobody waits for it in a round.
The AI gets text only: the report and the history numbers. It never gets screenshots.
"""
import json
import os
import re
import threading

from . import history

HEADING = "## Coach analysis (AI)"

SYSTEM = """You are an experienced Valorant aim and mechanics coach. You write a short review of one match for the player.
The data comes from a coach program that watches only the player's screen. It measures:
- Bursts: times the player fired. Moving: first shots fired while moving (from Valorant's Shooting Error stat or the movement keys).
- Counter-strafes "X of Y": of Y stops before a first shot, X used a tap on the opposite key.
- Long sprays: sprays longer than the limit for the weapon. Bad reloads: reloads with ammo left that were followed by damage.
- Crosshair median (px, + = low): how far the crosshair sat from the enemy head height when an enemy appeared. Positive means too low.
- Scoreboard: combat score, kills, deaths, assists and more, from the end-of-match screen.
- History: the player's recent matches, with the same numbers.
Rules:
- Use only the numbers given. Do not invent numbers, agents, weapons, maps or events. If data is missing or small, say so in one short line.
- For totals, use the "Counted from the round table" lines. When you name a round, copy its numbers from its own row.
- A "Result: STOPPED" match was stopped before it ended: do not call it a win or a loss.
- The program cannot see positions, utility use or team play. Do not give advice about those.
- "Moving" counts bad shots: fewer bursts while moving is better. A low share of moving bursts is a good habit, never
  a problem to fix.
- The program cannot see kills. Do not praise kills or combat. Round wins in a custom game or a match with no
  scoreboard say nothing about skill.
- Use the "Your trend" lines for any comparison with recent matches. Copy their numbers and their "better" or
  "worse" word. Never write your own comparison, and never invent a number for a match that is not listed.
- For the crosshair, use the "Crosshair" line. A positive median means the crosshair sat BELOW head height (too low).
  Never call a positive median "high". Do not quote a round median over 100 px as a fact: say that the number is unsure.
- The "Moving" column counts first shots fired while moving. A stop before a shot shows only in the "Counter-strafes"
  column. An empty Counter-strafes cell means no stop was measured in that round: do not say the player stopped there.
- Give advice only about a number that shows a problem. A number that is missing, empty, "-", 0 of 0, or not
  measured shows nothing: never put it under "Work on next" and never tell the player to fix it or to turn on tracking.
- If no measured number shows a problem, write under "Work on next" that the data showed no clear problem, say in one
  line which numbers had too little data, and give one drill to keep the best habit. Do not make up a weakness.
- Write in plain, short sentences. Address the player as "you".
- Format: Markdown, with these headings in this order, under 280 words in total:
### Summary
(2 or 3 sentences)
### What went well
(2 or 3 bullets, each tied to a number)
### Work on next
(up to 2 habits that a number shows a problem with, each as a bullet: the number, why it matters, and one Range or Deathmatch drill with a count or a time)
### Compared with your recent matches
(1 to 3 bullets. Leave this heading out when there is no history.)
### Clips to post
(Only when the report has a "Clips to post" table. One bullet for each clip, in the same order: the clock time and
moment, a hook of at most 8 words for the first second of a Short, a one-line caption, and 3 to 5 hashtags: #valorant,
the moment type, #gamingclips, and #shorts. The report does not say what happens in a clip: do not describe the play
(no "you miss everything"). Use only the round, the score and the moment type. Leave this heading out
when there are no clips. This part does not count in the word limit.)"""


def _report_body(text):
    """The report without an earlier AI analysis (for a new analysis of the same report)."""
    i = text.find(HEADING)
    return text[:i].rstrip() + "\n" if i >= 0 else text


def history_lines(path, before_date=None, last=8, mode=None):
    entries = history.this_season(history.load(path)) if os.path.exists(path) else []
    entries = history.same_kind(entries, mode)
    if before_date:
        entries = [e for e in entries if e.get("date", "") < before_date]
    keep = ("date", "result", "won", "lost", "rounds", "deaths", "bursts", "judged", "moving",
            "stops", "counter", "long_sprays", "bad_reloads", "crosshair", "board_score",
            "board_kills", "board_deaths", "board_assists")
    return [json.dumps({k: e[k] for k in keep if e.get(k) not in (None, "")}) for e in entries[-last:]]


def report_mode(text):
    """The mode word from the "Mode:" line of a report ("Mode: Competitive (ranked)" -> "competitive")."""
    m = re.search(r"^Mode: (\w+)", text, re.M)
    return m.group(1).lower() if m else None


def match_entry(path, date):
    """(the history line of the match that started at date, the earlier lines of this season)."""
    if not date or not os.path.exists(path):
        return None, []
    entries = history.this_season(history.load(path))
    after = [e for e in entries if e.get("date", "") >= date]
    current = after[0] if after else None
    before = [e for e in history.same_kind(entries, (current or {}).get("mode")) if e.get("date", "") < date]
    return current, before


def deaths_line(report_text, current):
    """The coach can miss a death. The scoreboard count is the true one."""
    board = (current or {}).get("board_deaths")
    if not isinstance(board, int):
        return None
    counted = sum(int(r["Deaths"]) for r in round_rows(report_text)[0] if r.get("Deaths", "").isdigit())
    if counted >= board:
        return None
    return (f"- Deaths: the scoreboard shows {board}, the round table counted {counted}. Use {board}. The coach "
            f"missed some deaths, so do not judge the rounds by the Deaths column.")


def round_rows(report_text):
    """The rows of the round table as dicts by column name, and the column map (None when no table)."""
    rows, col = [], None
    for line in report_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "Round" and "Moving" in cells:
            col = {name: i for i, name in enumerate(cells)}      # The crosshair check adds columns.
        elif col and cells and cells[0].isdigit() and len(cells) >= len(col) - 1:
            rows.append({name: cells[i] if i < len(cells) else "" for name, i in col.items()})
    return rows, col


def table_facts(report_text):
    """Count the round table, so the AI does not have to add up numbers (17 Nov: it wrote
    19 moving bursts for 17, and "0 of 1 counter-strafes" as a success)."""
    rows, col = round_rows(report_text)
    need = ("Result", "Deaths", "Bursts", "Moving", "Counter-strafes", "Long sprays", "Bad reloads")
    if not rows or any(n not in col for n in need):
        return []

    def num(c):
        try:
            return int(c)
        except ValueError:
            return 0
    moving = [(r["Round"], num(r["Moving"])) for r in rows]
    cs = [re.match(r"(\d+) of (\d+)", r["Counter-strafes"]) for r in rows]
    cs_done = sum(int(m[1]) for m in cs if m)
    cs_all = sum(int(m[2]) for m in cs if m)
    out = ["Counted from the round table (use these totals, do not add up the table yourself):",
           f"- Rounds: {len(rows)}. Won: {sum(r['Result'].startswith('Won') for r in rows)}. Deaths: {sum(num(r['Deaths']) for r in rows)}.",
           f"- Bursts: {sum(num(r['Bursts']) for r in rows)}. Bursts while moving: {sum(m for _, m in moving)}.",
           f"- Rounds with no moving burst: {', '.join(n for n, m in moving if m == 0) or 'none'}.",
           f"- Counter-strafes: {cs_done} of {cs_all} stops used a counter-strafe "
           f"(\"X of Y\" means X good stops out of Y; 0 of 1 means the one stop had no counter-strafe).",
           f"- Long sprays: {sum(num(r['Long sprays']) for r in rows)}. Bad reloads: {sum(num(r['Bad reloads']) for r in rows)}."]
    return out


def crosshair_line(report_text, tolerance=18, overall=None, fights=None):
    """One overall crosshair number with its direction, so the AI does not mix up "high" and "low".
    overall is the true median of all fights (from the history line). Without it, the round medians
    are used, weighted by fights."""
    if isinstance(overall, (int, float)):
        return _crosshair_text(overall, fights, tolerance)
    rows, col = round_rows(report_text)
    key = next((k for k in (col or {}) if k.startswith("Crosshair median")), None)
    meds, fights = [], 0
    for r in rows if key else []:
        try:
            v = float(r[key])
        except ValueError:
            continue
        n = int(r["Fights"]) if r.get("Fights", "").isdigit() and int(r["Fights"]) > 0 else 1
        meds += [v] * n                     # Weight each round by its number of fights.
        fights += n
    if not meds:
        return None
    meds.sort()
    m = meds[len(meds) // 2] if len(meds) % 2 else (meds[len(meds) // 2 - 1] + meds[len(meds) // 2]) / 2
    return _crosshair_text(m, fights, tolerance)


def _crosshair_text(m, fights, tolerance):
    if m > tolerance:
        where = f"below head height (too low by about {round(m)} px)"
    elif m < -tolerance:
        where = f"above head height (too high by about {round(-m)} px)"
    else:
        where = "at head height (good)"
    over = f" over {fights} fights" if fights else ""
    return f"- Crosshair: overall median {m:+.1f} px{over}. That is {where}. Good is within {tolerance} px."


def trend_lines(current, earlier, last=5):
    """Each metric of this match against the mean of the earlier matches, worked out here (19 Nov: the
    AI wrote "long sprays above your average" when the coach said 16 percent against 27 percent)."""
    earlier = [e for e in earlier if "season" not in e][-last:]
    if not current or not earlier:
        return []
    now = history.rates(current)
    kind = history.mode_kind(current.get("mode"))
    kind = {"ranked": "ranked ", "casual": "casual "}.get(kind, "")
    out = [f"Your trend (this match against the mean of your last {len(earlier)} {kind}matches, worked out for you):"]
    for name, (label, _, smaller_better, _) in history.METRICS.items():
        before = [r for r in (history.rates(e)[name] for e in earlier) if r is not None]
        if now[name] is None or not before:
            continue
        avg = sum(before) / len(before)
        unit = " percent" if name in history.PERCENT else ""
        fmt = (lambda v: f"{round(v)}") if name in history.PERCENT else (lambda v: f"{v:.2f}")
        small = abs(now[name] - avg) < (5 if name in history.PERCENT else 0.1)
        word = "about the same" if small else ("better" if (now[name] < avg) == smaller_better else "worse")
        out.append(f"- {label.capitalize()}: {fmt(now[name])}{unit} this match, {fmt(avg)}{unit} before: {word}.")
    cur = current.get("crosshair")
    old = [e["crosshair"] for e in earlier if isinstance(e.get("crosshair"), (int, float))]
    if isinstance(cur, (int, float)) and old:
        avg = sum(old) / len(old)
        word = "about the same" if abs(abs(cur) - abs(avg)) < 5 else ("better" if abs(cur) < abs(avg) else "worse")
        out.append(f"- Crosshair median: {cur:+.1f} px this match, {avg:+.1f} px before (+ = too low): {word}.")
    return out if len(out) > 1 else []


def build_prompt(report_text, hist, trend=None, current=None, kind=""):
    body = _report_body(report_text)
    out = ["Match report:", body.strip()]
    facts = table_facts(body)
    cur = current or {}
    extra = [crosshair_line(body, overall=cur.get("crosshair"), fights=cur.get("fights")), deaths_line(body, current)]
    extra = [x for x in extra if x]
    if extra:
        facts = (facts or ["Counted from the round table:"]) + extra
    if facts:
        out += [""] + facts
    if trend:
        out += [""] + trend
    if hist:
        only = {"ranked": ", ranked matches only", "casual": ", casual matches only (Unrated and Swiftplay)",
                "other": ", matches of the same type only"}.get(kind, "")
        out += ["", f"History (older first, this season{only}):"] + hist
    else:
        out += ["", "History: none yet."]
    return "\n".join(out)


def clean(text):
    """Keep the Markdown, but drop a code fence and any top-level heading the model adds."""
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    text = re.sub(r"^#{1,2} .*\n", "", text)
    return text.strip()


def analyze_report(path, client, history_path=None, timeout=90):
    """Ask the AI for a review and add it to the end of the report. Returns the text, or None."""
    if not client or not client.enabled:
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    date = None
    m = re.search(r"match_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})", os.path.basename(path))
    if m:
        date = f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}"
    hpath = history_path or os.path.join(os.path.dirname(path), "history.jsonl")
    current, before = match_entry(hpath, date)
    mode = (current or {}).get("mode") or report_mode(text)
    if re.search(r"^Result: STOPPED", text, re.M) or history.mode_kind(mode) == "other":
        # 19 Nov: a stopped custom game was compared with ranked matches. A part of a match, or a
        # custom game, Deathmatch or other mode, gets no comparison.
        current, before, hist = None, [], []
    else:
        hist = history_lines(hpath, date, mode=mode)
    prompt = build_prompt(text, hist, trend_lines(current, before), current, history.mode_kind(mode))
    review = clean(client.complete(SYSTEM, prompt, timeout=timeout, max_tokens=900))
    if len(review.split()) < 20:
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()                     # The scoreboard may have been added in the meantime.
    model = getattr(client, "model", "")
    body = _report_body(text).rstrip() + f"\n\n{HEADING}\n\n_Written by {model}. It uses only the numbers above._\n\n{review}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return review


class Analyst:
    """Runs analyze_report in a background thread. wait() lets the coach finish it before it exits."""

    def __init__(self, client, timeout=90):
        self.client = client
        self.timeout = timeout
        self.thread = None
        self.hurry = threading.Event()

    def start(self, path, history_path=None, delay_s=0.0):
        self.hurry.clear()

        def run():
            try:
                self.hurry.wait(delay_s)    # Wait for the scoreboard numbers, unless the coach stops.
                if analyze_report(path, self.client, history_path, self.timeout):
                    print(f"[analysis] The AI review is at the end of {path}")
                else:
                    print("[analysis] The AI gave no usable review.")
            except Exception as exc:
                print(f"[analysis] No AI review: {exc}")

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def wait(self):
        self.hurry.set()
        if self.thread and self.thread.is_alive():
            print("[analysis] Waiting for the AI review...")
            self.thread.join(self.timeout + 5)
