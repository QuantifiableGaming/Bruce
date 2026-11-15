"""Optional AI wording for the coach, with Claude (Anthropic) or ChatGPT (OpenAI).

The coach rules still decide WHEN to speak and WHAT about (a tip, a reset, praise, an answer).
The AI only writes the words, in the personality's style. The AI gets short text facts only:
score, streaks, mistake counts, recent lines, and the player's spoken question. It never gets
screenshots. If the AI is slow, offline, or returns nothing usable, the coach speaks the
scripted line instead.
"""
import json
import os
import queue
import random
import re
import threading
import time
import urllib.error
import urllib.request

DEFAULTS = {
    "claude": {"model": "claude-haiku-5-5", "base_url": "https://api.anthropic.com",
               "env": "ANTHROPIC_API_KEY", "secret": "anthropic_api_key"},
    "chatgpt": {"model": "gpt-5.4-mini", "base_url": "https://api.openai.com",
                "env": "OPENAI_API_KEY", "secret": "openai_api_key"},
}

ISSUE_TEXT = {
    "crosshair_low": "crosshair sits below head height when enemies appear",
    "crosshair_high": "crosshair sits above head height when enemies appear",
    "moving_shots": "shooting while still moving instead of stopping (counter-strafing) first",
    "long_spray": "spraying too long instead of short bursts",
    "bad_reload": "reloading during a fight instead of when safe",
}


class AIError(Exception):
    pass


def load_secrets(path="secrets.yaml"):
    if not os.path.exists(path):
        return {}
    from .util import load_yaml

    return load_yaml(path) or {}


def _post(url, headers, body, timeout):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise AIError(f"HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIError(f"Network problem: {exc}") from None


class AIClient:
    """Sends one prompt to Claude or ChatGPT with plain HTTPS. No extra packages needed."""

    def __init__(self, ai_cfg, secrets=None):
        ai_cfg = ai_cfg or {}
        secrets = secrets or {}
        self.provider = str(ai_cfg.get("provider", "off")).lower()
        self.timeout = float(ai_cfg.get("timeout_s", 4))
        d = DEFAULTS.get(self.provider)
        self.enabled = False
        self.reason = "AI is off."
        if d is None:
            return
        self.model = ai_cfg.get(f"{self.provider}_model") or d["model"]
        self.base_url = (ai_cfg.get(f"{self.provider}_base_url") or d["base_url"]).rstrip("/")
        self.key = (secrets.get(d["secret"]) or os.environ.get(d["env"]) or "").strip()
        self.use_reasoning_param = True
        if not self.key:
            self.reason = f"No API key. Run the AI setup, or set {d['env']}."
            return
        self.enabled = True
        self.reason = ""

    def complete(self, system, user, timeout=None):
        timeout = timeout or self.timeout
        if self.provider == "claude":
            body = {"model": self.model, "max_tokens": 150, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
            data = _post(self.base_url + "/v1/messages", headers, body, timeout)
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        body = {"model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        # Ask reasoning models to answer at once. No token limit here: with a limit, some
        # reasoning models spend it all on hidden reasoning and return an empty reply.
        if self.use_reasoning_param:
            body["reasoning_effort"] = "none"
        try:
            data = _post(self.base_url + "/v1/chat/completions", headers, body, timeout)
        except AIError as exc:
            if "reasoning" in str(exc).lower() and self.use_reasoning_param:
                self.use_reasoning_param = False  # This model does not take the setting.
                body.pop("reasoning_effort", None)
                data = _post(self.base_url + "/v1/chat/completions", headers, body, timeout)
            else:
                raise
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def clean_line(text, max_words=25):
    """Make the reply safe to speak: one short line, no markdown, emoji, or quotes."""
    if not text:
        return None
    text = " ".join(text.split()) if max_words > 40 else (text.strip().splitlines()[0] if text.strip() else "")
    text = re.sub(r"[*_#`>\[\]]", "", text)
    text = "".join(ch for ch in text if ord(ch) < 0x2190 or ch in "’‘“”–—…")
    text = text.strip().strip('"“”\'').strip()
    text = re.sub(r"^(coach|bruce)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    words = text.split()
    if not words:
        return None
    if len(words) > max_words:
        cut = " ".join(words[:max_words])
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[: end + 1] if end > 20 else cut.rstrip(",;:") + "."
    return text


def system_prompt(lines, max_words):
    examples = []
    for key in ("reset", "credit"):
        examples += lines.get(key, [])[:3]
    for tips in list(lines.get("tips", {}).values())[:3]:
        examples += tips[:1]
    examples = "\n".join(f"- {e}" for e in examples[:8])
    return f"""You are "{lines.get('name', 'Coach')}", a voice coach for a Valorant player. Style: {lines.get('description', '')}
You speak through the player's headset between rounds. Your words are read aloud by text-to-speech.

Rules:
- Reply with short sentences, {max_words} words or fewer in total.
- Plain spoken English only: no emoji, no lists, no markdown, no quotation marks, no stage directions.
- Coach habits and mindset only: crosshair height, stopping before shooting, burst control, reload timing, composure.
- Never give tactical calls: no enemy positions, sites, rotations, utility plans, economy or buy advice.
- Use only the facts you are given. Never invent numbers.
- Jokes and teasing are about plays only, never about the player as a person. No swearing or slurs.
- Do not repeat any of the recent lines.
- If the player asks for tactics or anything outside your job, say in character that you only coach habits and mindset.

Lines in your style:
{examples}"""


def build_request(kind, coach, lines, stats=None, issue=None, question=None, extra=None, recent=None):
    """Return the user prompt with the facts for one message."""
    facts = [f"Score: won {coach.won}, lost {coach.lost}. Round number: {coach.round_no}."]
    if coach.loss_streak:
        facts.append(f"Current losing streak: {coach.loss_streak}.")
    if coach.issue_counts:
        common = sorted(coach.issue_counts.items(), key=lambda kv: -kv[1])
        facts.append("Mistakes this match (rounds flagged): " +
                     "; ".join(f"{ISSUE_TEXT.get(k, k)}: {v}" for k, v in common) + ".")
    if stats is not None:
        s = stats.summary()
        facts.append(f"Last round: {s['bursts']} bursts, {s['moving_bursts']} while moving, "
                     f"{s['long_sprays']} long sprays, {s['bad_reloads']} reloads in a fight, "
                     f"{s['fights']} enemy sightings measured.")
    if extra:
        facts.append(extra)
    if recent:
        facts.append("Recent lines (do not repeat): " + " | ".join(recent[-4:]))

    task = {
        "tip": f"Give one short tip to fix this habit: {ISSUE_TEXT.get(issue, issue)}.",
        "focus": f"The player lost the last two rounds. Tell them to focus on one thing this round: {ISSUE_TEXT.get(issue, issue)}.",
        "reset": "The player is on a losing streak or lost a lead. Help them reset their mindset for the next round.",
        "credit": "The player just won a strong round. Give short praise.",
        "match_end": "The match is over. Give one genuine positive from the facts, and a short sign-off.",
        "debrief": "The match is over. Give a short spoken debrief in three or four sentences: the result and score, "
                   "one genuine positive from the facts, one habit to work on next match, and a sign-off.",
        "agent_select": "The player is in agent select before a match. Say one short line to get them ready.",
        "agent": "The player selected an agent in agent select. Say one short line about playing that agent's role "
                 "with good habits. Use the agent name.",
        "answer": f"The player asked you: \"{question}\". Answer from the facts.",
    }.get(kind, "Say something short and useful.")
    return task + "\n\nFacts:\n" + "\n".join(f"- {f}" for f in facts)


class AIVoiceWriter:
    """Runs AI requests one at a time in a background thread, so the game loop never waits.

    Every job has a scripted fallback. The fallback is used when the AI fails, times out,
    or answers too late.
    """

    def __init__(self, client, max_words=25, max_wait_s=8.0, kinds=None):
        self.client = client
        self.max_words = max_words
        self.max_wait = max_wait_s
        self.kinds = set(kinds) if kinds else None
        self.jobs = queue.Queue()
        self.last_error = None
        self.stats = {"ai": 0, "fallback": 0}
        if client.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    @property
    def enabled(self):
        return self.client.enabled

    def wants(self, kind):
        return self.enabled and (self.kinds is None or kind in self.kinds)

    def submit(self, system, user, fallback, on_done, max_words=None):
        self.jobs.put((time.monotonic(), system, user, fallback, on_done, max_words or self.max_words))

    def _run(self):
        while True:
            queued_at, system, user, fallback, on_done, max_words = self.jobs.get()
            text = None
            if time.monotonic() - queued_at < self.max_wait:
                try:
                    text = clean_line(self.client.complete(system, user), max_words)
                    self.last_error = None
                except Exception as exc:
                    self.last_error = str(exc)
                    print(f"[ai] {exc}  (using the scripted line)")
            if text and time.monotonic() - queued_at <= self.max_wait:
                self.stats["ai"] += 1
                on_done(text, True)
            else:
                self.stats["fallback"] += 1
                on_done(fallback, False)


def sample_prompts(lines):
    """Example requests for the AI test command."""
    class Fake:
        won, lost, round_no, loss_streak = 5, 7, 12, 3
        issue_counts = {"long_spray": 4, "moving_shots": 2}

    issue = random.choice(list(ISSUE_TEXT))
    return [
        ("reset", build_request("reset", Fake, lines)),
        ("tip", build_request("tip", Fake, lines, issue=issue)),
        ("credit", build_request("credit", Fake, lines, extra="Highlight: CLUTCH.")),
        ("answer", build_request("answer", Fake, lines, question="how am I going")),
    ]
