# Valorant Screen Coach

This program watches your screen while you play Valorant. At the end of each round, it speaks one short message. The message is a mechanical tip, a mental reset, or credit for a good round. At the end of the match, it tells you one good thing and saves a report.

The program only captures the screen. It does not read game memory, inject into the game, or draw an overlay. It stays silent during rounds.

## What it checks

| Check | How it works |
|---|---|
| Crosshair height | Finds the enemy highlight outline near the crosshair when an enemy first appears. It compares the head height to the screen center. |
| Shooting while moving | Measures your movement with optical flow before your first shot. It removes camera rotation, so turning does not count as moving. It does not judge flicks. |
| Spraying too long | Reads the ammo counter. It counts the shots in each burst. |
| Reloading at bad times | Flags a reload when you had ammo left and took damage in the last 2.5 seconds. |
| Mental coach | Gives a reset after a loss streak or a lost lead. It gives credit for clutches, aces, and wins after a streak. It gives a "one focus" message after two lost rounds. |
| Voice commands | Say "Bruce" and ask a question or give a command. See "Talk to the coach". |
| AI coach (optional) | Claude or ChatGPT writes the coach lines, so they sound less scripted. See "AI coach". |

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
| AI coach | Turn on Claude or ChatGPT, save your key, and test the lines. |
| Settings | Choose the Windows voice and speed, and get more voices. Change the checks, voice commands, AI timing, and screen capture. |
| Reports | Read the report for each match. |
| Help | Read the quick start guide. |

**Start coach** and **Stop coach** at the top run the coach from the panel. **Coach log** shows what the coach does and says. **Stop coach** stops it cleanly, so it saves the match report.

## Launchers

| File | What it does |
|---|---|
| `install.bat` | Installs the Python packages, Tesseract, and the voice model. It can also make the desktop icons, then opens the Control Panel. |
| `Coach Setup.bat` | Opens the Control Panel. Desktop icon: gear. |
| `Start Coach.bat` | Starts the coach without the panel (`main.py live`). Desktop icon: crosshair. |
| `Classic Setup Menu.bat` | The older text menu with the same tools. Use it if the panel does not work. |
| `editor.html` | The visual region editor. Open it from the Setup menu, so Save can write to `config.yaml`. |
| `create_shortcuts.bat` | Creates the two icon shortcuts in the coach folder and on the desktop. Run it again if you move the folder. |

A `.bat` file cannot have its own icon in Windows. The crosshair and gear icons show only on the shortcuts.

## Setup (Windows)

1. Install Python 3.15 (or 3.14) from python.org/downloads. Use the 64-bit Windows installer. On the first installer screen, tick "Add python.exe to PATH". The coach is tested on Python 3.14 and 3.15.
2. Install Tesseract OCR. install.bat offers to install it (it tries winget, then the official GitHub installer). The setup menu (**I**) and the region editor also have an install button. You can also use the Windows installer from the UB Mannheim Tesseract page. Keep the default install folder. The coach finds it there, so you do not need to change PATH.
3. If Tesseract is not on PATH, set `tesseract_cmd` in `config.yaml` to the full path of `tesseract.exe`.
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
| `random` | Picks a different personality at the start of each match and says which one. |

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
| "Bruce, switch to sassy" | Changes the personality (calm, hype, tough, mate, sassy, random). | At once |
| "Bruce, say that again" | Repeats the last message. | At once |
| "Bruce, help" | Lists the commands. | At once |
| "Bruce, how am I doing?" / "how am I going?" | Score and the main thing to work on. | Between rounds |
| "Bruce, what should I work on?" | Your most common mistake this match. | Between rounds |
| "Bruce, what's the score?" | Rounds won and lost. | Between rounds |

Coaching questions get an answer only between rounds. After a round ends, the coach answers for 35 seconds, which covers the buy phase. If you ask later, the coach says it will answer after the round, and it answers when the round ends. This keeps the coach within Riot's rule against real-time guidance. Mute, unmute, personality, repeat, and help work at any time.

### Tips

- Use a headset. Speakers can let the coach hear its own voice or your teammates.
- Valorant voice chat and the coach can use the same mic at the same time.
- Your teammates hear you say "Bruce" if your voice chat is open. Use the wake word when push-to-talk is off.
- To turn voice control off, set `voice_control.enabled` to `false`, or run `python main.py live --no-mic`.

## AI coach (Claude or ChatGPT)

The coach can use an AI to write its lines, so they sound less scripted. The coach rules still decide when to speak and what about (a tip, a reset, praise, or an answer). The AI only writes the words, in the personality's style.

### What the AI gets

The AI gets short text facts only: the score, the losing streak, the mistake counts, the last few coach lines, and the questions you say after "Bruce". It never gets screenshots or audio.

### Set it up

1. Get an API key:
   - **Claude:** console.anthropic.com > API keys. Add credit on the Billing page.
   - **ChatGPT:** platform.openai.com > API keys. Add credit on the Billing page.
2. Open **Valorant Coach Setup** and press **A** (AI coach setup). You can also run `python main.py ai-setup`.
3. Choose Claude or ChatGPT.
4. Paste your key and press Enter. The key does not show on screen. In the black window, right-click to paste.
5. The setup sends two test requests and shows the AI lines and how long they took.

To hear what each personality sounds like with the AI, press **T** in the Setup menu, or run `python main.py ai-test --personality sassy`.

### How it behaves

- The coach waits up to `ai.timeout_s` seconds (default 4). If the AI is slower, offline, or fails, the coach speaks the scripted line. The coach never goes silent because of the AI.
- AI lines are one or two short sentences (`ai.max_words`, default 25).
- The AI is told to coach habits and mindset only, never tactical calls. It still answers only between rounds.
- With the AI on, "Bruce" understands free questions, for example "Bruce, why do I keep losing gunfights?"
- To keep scripted lines for some messages, remove items from `ai.use_for`: `tips`, `mental`, `answers`, `match_end`.
- To turn the AI off, run the AI setup and choose 3, or set `ai.provider: off`.

### Models and cost

The defaults are `claude-haiku-5-5` and `gpt-5.4-mini`, which are fast and low-cost models. If a model name stops working, change `claude_model` or `chatgpt_model` under `ai:` in `config.yaml`. Each line is a small request. Check the pricing page of your AI provider for current prices.

### Keep your key safe

Your key is in `secrets.yaml` in the coach folder. Do not share that file. If you share the coach folder with a friend, delete `secrets.yaml` from the copy first. You can also use the `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` environment variable instead of the file.

## If the coach does not detect rounds

The coach detects a round end when it reads "WON" or "LOST" in the Round banner box. It needs the same result in two reads, and an ammo or health reading in the last 20 seconds.

1. During a match, press **F9** while the round result shows. The coach saves the screen to the `screenshots` folder and loads it into the region editor.
2. Open Screen regions. Drag the **Round banner** box over the result text and click **Test reading**. The Banner line shows what the coach read.
3. If the words on screen are not WON, WIN, LOST or LOSS, add the real words under `banner_keywords` in `config.yaml`.
4. The file `logs/banner_reads.txt` lists every text the coach read in the banner box during the last session, with the ammo and health values.

## Record a match for troubleshooting

Tick **Record match** in the Control Panel before you click **Start coach** (or run `python main.py live --record`). The recording is for one coach session. It stops when you click **Stop coach**, or after 45 minutes.

The coach saves a folder `recordings/match_<date>_<time>` with these items:

| Item | Contents |
|---|---|
| `summary.txt` | How often the ammo and health readings worked, which result words were found, and every banner text seen. Read this first. |
| `readings.csv` | One line for each reading (about 10 per second): time, ammo, health, and banner text. |
| `banner` folder | A picture of the Round banner box at each banner read (about 2 per second). |
| `hud` folder | A picture of the Health and Ammo boxes at each banner read. |
| `full` folder | The full screen every 2 seconds. |

A Swiftplay match uses about 100 to 200 MB. Delete old recordings when you do not need them. The pictures show your screen, including player names and chat, so check them before you share them.

## Known limits

- **Rounds and matches** come from OCR of the banner text. If the coach misses rounds, check the banner region and the words under `banner_keywords`.
- **Death detection** needs the health counter to show 0. If it misses a death, the program can count your teammate's shots while you spectate.
- **Weapon swaps** can look like a reload. The reload check only counts a reload when you also took damage, which removes most false alarms.
- **Spray length** uses one limit for all weapons. If you play SMGs, raise `long_spray.max_shots`.
- **Crosshair height** uses the highlight color. Red objects in the map, such as abilities or decorations, can cause false readings. Purple or yellow highlight colors usually give fewer false readings.
- **Riot patches** can move or change the HUD. If the checks stop working after a patch, calibrate again.

## Rules

Riot does not allow tools that give real-time performance guidance. This program speaks only between rounds and gives tips about habits, not tactical calls. It is still not approved by Riot.

- Test it in Deathmatch, Team Deathmatch, and unrated first.
- Decide for yourself if you want to use it in Ranked.
- Keep it for your own use. If you want to share it with other players, apply for Riot approval first.

## Run the tests

Run `python -m pytest -q`. The tests use synthetic frames and do not need Valorant or Tesseract.
