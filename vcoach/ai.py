"""Optional AI wording for the coach, with Claude (Anthropic), ChatGPT (OpenAI) or the free
GLM 5.3 Flash model (Z.ai, through OpenRouter).

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
    # Free. Needs a free OpenRouter key. Limits: 20 requests a minute, 50 a day (1,000 a day
    # after a one-time purchase of $10 of OpenRouter credits).
    # GLM writes the match review only, never the spoken lines in a match ("review_only").
    "glm": {"model": "z-ai/glm-5.3-flash:free", "base_url": "https://openrouter.ai/api",
            "env": "OPENROUTER_API_KEY", "secret": "openrouter_api_key", "timeout": 8, "review_only": True},
    # Free and fast: fast enough for the spoken lines between rounds. Free key from console.groq.com.
    "groq": {"model": "openai/gpt-oss-120b", "base_url": "https://api.groq.com/openai",
             "env": "GROQ_API_KEY", "secret": "groq_api_key"},
    # Any service with the OpenAI chat format: Ollama on this PC, Google Gemini, Mistral, Cerebras...
    # custom_base_url is the address up to and with /v1, as the service's docs give it.
    "custom": {"model": "llama3.2", "base_url": "http://localhost:11434/v1", "path": "/chat/completions",
               "env": "CUSTOM_AI_API_KEY", "secret": "custom_api_key", "timeout": 8, "key_optional": True},
}

# Models that a service shut down, with the replacement that the service recommends.
# Your config.yaml may still name an old model, so the coach changes it here.
RETIRED = {
    "groq": {"llama-3.3-70b-versatile": "openai/gpt-oss-120b",     # Groq shut it down on 16 Aug 2026.
             "llama-3.1-8b-instant": "openai/gpt-oss-20b"},
}

_RETIRED_SAID = set()   # Say the note once for each process, not on each panel refresh.

NAMES = {"claude": "Claude", "chatgpt": "ChatGPT", "glm": "GLM (free)", "groq": "Groq (free)", "custom": "Custom AI"}
RATE_LIMIT_PAUSE_S = 600     # After "too many requests", use the scripted lines for 10 minutes.

ISSUE_TEXT = {
    "crosshair_low": "crosshair sits below head height when enemies appear",
    "crosshair_high": "crosshair sits above head height when enemies appear",
    "moving_shots": "shooting while still moving instead of stopping (counter-strafing) first",
    "long_spray": "spraying too long instead of short bursts",
    "bad_reload": "reloading during a fight instead of when safe",
    "no_counter_strafe": "stopping slowly (letting go of the key) instead of a counter-strafe tap on the opposite key",
}


class AIError(Exception):
    pass


def load_secrets(path="secrets.yaml"):
    if not os.path.exists(path):
        return {}
    from .util import load_yaml

    return load_yaml(path) or {}


USER_AGENT = "ValorantCoach/1.0"


def _post(url, headers, body, timeout):
    data = json.dumps(body).encode("utf-8")
    # Cloudflare (in front of Groq and others) blocks Python's default "Python-urllib" agent
    # with HTTP 403, error code 1010. A User-Agent of our own fixes that.
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **headers}
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
        d = DEFAULTS.get(self.provider)
        self.timeout = float(ai_cfg.get(f"{self.provider}_timeout_s") or ai_cfg.get("timeout_s", 4))
        if d and d.get("timeout"):
            self.timeout = max(self.timeout, float(ai_cfg.get(f"{self.provider}_timeout_s") or d["timeout"]))
        self.paused_until = 0.0
        self.enabled = False
        self.reason = "AI is off."
        if d is None:
            return
        self.model = ai_cfg.get(f"{self.provider}_model") or d["model"]
        new = RETIRED.get(self.provider, {}).get(self.model)
        if new and self.model not in _RETIRED_SAID:
            _RETIRED_SAID.add(self.model)
            print(f"[ai] {self.model} was shut down by the service. The coach uses {new}. "
                  f"Change the model in Settings > AI coach to stop this message.")
        if new:
            self.model = new
        self.base_url = (ai_cfg.get(f"{self.provider}_base_url") or d["base_url"]).rstrip("/")
        self.key = (secrets.get(d["secret"]) or os.environ.get(d["env"]) or "").strip()
        self.path = d.get("path", "/v1/chat/completions")
        # "reasoning_effort" only where the model takes it. Other services may refuse an unknown setting.
        self.use_reasoning_param = self.provider in ("chatgpt", "glm") or \
            (self.provider == "groq" and "gpt-oss" in self.model)
        if not self.key and not d.get("key_optional"):
            self.reason = f"No API key. Run the AI setup, or set {d['env']}."
            return
        self.enabled = True
        self.reason = ""

    def complete(self, system, user, timeout=None, max_tokens=None):
        if time.monotonic() < self.paused_until:
            raise AIError("Rate limit: the coach uses scripted lines for a few minutes.")
        try:
            return self._complete(system, user, timeout, max_tokens)
        except AIError as exc:
            if "HTTP 429" in str(exc):
                self.paused_until = time.monotonic() + RATE_LIMIT_PAUSE_S
                print("[ai] The AI said \"too many requests\". Scripted lines for the next 10 minutes."
                      + (" Free GLM allows 50 requests a day." if self.provider == "glm" else ""))
            raise

    def _complete(self, system, user, timeout=None, max_tokens=None):
        timeout = timeout or self.timeout
        if self.provider == "claude":
            body = {"model": self.model, "max_tokens": max_tokens or 150, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
            data = _post(self.base_url + "/v1/messages", headers, body, timeout)
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        if self.provider == "glm":
            headers["X-Title"] = "Valorant Coach"      # Shows the app name in your OpenRouter activity.
        body = {"model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        # Ask reasoning models to answer at once. No token limit here: with a limit, some
        # reasoning models spend it all on hidden reasoning and return an empty reply.
        if self.use_reasoning_param:
            # GPT-OSS on Groq has no "none": "low" is its fastest setting.
            body["reasoning_effort"] = "low" if "gpt-oss" in self.model else "none"
        try:
            data = _post(self.base_url + self.path, headers, body, timeout)
        except AIError as exc:
            if "reasoning" in str(exc).lower() and self.use_reasoning_param:
                self.use_reasoning_param = False  # This model does not take the setting.
                body.pop("reasoning_effort", None)
                data = _post(self.base_url + self.path, headers, body, timeout)
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


def system_prompt(lines, max_words, coach="Bruce"):
    # Style examples come only from mindset and praise lines. Mistake tips here ("your crosshair is
    # low") made the AI name mistakes that the facts did not show (17 Nov).
    examples = []
    for key in ("reset", "credit"):
        examples += lines.get(key, [])[:4]
    examples = "\n".join(f"- {e}" for e in examples[:8])
    return f"""You are {coach}, a voice coach for a Valorant player. The player says "{coach}" to talk to you.
Personality: {lines.get('name', 'Coach')}. Style: {lines.get('description', '')}
You speak through the player's headset between rounds. Your words are read aloud by text-to-speech.

There are two kinds of task. The task line says which one it is.
- Coaching (all tasks but chat): talk only about habits and mindset: crosshair height, stopping before shooting,
  burst control, reload timing, composure.
- Chat (the task says "It may not be about Valorant"): talk about anything the player brings up, as a friend would.
In both kinds, never give tactical calls: no enemy positions, sites, rotations, utility plans, economy or buy advice.

Rules:
- Reply with short sentences, {max_words} words or fewer in total.
- Plain spoken English only: no emoji, no lists, no markdown, no quotation marks, no stage directions.
- Use only the facts you are given. Never invent numbers.
- Give a number only when the facts give it, with its exact value. Never add numbers together.
- Name a mistake only when the facts list it. When the facts list no mistakes, do not say that the player made one
  (for example, never say "your crosshair is low" without a crosshair fact). Then praise, or talk about mindset.
{"- 18+ personality: swearing, crude jokes and innuendo are fine. Nothing sexually explicit and no slurs. Jokes are about plays, never about who the player is." if lines.get("adult") else "- Jokes and teasing are about plays only, never about the player as a person. No swearing or slurs."}
- Do not repeat any of the recent lines.
- If the player asks for tactics (positions, sites, rotations, utility, economy), say in character that you only coach
  habits and mindset.
- In a chat, do not turn the talk into Valorant advice unless the player asks about the game.

Lines in your style (for the style only: they are not facts about this player):
{examples}"""


def build_request(kind, coach, lines, stats=None, issue=None, question=None, extra=None, recent=None,
                  talk=None):
    """Return the user prompt with the facts for one message."""
    facts = [f"Score: won {coach.won}, lost {coach.lost}. Round number: {coach.round_no}."]
    if coach.loss_streak:
        facts.append(f"Current losing streak: {coach.loss_streak}.")
    if stats is not None:
        # The last round comes first: the line is about that round (17 Nov: the coach kept repeating
        # the match totals after rounds with no mistakes).
        s = stats.summary()
        moving = s["moving_bursts"]
        facts.append(f"LAST ROUND: {s['bursts']} bursts; {moving} of them while moving"
                     + (" (none: the player stopped before shooting)" if s["bursts"] and not moving else "")
                     + f"; {s['long_sprays']} long sprays; {s['bad_reloads']} reloads in a fight."
                     + (f" {s['counter_stops']} of {s['stops']} stops used a counter-strafe." if s.get("stops") else ""))
    if kind in ("reset", "credit", "chat"):
        pass                          # Mindset, praise and chat: no match totals, so no old mistakes.
    elif coach.issue_counts:
        common = sorted(coach.issue_counts.items(), key=lambda kv: -kv[1])
        facts.append("Whole match so far (number of rounds with this mistake): " +
                     "; ".join(f"{ISSUE_TEXT.get(k, k)}: {v}" for k, v in common) + ".")
    elif kind != "tip":
        facts.append("Mistakes this match: none measured.")
    if kind == "credit" and not extra:
        facts.append("Highlight: none (not a clutch, not an ace).")
    if extra:
        facts.append(extra)
    if recent:
        facts.append("Your recent lines. Do not repeat their words or their advice: " + " | ".join(recent[-4:]))
    if talk:
        # The last few questions and answers, so a follow-up such as "what about my last round?" makes sense.
        facts.append("Your talk with the player so far (older first): " +
                     " | ".join(f"Player: {q} / You: {a}" for q, a in talk))

    task = {
        "tip": f"Give one short tip to fix this habit: {ISSUE_TEXT.get(issue, issue)}.",
        "focus": f"The player lost the last two rounds. Tell them to focus on one thing this round: {ISSUE_TEXT.get(issue, issue)}.",
        "reset": "The player is on a losing streak or lost a lead. Help them reset their mindset for the next round. "
                 "Mindset only: no mechanics, no mistakes and no numbers.",
        "credit": "The player just won a round. Give short praise. Name a highlight only if the facts give one.",
        "match_end": "The match is over. Give one genuine positive from the facts, and a short sign-off.",
        "debrief": "The match is over. Give a short spoken debrief in three or four sentences: the result and score, "
                   "one genuine positive from the facts, one habit to work on next match (only a mistake that the "
                   "facts list), and a sign-off.",
        "agent_select": "The player is in agent select before a match. Say one short line to get them ready.",
        "agent": "The player selected an agent in agent select. Say one short line about playing that agent's role "
                 "with good habits. Use the agent name.",
        "answer": f"The player asked you: \"{question}\". Answer from the facts.",
        "chat": f"The player said: \"{question}\". It may not be about Valorant. Reply in character as a friend "
                f"would, in one or two short sentences. Use the facts only if the player asks about the game.",
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
        self.kinds = set(kinds) if kinds is not None else None
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
