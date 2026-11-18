# TopBar feedback bot

Reads the feedback channel (the messages TopBar's webhook sends) and lets you answer people from Discord.

## What it does
- Runs hidden in the background and starts with Windows (start.bat once; stop.bat to turn off). Errors go to bot.log.
- **Reply in Discord**: right-click a feedback message → Reply → type. The bot adds a ✅ and the person sees it in TopBar within a few minutes.
- `/reply code message`: same thing, using the reply code.
- `/export`: pick what you get (all optional): format (short text for Claude, spreadsheet, full report, or all), newest N, last N days, only new since last export, only unreplied, one type, one person, text search, one version, and whether to include settings changes, PC info, screenshots (zipped) and crash details.
- `/defaults`: save what `/export` gives you when you don't pick anything (`reset` puts it back).
- old `/export` notes: every feedback message as `topbar-feedback.csv` (opens in Excel / Google Sheets) and `topbar-feedback.md`: what each person said, version, and how many settings they changed and which ones.
- `/settings`: the settings people change the most, with counts.

## Set up (once)
1. Go to https://discord.com/developers/applications → **New Application** → **Bot** → **Reset Token** and copy it.
   On the same page turn on **Message Content Intent**.
2. **OAuth2 → URL Generator**: tick `bot` and `applications.commands`, then permissions *View Channels, Read Message History, Send Messages, Add Reactions, Attach Files*. Open the link and add it to your server.
3. Make a GitHub token: https://github.com/settings/personal-access-tokens → fine-grained, only the `topbar` repo, **Contents: Read and write**.
4. In Discord turn on Developer Mode (Settings → Advanced), right-click the feedback channel → **Copy Channel ID**, and right-click yourself → **Copy User ID**.
5. Run it (Python 3.10+):
   ```
   pip install -r requirements.txt
   set DISCORD_TOKEN=...
   set GITHUB_TOKEN=...
   set FEEDBACK_CHANNEL_ID=...
   set OWNER_ID=...
   python bot.py
   ```
   The bot only works while this is running. To keep it on all the time, host it on a free service such as Railway or a Raspberry Pi, with the same four settings.

Never paste the tokens into Discord or into the code.
