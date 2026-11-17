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


def history_lines(path, before_date=None, last=8):
    entries = history.this_season(history.load(path)) if os.path.exists(path) else []
    if before_date:
        entries = [e for e in entries if e.get("date", "") < before_date]
    keep = ("date", "result", "won", "lost", "rounds", "deaths", "bursts", "judged", "moving",
            "stops", "counter", "long_sprays", "bad_reloads", "crosshair", "board_score",
            "board_kills", "board_deaths", "board_assists")
    return [json.dumps({k: e[k] for k in keep if e.get(k) not in (None, "")}) for e in entries[-last:]]


def table_facts(report_text):
    """Count the round table, so the AI does not have to add up numbers (17 Nov: it wrote
    19 moving bursts for 17, and "0 of 1 counter-strafes" as a success)."""
    rows, col = [], None
    for line in report_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "Round" and "Moving" in cells:
            col = {name: i for i, name in enumerate(cells)}      # The crosshair check adds columns.
        elif col and cells and cells[0].isdigit() and len(cells) >= len(col) - 1:
            rows.append({name: cells[i] if i < len(cells) else "" for name, i in col.items()})
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


def build_prompt(report_text, hist):
    out = ["Match report:", _report_body(report_text).strip()]
    facts = table_facts(_report_body(report_text))
    if facts:
        out += [""] + facts
    if hist:
        out += ["", "History (older first, this season):"] + hist
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
    hist = history_lines(history_path or os.path.join(os.path.dirname(path), "history.jsonl"), date)
    review = clean(client.complete(SYSTEM, build_prompt(text, hist), timeout=timeout, max_tokens=900))
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
