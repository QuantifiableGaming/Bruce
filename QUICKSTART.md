# Quick Start

This page gives the minimum steps to install and use the coach. For details and tuning, see README.md.

## Install (one time)

1. Install Python 3.15 (or 3.14) from python.org/downloads. Use the 64-bit Windows installer. On the first installer screen, tick "Add python.exe to PATH".
2. Unzip this folder to a location you will keep, for example `C:\Games\valorant_coach`.
3. Double-click `install.bat`. It installs the Python packages, offers to install Tesseract (press **Y**), and downloads the voice model for "Bruce".
4. When it asks, press **Y** to put the two icons on your desktop:
   - **Valorant Coach Setup** (gear icon) opens the Control Panel in your web browser.
   - **Valorant Coach** (crosshair icon) starts the coach without the panel.
5. At the end, the Control Panel opens in your web browser.

## Set up Valorant (one time)

1. Set Display Mode to **Windowed Fullscreen**.
2. Note your Enemy Highlight Color (Settings > General). Purple or yellow gives fewer false alarms than red.
3. Note which hand shows your weapon (Settings > General > Show Weapon).

## Set up the coach in the Control Panel (one time)

Double-click **Valorant Coach Setup**. The Control Panel opens in your web browser. Keep the small black window open while you use it. You can minimise it.

1. **Home:** click **Install** or **Download** next to each red or yellow item.
2. **Screen regions:** start a Deathmatch in Valorant. Click **Take screenshot**, then switch to Valorant within 10 seconds.
   - Drag each box over its item: **Ammo** (the magazine number only), **Health**, **Round banner**, **Enemy search** (around the crosshair), and **Gun area** (your own gun).
   - Set **Enemy highlight colour** and **Gun side** to match Valorant.
   - Click **Test reading**. Make sure Ammo and Health show the numbers that were on screen.
   - Click **Save to config**.
3. **Personality:** click **Hear it** to listen, then **Use this** on the coach you like.
4. **Voice & mic:** choose your microphone. Click **Start test** and say "Bruce, how am I doing?"
5. **Settings > Coach voice (optional):** choose a Windows voice and speed, and click **Hear it**. To get more voices, click **Open Windows voice settings**, add a voice, then click **Refresh voice list**.
6. **AI coach (optional):** choose Claude or ChatGPT, paste your API key, and click **Test AI lines**.

## Use it (every time)

1. Double-click **Valorant Coach Setup**.
2. Click **Start coach** at the top of the panel.
3. Start Valorant and play. The coach speaks between rounds.
4. Click **Stop coach** when you finish. The coach saves a report, and you can read it on the **Reports** page.

You can also double-click **Valorant Coach** to start the coach without the panel. To stop it, close its window.

## Quick controls

| Action | How |
|---|---|
| Mute or unmute | Press **F8**, or say "Bruce, mute" / "Bruce, unmute". |
| Save a screenshot for the Screen regions page | Press **F9** during a match. |
| Ask how you are doing | Say "Bruce, how am I doing?" |
| Ask what to work on | Say "Bruce, what should I work on?" |
| Ask the score | Say "Bruce, what's the score?" |
| Repeat the last message | Say "Bruce, say that again." |
| Ask anything (AI on) | Say "Bruce, why do I keep losing gunfights?" |
| Change personality | Say "Bruce, switch to sassy", or use the menu at the top of the panel. |
| Change how the coach works | Control Panel > **Settings**. |
| Icons are missing or broken | Double-click `create_shortcuts.bat`. Run it again if you move the folder. |

Coaching questions get an answer between rounds. If you ask during a round, the coach answers when the round ends.

## If something does not work

| Problem | Fix |
|---|---|
| "Python was not found" | Install Python and tick "Add python.exe to PATH". Then run install.bat again. |
| The panel does not open in the browser | Open your browser and go to the address in the black window, for example `http://127.0.0.1:8765`. |
| "The panel helper is not running" | The black window was closed. Double-click **Valorant Coach Setup** again. |
| A red or yellow item on the Home page | Click the button next to it. |
| No sound | Settings > Coach voice > **Hear it**. Check that Windows sound goes to your headset. |
| A new Windows voice is not in the list | Click **Refresh voice list**. Some newer "Natural" Windows voices do not work with the coach. |
| The coach never speaks after a round | The coach did not read the round banner. During a match, press **F9** while the round result shows on screen. After the match, open Screen regions: the picture shows the banner. Drag the **Round banner** box over the text, click **Test reading**, and save. |
| You want to show what the coach read in a match | Tick **Record match** at the top of the panel before you click **Start coach**. After the match, click **Stop coach**. Open the newest folder in `recordings` and read `summary.txt`. |
| No report after a match | The coach saves a report only when it detects rounds. Fix the round banner first (see the row above). |
| "Bruce" hears the wrong words | Settings > Voice commands > Voice model: choose **Large**. Then click **Download** on the Home page. |
| Ammo or health shows a wrong number | Screen regions: make the box cover the whole number, with a little space around it. |
| "Enemy outline found" with no enemy on screen | Screen regions: look at the magenta pixels after **Test reading**. Move the **Gun area** box over your gun. Check the colour dropdown. |
| It does not hear "Bruce" | Voice & mic > **Start test**. Add the word it hears instead to `wake_aliases` in `config.yaml`. |
| AI lines do not play | AI coach > **Test AI lines** and read the error. The coach uses scripted lines until it works. |
| Black screenshot | Set Valorant to Windowed Fullscreen. |
| The panel does not work at all | Double-click `Classic Setup Menu.bat` in the coach folder. It has the same tools in a text menu. |

## Before you use it in Ranked

Riot has not approved this tool. Test it in Deathmatch and unrated first. Keep it for your own use.
