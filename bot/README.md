# TopBar feedback bot

Reads the feedback channel (the messages TopBar's webhook sends) and lets you answer people from Discord.

## What it does
- Runs hidden in the background and starts with Windows (start.bat once; stop.bat to turn off). Errors go to bot.log.
- **Reply in Discord**: right-click a feedback message → Reply → type (attach pictures, videos or files too). The bot adds a ✅ and the person sees it in TopBar within about 20 seconds: a card in the middle of their screen that glides into the corner.
- `/reply code message`: same thing, using the reply code.
- `/export`: what's new since your last export (pick "Everything" or "Not replied to yet" if you want), using your saved `/exportsettings`. Screenshots come in a zip: each message has its own folder, and `feedback.html` shows every message with its pictures.
- `/exportsettings`: what `/export` gives you (format, screenshots, settings changed, PC info). `reset` puts it back.
- `/exportfilter`: one type, one person, a word or a version.
- `/undoexport`: exported by mistake? Those messages count as new again.
- `/topsettings`: the settings people change the most, with counts.

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
