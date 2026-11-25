# Valorant Screen Coach

This program watches your screen while you play Valorant. At the end of each round, it speaks one short message. The message is a mechanical tip, a mental reset, or credit for a good round. At the end of the match, it tells you one good thing and saves a report.

The program only captures the screen. It does not read game memory, inject into the game, or draw an overlay. It stays silent during rounds.

> **Use at your own risk.** This is a free hobby project. Riot Games did not make it and has not approved it. Read "Rules" before you use it, and before you share it.

## What it checks

| Check | How it works |
|---|---|
| Shooting while moving | Reads Valorant's **Shooting Error** stat for the first shot of each burst. Above 1 degree means you moved when you fired. Turn the stat on in Settings > Video > Stats (Text Only). If the stat is not on the screen after 60 seconds with your gun out, the coach asks you once to turn it on. Without the stat, the coach uses your movement keys (W, A, S, D). Without the keys (for example in a replay of a video), it guesses from the picture with optical flow, which is less exact. |
| Counter-strafing | Uses your movement keys and your fire button. When you stop a strafe before you shoot, the coach checks how you stopped: a short tap on the opposite key (A then D) is a counter-strafe. Letting go of the key and waiting is a slow stop. The report shows how many of your stops used a counter-strafe. The coach gives a tip when more than 60% of your stops in a round are slow. |
| Crosshair height | **Test version, off by default.** When an enemy outline first shows near the crosshair with your gun out, it compares the top of the outline (the head) with the crosshair. A colour and shape filter rejects fire, lamps, tracers, map lines and smoke. In tests on three real games with yellow outlines, the filter kept about 85% of real enemies and rejected about 95% of false outlines, and the head height was right in about 8 of 10 fights. Turn it on with `checks.crosshair.enabled: true`. Yellow outlines give the best results. |
| Spraying too long | Reads the ammo counter and counts the shots in each burst. The full magazine size tells the weapon (for example 12 Classic, 25 Vandal, 30 Phantom, 100 Odin), and each weapon has its own limit in `checks.long_spray.per_magazine`. Ares and Odin have no spray tips, because they are made to spray. |
| Reloading at bad times | Flags a reload when you had ammo left and took damage in the last 2.5 seconds. |
| Death review | From the H3ners video "This is Why You Keep Dying". For each death, the coach looks at the last 5 seconds and asks "Did I have an advantage?" It sees your own flash or recon ability key (the table is in `vcoach/agent_abilities.yaml`), revealed enemies (Fade's magenta figures and the pink edges of Sova's Recon Bolt), and a flat blinded screen ("You were blinded"). It cannot see an off angle or a teammate next to you, so between rounds it asks you about them. The result is in the death line of the log and in the "Before you died" column of the report. The white mark over a blinded enemy is a test (`checks.advantage.use_blind_marks`), off by default. Turn the whole review off with `checks.advantage.enabled: false`. |
| Mental coach | Gives a reset after a loss streak or a lost lead. It gives credit for clutches, aces, and wins after a streak. It gives a "one focus" message after two lost rounds. |
| Voice commands | Say "Bruce" and ask a question or give a command. See "Talk to the coach". |
| AI coach (optional) | Claude, ChatGPT, Groq (free) or a Custom AI writes the coach lines and a review after each match. The free GLM model writes the review only. See "AI coach". |

The coach speaks a maximum of one message per round. After losses, a mental message takes priority over a tip. When nothing needs attention, it stays quiet. It does not repeat a line in the same match until it has used every line in that list.

For the short version, see QUICKSTART.md.

## Control Panel

The Control Panel is a local website for all setup and use. Double-click **Valorant Coach Setup** (or `Coach Setup.bat`, or run `python main.py panel`). It opens in your web browser at `http://127.0.0.1:8765`. Only your own PC can connect to it. Keep the small black window open while you use it.

| Page | What you do there |
|---|---|
| Home | See what is ready. Install missing parts with one click. |
| Screen regions | Take a screenshot and drag the boxes. Set the enemy colour and gun side. |
| Personality | Hear each coach and choose one. |
| Voice & mic | Choose the microphone. Test the wake word. |
| AI coach | Turn on Claude, ChatGPT or GLM (free), save your key, and test the lines. |
| Settings | Choose the Windows voice and speed, and get more voices. Change the checks, voice commands, AI timing, and screen capture. |
| Reports | Read the report for each match. |
| Help | Read the quick start guide. |

**Start coach** and **Stop coach** at the top run the coach from the panel. **Coach log** shows what you said, what the coach said, and your marks and replays. Tick **Debug** in it to see every line. **Stop coach** stops it cleanly, so it saves the match report.

The coach writes 2 logs in the `logs` folder:
- `coach_log.txt`: your view, with the time of day. What you said, what the coach said, marks, replays and slow motion replays, and the round results.
- `debug_log.txt`: every line, for finding problems (shots, weapon changes, timing, errors). Send this one when you ask for help.

## Settings files

| File | Purpose |
|---|---|
| `config.yaml` | Your settings. The coach creates it on the first start. Updates do not replace it. |
| `config.default.yaml` | The default settings. Updates replace it. Do not edit it. |
| `secrets.yaml` | Your AI keys. Updates do not replace it. |

To update, unzip the new version over the coach folder. After an update, the coach adds new settings to `config.yaml` and keeps your values.

## Launchers

| File | What it does |
|---|---|
| `install.bat` | Installs the Python packages and the voice model. It offers Tesseract only if the Windows text reader does not work. It can also make the desktop icons, then opens the Control Panel. |
| `Coach Setup.bat` | Opens the Control Panel. Desktop icon: gear. |
| `Start Coach.bat` | Starts the coach without the panel (`main.py live`). Desktop icon: crosshair. |
| `Classic Setup Menu.bat` | The older text menu with the same tools. Use it if the panel does not work. |
| `editor.html` | The visual region editor. Open it from the Setup menu, so Save can write to `config.yaml`. |
| `create_shortcuts.bat` | Creates the two icon shortcuts in the coach folder and on the desktop. Run it again if you move the folder. |
| `Make Share Zip.bat` | Makes a zip that is safe to give to a friend. Your keys, settings, recordings and reports stay out. It is not in the share zip. |
| (drop a video) | Drag a game video onto the Valorant Coach shortcut or `Start Coach.bat`. It cuts the video into parts of about 250 MB in a `parts` folder next to the video, for replay tests. The coach does not start. |

A `.bat` file cannot have its own icon in Windows. The crosshair and gear icons show only on the shortcuts.

## Setup (Windows)

1. Install Python 3.15 (or 3.14) from python.org/downloads. Use the 64-bit Windows installer. On the first installer screen, tick "Add python.exe to PATH". The coach is tested on Python 3.14 and 3.15.
2. Words (BUY PHASE, WON, VICTORY, agent names) use the text reader that is built into Windows 10 and 11. It needs no extra program. To check it, click **Test** next to **Word reader** on the Home page. The test compares the readers on your recordings and saves the result in `logs\text_test.txt`.
3. Tesseract OCR is now only a backup. install.bat offers it only when the Windows text reader does not work. To force a reader, set `text_reader` in `config.yaml` to `windows` or `tesseract` (the default is `auto`). If Tesseract is not on PATH, set `tesseract_cmd` to the full path of `tesseract.exe`.
4. Open a terminal in this folder.
5. Double-click `install.bat`, or run `pip install -r requirements.txt`.
6. In Valorant, set Display Mode to **Windowed Fullscreen**. Exclusive fullscreen can give black screenshots.
7. In Valorant, check Settings > General > Enemy Highlight Color. Set the same color in `config.yaml` under `enemy_highlight.color`.

## Step 1: Calibrate the HUD regions

The default regions are estimates. You must adjust them for your screen and HUD scale.

### Region editor (recommended)

Run `python main.py editor`, or press **1** in the Setup menu. The editor opens in your web browser.

1. Click **Take screenshot** and switch to Valorant. You can also click **Load picture** to use a screenshot file.
2. Drag each box over its HUD item. Drag a handle to resize it. Use the arrow keys for fine moves (Shift for 10 pixels, Alt to resize). Drag the dashed **Gun area** box over your own gun: enemy colours inside it are ignored.
3. Set the **Enemy highlight colour** and **Gun side** dropdowns to match Valorant (Settings > General). Changing Gun side moves the Gun area box to that side.
4. Click **Test reading**. It runs the same text reading and enemy detection as the coach, on the screenshot.
5. Click **Save to config**. The editor changes only the region numbers in `config.yaml` and keeps all comments.
6. Click **Done** to stop the editor.

The editor runs a small helper on your PC at `http://127.0.0.1:8765`. Only your own PC can connect to it. If you open `editor.html` straight from the folder, the editor still works, but **Save** copies the settings for you to paste into `config.yaml`, and **Take screenshot** and **Test reading** are off.

### Manual calibration

1. Start a Deathmatch or a custom game.
2. Run `python main.py calibrate`.
3. Switch to Valorant within 5 seconds.
4. Open `calibration.png`. Green boxes show the regions. Magenta pixels show what the program sees as enemy highlight.
5. Read the terminal output. It shows the ammo, health, and banner values that OCR read.
6. If a box does not cover its HUD item, change the numbers under `regions` in `config.yaml`.
7. Run the calibration again until the ammo and health values are correct.

The ammo box must cover the magazine number only, not the reserve ammo.

To calibrate from a recording, run `python main.py calibrate --video clip.mp4 --at 42`. The `--at` value is the time in seconds.

## Step 2: Tune with recordings

1. Record a few rounds with OBS at the same resolution you play at.
2. Run `python main.py replay clip.mp4 --log metrics.csv`.
3. Open `metrics.csv` in Excel. Each frame row shows the `motion` score, `enemy_pixels`, and `head_offset`. Each HUD row shows `ammo`, `health`, and `banner`.
4. Find moments where you stood still and moments where you strafed. Compare their motion scores.
5. Set `checks.moving_shots.motion_threshold` between the two values.
6. Run the replay again and check the round summaries in the terminal.

Use the same clips each time, so you can compare results after you change a setting.

## Step 3: Play

1. Run `python main.py live`.
2. Start Valorant.
3. Press **F8** to mute or unmute the coach.
4. Press **Ctrl+C** in the terminal to stop.

Reports go to the `reports` folder. Each report has one row per round with the stats and what the coach said.

Use `--silent` to print messages instead of speaking them. Use `--personality random` to get a new coach each match. Use `--log metrics.csv` to save metrics during live play.

## Coach personalities

| Personality | Style |
|---|---|
| `calm` | Steady and quiet. Short, clear advice. This is the default. |
| `hype` | High energy. Celebrates big plays and pumps you up after losses. |
| `tough` | Strict and direct. Firm, but never insulting. |
| `mate` | Laid-back Aussie mate. Relaxed and friendly. |
| `sassy` | Cheeky and playful. Roasts your plays, then tells you how to fix them. The roasts get gentler after a loss streak. |
| `savage` | Funny and mean. Short roasts made for clips. It still tells you the fix. No swearing. |
| `flirty` | Playful and charming. Cheeky compliments about your plays, then the fix. Safe for streams. |
| `degen` | **18+ only, not for streams.** Swears, roasts your plays hard and makes dirty jokes, then gives the fix. No explicit sexual content and no slurs. It shows only after you turn on **18+ personality (Degen)** in Settings > Coaching. Random, Mix and Twitch chat votes never pick it. |
| `random` | Picks a different personality at the start of each match and says which one. |
| `mix` | Picks a different personality each round. It does not say which one. |

To choose a personality, do one of these:

- Set `coach.personality` in `config.yaml`.
- Add `--personality` to a command, for example `python main.py live --personality random`.

To limit the random choice, list personalities in `coach.random_pool`, for example `[hype, mate]`. If you do not want roasts on a bad day, leave `sassy` out of the list. An empty list means all personalities.

To list the personalities, run `python main.py personalities`.

### Make your own personality

1. Copy a file in the `personalities` folder, for example `mate.yaml` to `myname.yaml`.
2. Change `name`, `description`, and the lines.
3. Run `python main.py preview --personality myname` to hear it.

You can leave out any list. The coach then uses the list from `calm.yaml`. Keep each line short, so it finishes before the buy phase ends. A list with 20 or more lines sounds less repetitive. Keep `{focus}`, `{good}`, `{total}`, `{count}`, `{won}`, `{lost}`, and `{name}` in the lines that use them. The `answers` list holds the replies to voice commands.

## How the coach speaks

The coach uses the text-to-speech voices that come with Windows (through `pyttsx3`). It works offline and needs no API key.

1. Run `python main.py voices` to list the voices on your PC.
2. Run `python main.py preview --personality hype` to hear five sample lines.
3. To use a different voice, set `voice.name_contains` in the personality file to part of the voice name, for example `"Zira"` or `"David"`.
4. To use one voice for all personalities, set `coach.voice_name` in `config.yaml`.
5. To change the speed, set `voice.rate` in the personality file, or `coach.voice_rate` in `config.yaml`.

To add more voices, for example an Australian English voice, open Windows Settings > Time & language > Speech > Add voices. Some newer "natural" Windows voices do not show in `pyttsx3`. If a new voice does not show in `python main.py voices`, it is not available to the coach.

If you hear nothing, check that Windows sound output is on the device you use for the game, and run the preview without `--silent`.

## Talk to the coach ("Bruce")

Say **"Bruce"**, then a command. You can say both in one sentence ("Bruce, how am I doing?"). You can also say "Bruce" first, wait for the answer ("Yes?"), and then say the command within 5 seconds.

Speech recognition uses Vosk. It runs offline on your PC and sends no audio anywhere.

### Set up voice control

1. Run install.bat. It installs `vosk` and `sounddevice`, and downloads the Vosk model `vosk-model-small-en-us-0.15` (about 40 MB) from alphacephei.com into the `models` folder.
2. If the download fails, the coach tries again the first time voice control starts. You can also press **V** in the setup menu, or run `python main.py install-voice-model`.
3. Run `python main.py mics` to list your microphones.
4. If the coach must use a mic that is not the Windows default, set `voice_control.mic_device` to its number.
5. Run `python main.py listen` and say "Bruce, how am I doing?" The terminal shows what the recognizer heard.

If the recognizer hears "Bruce" as another word, add that word to `voice_control.wake_aliases`. If recognition is poor, try the larger model `vosk-model-en-us-0.22-lgraph` (128 MB): change `model_path` to `models/vosk-model-en-us-0.22-lgraph`, then press **V** in the setup menu to download it.

### Commands

| Say | What happens | When |
|---|---|---|
| "Bruce" | The coach answers "Yes?" and waits for a command. | At once |
| "Bruce, mute" / "be quiet" | The coach goes silent. | At once |
| "Bruce, unmute" / "talk again" | The coach speaks again. | At once |
| "Bruce, switch to sassy" | Changes the personality (calm, hype, tough, mate, sassy, savage, random, mix). | At once |
| "Bruce, say that again" | Repeats the last message. | At once |
| "Bruce, help" | Lists the commands. | At once |
| "Bruce, how am I doing?" / "how am I going?" | Score and the main thing to work on. | Between rounds |
| "Bruce, what should I work on?" | Your most common mistake this match. | Between rounds |
| "Bruce, what's the score?" | Rounds won and lost. | Between rounds |

### Chat mode ("Hey Bruce")

Say **"Hey Bruce"** to chat, and **"Bruce"** to get coaching.

| You say | What happens |
|---|---|
| "Hey Bruce, what music are you into?" | A chat about anything: life, music, food, a joke. The coach answers in character, in one or two short sentences. |
| "Bruce, how am I doing?" | Coaching: your numbers and your habits. |

- **Follow-up questions.** After an AI answer, the coach listens for 8 seconds. Ask your next question with no "Bruce". A follow-up stays in the same mode (chat or coaching). Short words such as "ok" or "thanks" and the coach's own voice from your speakers are ignored.
- **Memory.** The AI remembers your last 3 questions and its answers in this match, so "what about my last round?" makes sense. A new match starts a new talk.
- **When.** Chats and coaching answers play between rounds and in the buy phase only. A question in a live round waits for the round to end. Mute, unmute, help, mark that and replay that still work in chat mode.
- **No tactics.** In both modes the coach gives no live tactics (positions, sites, utility, economy). That keeps it inside Riot's rules.
- **Needs an AI** that writes spoken lines (Groq, Claude, ChatGPT or Custom). GLM writes only the match review.
- **Settings:** `voice_control.follow_up_s` (0 turns follow-ups off), `ai.conversation_memory` and `ai.small_talk` (false turns chat mode off).

### Creator commands (for short videos)

| Say | What happens | When |
|---|---|---|
| "Bruce, mark that" / "clip that" | Saves the moment (clock time, round, score) in the match report under "Clips to post", and says "Marked." If you set `creator.clip_hotkey`, it also presses your recording app's clip key. | At once |
| "Bruce, replay that" | An instant replay on your stream through OBS. See "Instant replay on stream". | At once |
| "Bruce, slow mo" | The last half of the replay at half speed. Also: "slow motion", "slow that down", "replay that in slow motion". | At once |
| "Bruce, start the challenge" | The Range challenge. See "Range challenge". "Bruce, stop the challenge" ends it. | In the Range |
| "Bruce, say hi to chat" | A short intro line for your stream or video. | At once |
| "Bruce, sign off" | A short outro line. | At once |
| "Bruce, rate that round" | A score out of 10 for the last round, with a funny reason. | Between rounds |
| "Bruce, roast me" | A Savage roast about your most common mistake, whatever personality is on. | Between rounds |
| "Bruce, hype me up" | A Hype line. | Between rounds |
| "Bruce, recap" | The score, your deaths, and the habit to fix. | Between rounds |

### Auto-clip highlights

When your team wins a round with an ACE, TEAM ACE or CLUTCH banner, the coach saves a clip by itself. You do not have to say anything. The banners also show for the enemy team and for your teammates, so the coach clips only rounds that your team won, and an ACE or CLUTCH only when you did not die in that round. A teammate's ace in a round where you lived can still be clipped, because the coach does not know your in-game name. The clip comes at the round end, a few seconds after the banner: set your recorder to keep at least the last 20 seconds. It adds a line to "Clips to post" in the match report and presses your clip hotkey. If OBS instant replay is on, it also saves the OBS replay file (`creator.auto_clip_obs: save`), or plays it on stream (`replay`). To add FLAWLESS or THRIFTY, change `creator.auto_clip_moments`. To turn it off, clear **Auto-clip highlights** on the Settings page.

### Shorts pack

Each match report ends with a **Clips to post** table: the clock time, round, score and moment of each clip, with a starter caption and hashtags. The clock time helps you find the moment in your recording. If an AI is on, the AI match review also writes a hook, a caption and hashtags for each clip, from what happened in that round. This uses no extra AI request.

### Range challenge

Say "Bruce, start the challenge" in the Range. Then strafe, stop, and shoot 20 times. The coach counts each first shot after a strafe:

- **Clean:** you stopped with a tap on the opposite key (or, without the key reader, the Shooting Error stat was 1 degree or less).
- **Slow:** you let go of the key and waited.
- **Moving:** you were still moving when you shot.

Shots that you fire while you stand still do not count. At the end, the coach says your score and compares it with your best and your last challenge. It also saves a result card for a Short (a 1080 x 1920 picture) in `reports\challenges`, and a line in `reports\challenges.jsonl`. Change the number of stops on the Settings page. The challenge works only outside a match, and a match that starts ends the challenge.

### Twitch chat

The coach can read your Twitch chat. Turn it on in the Control Panel: **Settings > Twitch chat**, and type your channel name. Reading chat needs no account.

| Chat command | What happens |
|---|---|
| `!vote savage` | One vote for a personality. Each viewer has one vote in each round. A new vote replaces the old one. At each round end, the personality with the most votes takes over, and the coach says "Chat picked Savage." |
| `!coach` | The coach answers in chat with your session numbers: score, deaths, moving first shots, counter-strafes and the current coach. One answer each 30 seconds. |

To answer `!coach` in chat, the coach needs a chat account (it can be your own) and an OAuth token for it with the `chat:read` and `chat:edit` scopes. Put the account name and the token in the Twitch chat card. The token goes into `secrets.yaml`. Keep it private, as you would a password. Without a token, the coach reads chat only: votes still work.

Set your name or channel name on the **Settings** page of the Control Panel (Content creator card), so the intro and outro use it. The same card has the recorder clip hotkey. The Movement keys card sets the keys for the counter-strafe check. The lines are in `vcoach/creator_lines.yaml`, and you can change them.

`creator.clip_hotkey` is empty by default. Examples: `alt+f10` (NVIDIA instant replay) or `win+alt+g` (Xbox Game Bar: record the last 30 seconds). The coach then presses keys on your PC. It only sends that one key combination, and only when you ask for it. The anti-cheat sees it as normal key input, but no one can promise how Riot treats it, so use it at your own risk.

**The coach never talks during a live round.** All speech, AI lines too, stops 5 seconds after the buy phase ends, and starts again when the round ends. A line that is still playing at that moment stops in the middle. A line that arrives late (for example a slow AI answer) is not spoken. The coach reads the round timer at the top of the screen to find the round start. In the buy phase, the timer shows how many seconds are left, so the coach knows the start time before it comes. If the coach cannot read the timer, it uses the "BUY PHASE" banner. With no timer and no banner, it stops talking 25 seconds after the score changes. Change these times with `voice_control.stop_after_round_start_s` and `voice_control.silent_after_round_end_s`.

Coaching questions get an answer only between rounds. If you ask during a round, the coach answers when the round ends. Mute, unmute, personality and "mark that" work at any time, but during a live round they work silently.

### Tips

- Use a headset. Speakers can let the coach hear its own voice or your teammates.
- Valorant voice chat and the coach can use the same mic at the same time.
- Your teammates hear you say "Bruce" if your voice chat is open. Use the wake word when push-to-talk is off.
- To turn voice control off, set `voice_control.enabled` to `false`, or run `python main.py live --no-mic`.

## AI coach (Claude, ChatGPT, Groq, GLM or Custom)

The coach can use an AI to write its lines, so they sound less scripted. The coach rules still decide when to speak and what about (a tip, a reset, praise, or an answer). The AI only writes the words, in the personality's style.

### What the AI gets

The AI gets short text facts only: the score, the losing streak, the mistake counts, the last few coach lines, and the questions you say after "Bruce". It never gets screenshots or audio.

### Set it up

1. Get an API key:
   - **Claude:** console.anthropic.com > API keys. Add credit on the Billing page.
   - **ChatGPT:** platform.openai.com > API keys. Add credit on the Billing page.
   - **Groq (free):** make a free account at console.groq.com, then go to console.groq.com/keys. Groq is fast enough for the spoken lines between rounds. The free limits differ for each model.
   - **Custom AI:** any service that takes the OpenAI chat format. Give the address up to and with `/v1`, the model name and a key. For Ollama on your own PC, use `http://localhost:11434/v1` and no key. Ollama uses your graphics card, so it suits the match review better than live lines. Other examples: Google Gemini (`https://generativelanguage.googleapis.com/v1beta/openai`) and Mistral (`https://api.mistral.ai/v1`).
   - **GLM 5.3 Flash (free):** make a free account at openrouter.ai, then go to openrouter.ai/keys. No credit is needed. The free model allows 20 requests a minute and 50 a day. After a one-time purchase of $10 of OpenRouter credit, the limit is 1,000 a day.
2. Open **Valorant Coach Setup** and press **A** (AI coach setup). You can also run `python main.py ai-setup`.
3. Choose Claude, ChatGPT, GLM, Groq or Custom AI. You can also do this on the **AI coach** page of the Control Panel.
4. Paste your key and press Enter. The key does not show on screen. In the black window, right-click to paste.
5. The setup sends two test requests and shows the AI lines and how long they took.

To hear what each personality sounds like with the AI, press **T** in the Setup menu, or run `python main.py ai-test --personality sassy`.

### How it behaves

- The coach waits up to `ai.timeout_s` seconds (default 4). If the AI is slower, offline, or fails, the coach speaks the scripted line. The coach never goes silent because of the AI.
- AI lines are one or two short sentences (`ai.max_words`, default 25).
- The AI is told to coach habits and mindset only, never tactical calls. It still answers only between rounds.
- With the AI on, "Bruce" understands free questions, for example "Bruce, why do I keep losing gunfights?"
- To keep scripted lines for some messages, remove items from `ai.use_for`: `tips`, `mental`, `answers`, `match_end`.
- To turn the AI off, run the AI setup and choose 4, or set `ai.provider: off`.
- The free GLM model writes the match review only. During a match, the coach uses its scripted lines. One review uses one of the 50 free requests a day.

### Match review

After each match, the AI writes a review at the end of the match report: a summary, what went well, the two habits to work on with a drill for each, and a comparison with your recent matches. It uses one AI request for each match. The coach waits 60 seconds first (`ai.analysis_delay_s`), so the review can use the scoreboard numbers. To write a review for an older report, open it on the **Reports** page and click **Write an AI review**, or run `python main.py analyze`. To choose, use the **Use the AI for** boxes on the **AI coach** page of the Control Panel: four kinds of spoken lines and **Written match review**. With GLM, only **Written match review** can be chosen.

### Models and cost

The defaults are `claude-haiku-5-5`, `gpt-5.4-mini` and `z-ai/glm-5.3-flash:free`. If a model name stops working, change `claude_model`, `chatgpt_model` or `glm_model` under `ai:` in `config.yaml`. Free models on OpenRouter change often. Any model ID that ends in `:free` works in `glm_model`. Each line is a small request. Check the pricing page of your AI provider for current prices.

### Keep your key safe

Your key is in `secrets.yaml` in the coach folder. Do not share that file. If you share the coach folder with a friend, delete `secrets.yaml` from the copy first. You can also use the `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY` or `CUSTOM_AI_API_KEY` environment variable instead of the file.

## How the coach follows a match

| Moment | What the coach reads | What it does |
|---|---|---|
| Agent select | The mode name at the top left, and the role and agent name on the right | Says one line when agent select starts, and one line about your agent after you keep it selected for 8 seconds. |
| Round end | The round score at the top of the screen (yours on the left) | Counts the round when two reads in a row show the new score. Then it gives a tip, a reset, or praise. |
| Banner | BUY PHASE, MATCH POINT, CLUTCH, ACE, TEAM ACE, FLAWLESS, THRIFTY | Uses the buy phase to answer questions. Uses the highlight words for praise. |
| Match end | VICTORY or DEFEAT on the results page | Gives a spoken debrief (result, one good thing, one thing to work on) and saves the report. |

The score is on screen for the whole match, also when you are dead, so it is the main signal. If the coach starts in the middle of a match, it takes the score from the screen. The score, mode and agent areas are fixed for 16:9 screens. For another screen shape, set `score_left`, `score_right`, `top_left` and `agent_panel` under `regions` in `config.yaml`.

If the coach does not detect rounds, record a match (see the next section) and read `summary.txt`. It lists every score change the coach saw.

## Record a match for troubleshooting

In the Control Panel, open Settings > Troubleshooting and turn on **Record the next match**. Then click **Start coach**. (You can also run `python main.py live --record`.) The recording is for one coach session. It stops when you click **Stop coach**, or after 45 minutes.

The coach saves a folder `recordings/match_<date>_<time>` with these items:

| Item | Contents |
|---|---|
| `summary.txt` | How fast the coach read the screen, how often each reading worked, every score change, and every text seen. Read this first. |
| `readings.csv` | One line for each reading: time, ammo, health, score, banner text, and menu text. |
| `banner` folder | A picture of the top of the screen (score and banner) at each score read. |
| `hud` folder | A picture of the Health and Ammo boxes at each score read. |
| `full` folder | The full screen every 2 seconds. |

A Swiftplay match uses about 100 to 200 MB. Delete old recordings when you do not need them. The pictures show your screen, including player names and chat, so check them before you share them.

## CPU use

The coach does the slow picture work only when it needs it:

- **Moving shots.** The picture motion check (optical flow) is slow, so the coach does not run it on each frame. It keeps small copies of the last 2 seconds and measures only the moment before a shot, and only when the Shooting Error stat and the movement keys cannot judge that shot. With the key reader on, it keeps no copies at all.
- **Enemy search.** The coach looks for enemy outlines only when the crosshair check is on.
- **Capture rate.** With the key reader on and the crosshair check off, the coach captures 20 frames a second (`low_fps`), not 30.

In a test on a recorded match, the work for each frame went from about 14 ms to about 2 ms, and to almost nothing with the key reader on.

## Known limits

- **Rounds and matches** come from OCR of the banner text. If the coach misses rounds, check the banner region and the words under `banner_keywords`.
- **Screen and language.** The coach is tested at 1920 x 1080 (16:9) only. Other 16:9 sizes should work. 16:10, 4:3 and stretched screens need new boxes on the Screen regions page. Deaths ("SWITCH PLAYER") and the Shooting Error stat are found by their English text. In another game language, the coach misses deaths more often and uses the optical-flow guess for moving shots.
- **Death detection** looks for "SWITCH PLAYER" (you spectate a teammate). A second check counts a death when you took damage and then your ammo and health went off the screen.
- **Moving shots without the Shooting Error stat** use your movement keys. With no key data (replays of videos), the coach uses optical flow. In tests, optical flow matched the stat for about 76% of bursts.
- **Movement keys** are W, A, S and D, and the fire button is the left mouse button. If you use other keys, change the `keys` section in `config.yaml`. To turn the key reader off, set `keys.enabled: false`.
- **Weapon changes.** The key reader also notes R (reload), the weapon keys 1 to 4, the ability keys Q, E, C and X, and the mouse wheel. An ammo change with no fire click, or right after one of these keys, is a weapon change (for example to Chamber's Headhunter), not shots. An ammo rise with no R key and ammo left is a weapon change, not a reload. Change the keys with `keys.reload` and `keys.switch`.
- **Weapon swaps** can look like a reload. The reload check only counts a reload when you also took damage, which removes most false alarms.
- **Spray length** uses the limit for the weapon, from its magazine size. Change the limits in `checks.long_spray.per_magazine`.
- **Crosshair height** is a test version and off by default. The filter is tuned for yellow outlines only. Abilities, very close enemies and enemies against a bright sky still give wrong head heights in about 2 of 10 fights.
- **Riot patches** can move or change the HUD. If the checks stop working after a patch, calibrate again.

## Rules

Riot's developer policy rejects in-game apps that give real-time information to improve play immediately ("go here now"). It accepts coaching that helps players learn over time. See the VALORANT section of the Riot Developer Portal: https://developer.riotgames.com/docs/valorant

This program tries to stay on the coaching side:

- It speaks only in agent select, between rounds, and after the match. All speech stops 5 seconds after the buy phase ends, AI lines too.
- It gives tips about your own habits (moving shots, spray length, reloads). It never gives tactical calls, and it never tells you about enemies.
- It reads pixels from your screen. It also notes when your movement, reload, weapon and ability keys and your fire button go down and up, the same way input overlays for streamers do. It does not read game memory, change game files, or send keys to the game. (The optional "mark that" command can press your recorder's clip hotkey. It never presses a game key.)

It is still not approved by Riot, and Riot decides what is allowed. Between-round tips are a grey area.

- You use it at your own risk. You are responsible for your own account.
- Test it in Deathmatch, Team Deathmatch, and unrated first.
- Decide for yourself if you want to use it in Ranked.
- Share it only with friends who read this section. Do not publish it or sell it without Riot approval.

## Progress over many matches

After each match, the coach adds one line to `reports\history.jsonl`. The debrief then compares the match with your last 5 matches and says the biggest change, for example: "Progress: First shots while moving, 32 percent this match, against 50 percent in your last 5 matches." It needs at least 2 earlier matches in the current season. The **Reports** page of the Control Panel shows your last 20 matches in a **Progress** table.

**New season:** click **Start a new season** on the Reports page when a new competitive act starts. The debrief then compares only the matches after that point. Older matches stay in the table, under the season line.

**Scoreboard numbers:** after a match, open the **SUMMARY** tab while the coach still runs. The coach finds your own line (the yellow stripe at the left edge) and reads your combat score, kills / deaths / assists, trades, first bloods, plants and defuses. It adds them to the match report and the Progress table. It needs the same numbers in two readings in a row, and it waits up to 10 minutes after the match.

Reports from before this version are not in the history, because older versions counted some things wrong (deaths, false sprays, moving shots).

## Share the coach with a friend

Do not zip the coach folder yourself. It has your API keys (`secrets.yaml`), your settings, your screenshots, your recordings and your reports.

1. Double-click **Make Share Zip.bat** in the coach folder.
2. The zip goes into the `share` folder inside the coach folder, for example `share\valorant_coach_share_20261115.zip`. File Explorer opens and shows it. It has only the program files.
3. Your friend unzips it and runs `install.bat`, then follows QUICKSTART.md. They need their own API keys for the AI coach and for ElevenLabs.


## Instant replay on stream (OBS)

Say "Bruce, replay that" (or "instant replay", or "run it back"). The coach tells OBS Studio to save its replay buffer and play it in your replay scene. Your viewers see the replay. Your game screen does not change. Turn it on in the Control Panel: **Settings > Stream instant replay (OBS)**. The OBS steps are on page 2 of `Coach Commands and OBS Setup.pdf`. Page 1 lists all voice commands.

Say "Bruce, slow mo" (or "slow motion", "slow that down", "replay that in slow motion") for a slow motion replay. The coach plays the last half of the clip at half speed, so it follows your OBS replay buffer time: a 20 second buffer gives 10 seconds of slow motion, which take 20 seconds on stream. A 30 second buffer gives 15 seconds. To change this, set `obs_replay.slowmo_part` (0.5 is half the clip) and `obs_replay.slowmo_speed_percent` (50 is half speed).

## ElevenLabs voices (optional)

ElevenLabs voices sound more human than Windows voices. You need an ElevenLabs account and an API key.

1. In the Control Panel, open **Settings**, then **ElevenLabs voices**.
2. Paste your API key. Give the key "Text to Speech" and "Voices: Read" access.
3. Click **Load my voices**, and choose a voice. To give one personality its own voice, open **A different voice for one personality**.
4. Turn on **Use ElevenLabs voices**, and click **Hear it**.

Each spoken line uses ElevenLabs characters. The coach saves each line in `cache\voice`, so a repeated line uses no characters. If ElevenLabs is slow or gives an error, the Windows voice speaks the line. If the key is wrong or the characters run out, the coach uses the Windows voice until it restarts. The key goes into `secrets.yaml`.

## Run the tests

Run `python -m pytest -q`. The tests use synthetic frames and a small set of real HUD pictures in `tests/pictures.npz`. The tests are not in the share zip. The tests do not need Valorant or Tesseract.

## How the coach reads numbers

The coach reads ammo, health, and the round score with its own digit reader (`vcoach/digits.py`). The digit reader compares the screen with stored digit shapes from real Valorant pictures. One read takes about 2 milliseconds, so the coach reads ammo and health 20 times each second. The Windows text reader reads only the words (banner, menus and agent names). To read numbers with Tesseract again, add `number_reader: tesseract` to `config.yaml`.

The coach also reads two more things with the same method:

- **"SWITCH PLAYER"** at the bottom left. The game shows it when you are dead and watch a teammate. The coach then counts a death and ignores the teammate's ammo and health.
- **Valorant's Shooting Error stat** (Settings > Video > Stats > Shooting Error, Text Only). The stat shows the spread of each shot for about 0.1 seconds. A first shot above 1 degree means that you moved when you fired. In tests on two full games, standing shots showed 0.16 to 0.9 degrees and moving shots showed 1.9 to 7.4 degrees. Without the stat, the coach uses an optical-flow guess, which matched the stat for about 76% of bursts.

The crosshair placement check is off by default (`checks.crosshair.enabled: false`). In tests on real videos, smokes, Cypher cages, and ability effects made false "enemies", so its tips were not reliable.
