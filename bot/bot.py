"""TopBar feedback bot.

- Reply to a feedback message in Discord (normal Discord reply) -> the person gets it in TopBar.
- /reply code message   -> same thing, by reply code.
- /export               -> every feedback message as a spreadsheet (CSV) + a readable summary.
- /settings             -> which settings people change the most.

Replies are written to replies.txt in the GitHub repo, which TopBar already checks.
Setup: see README.md in this folder. Secrets come from environment variables only.
"""
import base64, csv, io, os, re
from collections import Counter

import aiohttp
import discord
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]
GH_TOKEN = os.environ["GITHUB_TOKEN"]
CHANNEL = int(os.environ["FEEDBACK_CHANNEL_ID"])
REPO = os.environ.get("REPO", "jesse-delman/topbar")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # only this person can reply (0 = anyone with Manage Messages)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

CHANGE_RE = re.compile(r"^\d\d-\d\d \d\d:\d\d\s+(.+?)(?::\s*(-?\d+)\s*\u2192\s*(-?\d+))?\s*$")


def allowed(user: discord.abc.User, member_perms=None) -> bool:
    if OWNER_ID:
        return user.id == OWNER_ID
    return bool(member_perms and member_perms.manage_messages)


def field(e: discord.Embed, name: str) -> str:
    for f in e.fields:
        if f.name.lower().startswith(name.lower()):
            return f.value or ""
    return ""


def parse(msg: discord.Message):
    """One feedback message -> dict, or None if it isn't one."""
    if not msg.embeds:
        return None
    e = msg.embeds[0]
    code = field(e, "Reply code").strip("` ")
    if not code or code == "none":
        return None
    author = e.author.name if e.author else ""
    kind, who = author, ""
    m = re.match(r"^\S+\s+(.*?) from (.*)$", author)
    if m:
        kind, who = m.group(1), m.group(2)
    changes = []
    for line in field(e, "Settings they changed").strip("`\n ").splitlines():
        m = CHANGE_RE.match(line.strip())
        if m:
            name = m.group(1).replace("changed text", "").strip(": ") or m.group(1)
            changes.append((name, m.group(2), m.group(3)))
    ver = re.sub(r"[*]", "", field(e, "Version"))
    return {
        "when": msg.created_at.strftime("%Y-%m-%d %H:%M"),
        "kind": kind, "who": who, "title": e.title or "",
        "message": e.description or "", "version": ver,
        "windows": field(e, "Windows"), "code": code,
        "was_doing": field(e, "Was doing"), "setup": field(e, "Setup"),
        "changes": changes, "link": msg.jump_url,
    }


async def all_feedback():
    ch = client.get_channel(CHANNEL) or await client.fetch_channel(CHANNEL)
    out = []
    async for m in ch.history(limit=None, oldest_first=True):
        p = parse(m)
        if p:
            out.append(p)
    return out


async def send_reply(code: str, text: str) -> str:
    """Add 'CODE | text' to replies.txt on GitHub. Returns '' or an error."""
    code = re.sub(r"[^0-9A-Fa-f]", "", code).upper()
    if not code:
        return "That isn't a reply code."
    line = f"{code} | " + text.replace("\r", "").replace("\n", "\\n").strip()
    url = f"https://api.github.com/repos/{REPO}/contents/replies.txt"
    h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    async with aiohttp.ClientSession(headers=h) as s:
        for _ in range(3):  # retry if someone else changed the file at the same time
            async with s.get(url) as r:
                if r.status != 200:
                    return f"GitHub said {r.status} reading replies.txt"
                j = await r.json()
            body = base64.b64decode(j["content"]).decode("utf-8")
            if not body.endswith("\n"):
                body += "\n"
            body += line + "\n"
            data = {"message": f"Reply to {code}", "sha": j["sha"],
                    "content": base64.b64encode(body.encode("utf-8")).decode()}
            async with s.put(url, json=data) as r:
                if r.status in (200, 201):
                    return ""
                if r.status != 409:
                    return f"GitHub said {r.status} saving replies.txt"
    return "replies.txt kept changing, try again"


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")


@client.event
async def on_message(msg: discord.Message):
    # replying to a feedback message in Discord sends that reply to the person
    if msg.author.bot or msg.channel.id != CHANNEL or not msg.reference or not msg.content.strip():
        return
    if not allowed(msg.author, msg.channel.permissions_for(msg.author) if msg.guild else None):
        return
    try:
        src = msg.reference.resolved or await msg.channel.fetch_message(msg.reference.message_id)
    except discord.HTTPException:
        return
    p = parse(src) if isinstance(src, discord.Message) else None
    if not p:
        return
    err = await send_reply(p["code"], msg.content)
    if err:
        await msg.reply(f"Couldn't send: {err}", mention_author=False)
    else:
        await msg.add_reaction("\u2705")  # sent; shows up in their TopBar within a few minutes


@tree.command(description="Send a reply to someone's TopBar by their reply code")
async def reply(inter: discord.Interaction, code: str, message: str):
    if not allowed(inter.user, inter.permissions):
        return await inter.response.send_message("Only the developer can reply.", ephemeral=True)
    await inter.response.defer(ephemeral=True)
    err = await send_reply(code, message)
    await inter.followup.send(f"Couldn't send: {err}" if err else f"Sent to `{code}`.", ephemeral=True)


@tree.command(description="Export all feedback as a spreadsheet and a summary")
async def export(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    fb = await all_feedback()
    if not fb:
        return await inter.followup.send("No feedback found.", ephemeral=True)

    sheet = io.StringIO()
    w = csv.writer(sheet)
    w.writerow(["When", "Type", "From", "Title", "What they said", "Version", "Windows",
                "Reply code", "Was doing", "Settings changed (count)", "Settings changed", "Link"])
    for p in fb:
        ch = "; ".join(f"{n} {a}\u2192{b}" if a is not None else n for n, a, b in p["changes"])
        w.writerow([p["when"], p["kind"], p["who"], p["title"], p["message"], p["version"], p["windows"],
                    p["code"], p["was_doing"], len(p["changes"]), ch, p["link"]])

    kinds = Counter(p["kind"] for p in fb)
    people = len({p["code"] for p in fb})
    top = Counter(n for p in fb for n, _, _ in p["changes"]).most_common(25)
    md = [f"# TopBar feedback ({len(fb)} messages from {people} people)", ""]
    md += [f"- {k}: {n}" for k, n in kinds.most_common()]
    md += ["", "## Settings changed most", ""] + [f"- {n} \u2014 {c}" for n, c in top]
    md += ["", "## Everything people said", ""]
    for p in fb:
        md += [f"### {p['when']} \u00b7 {p['kind']} from {p['who']} \u00b7 `{p['code']}`",
               f"**{p['title']}**" if p["title"] else "", p["message"], "",
               f"Version {p['version']} \u00b7 Windows {p['windows']} \u00b7 {len(p['changes'])} settings changed", ""]
        md += [f"  - {n}" + (f": {a} \u2192 {b}" if a is not None else "") for n, a, b in p["changes"]]
        md += [""]

    files = [discord.File(io.BytesIO(sheet.getvalue().encode("utf-8-sig")), "topbar-feedback.csv"),
             discord.File(io.BytesIO("\n".join(md).encode("utf-8")), "topbar-feedback.md")]
    await inter.followup.send(f"{len(fb)} messages from {people} people.", files=files, ephemeral=True)


@tree.command(name="settings", description="Which settings people change the most")
async def settings_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    fb = await all_feedback()
    top = Counter(n for p in fb for n, _, _ in p["changes"]).most_common(20)
    text = "\n".join(f"`{c:>3}`  {n}" for n, c in top) or "No settings changes yet."
    await inter.followup.send(embed=discord.Embed(title="Settings changed most", description=text, color=0x5865F2), ephemeral=True)


client.run(TOKEN)
