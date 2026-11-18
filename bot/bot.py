"""TopBar feedback bot.

- Reply to a feedback message in Discord (normal Discord reply) -> the person gets it in TopBar.
- /reply code message   -> same thing, by reply code.
- /export               -> feedback, filtered how you like (who, type, days, unreplied...), as short text for Claude,
                           a spreadsheet, or a full report, with screenshots in a zip.
- /defaults             -> what /export gives you when you don't pick anything.
- /settings             -> which settings people change the most.

Replies are written to replies.txt in the GitHub repo, which TopBar already checks.
Setup: see README.md in this folder. Secrets come from environment variables only.
"""
import base64, csv, io, json, os, re, sys, zipfile
from datetime import datetime, timedelta, timezone
from collections import Counter

# runs hidden in the background: keys come from keys.txt next to this file, errors go to bot.log
HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(HERE, "keys.txt")):
    for _l in open(os.path.join(HERE, "keys.txt"), encoding="utf-8"):
        if "=" in _l:
            _k, _v = _l.strip().split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
if sys.stdout is None:
    sys.stdout = sys.stderr = open(os.path.join(HERE, "bot.log"), "a", encoding="utf-8", buffering=1)

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
        "changes": changes, "link": msg.jump_url, "id": msg.id, "dt": msg.created_at,
        "pc": field(e, "PC"), "crash": field(e, "Crash details"),
        "images": [a for a in msg.attachments if (a.content_type or "").startswith("image/") or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))],
        "longtext": next((a for a in msg.attachments if a.filename == "message.txt"), None),
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


# ---------------------------------------------------------------- updates itself from GitHub within seconds of a change
# Asks GitHub every 5 seconds "has the bot folder changed?" with an ETag: an unchanged answer is free (doesn't use up the token's limit).
API = f"https://api.github.com/repos/{REPO}"
GH = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
_etag, _sha = None, None


async def self_update(force=False) -> bool:
    import subprocess
    global _etag, _sha
    async with aiohttp.ClientSession(headers=GH) as s:
        h = {} if force or not _etag else {"If-None-Match": _etag}
        async with s.get(f"{API}/commits", params={"path": "bot", "per_page": "1"}, headers=h) as r:
            if r.status != 200:
                return False
            _etag = r.headers.get("ETag")
            sha = (await r.json())[0]["sha"]
        if sha == _sha and not force:
            return False
        first = _sha is None
        _sha = sha
        changed = False
        for name in ("requirements.txt", "start.bat", "stop.bat", "README.md", "bot.py"):
            async with s.get(f"{API}/contents/bot/{name}", params={"ref": sha}, headers={"Accept": "application/vnd.github.raw"}) as r:
                if r.status != 200:
                    continue
                new = await r.read()
            path = os.path.join(HERE, name)
            old = open(path, "rb").read() if os.path.exists(path) else b""
            if new.replace(b"\r\n", b"\n") == old.replace(b"\r\n", b"\n"):
                continue
            if name == "bot.py":
                try:
                    compile(new, "bot.py", "exec")  # never install a broken file
                except SyntaxError:
                    continue
            open(path, "wb").write(new)
            print(f"updated {name}")
            changed = changed or name in ("bot.py", "requirements.txt")
            if name == "requirements.txt":
                py = sys.executable.replace("pythonw.exe", "python.exe")
                subprocess.run([py, "-m", "pip", "install", "-q", "-r", path], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if changed:
        print("restarting into the new version")
        await client.close()
        os.execv(sys.executable, [sys.executable, os.path.join(HERE, "bot.py")])
    return changed


async def update_loop():
    import asyncio
    while True:
        try:
            await self_update()
        except Exception as ex:
            print("update check failed:", ex)
        await asyncio.sleep(5)


@client.event
async def on_ready():
    if not getattr(client, "_upd", None):
        client._upd = client.loop.create_task(update_loop())
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
    if not err:
        mark_replied(p["id"])
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
    if not err:
        st = load_state(); st.setdefault("replied_codes", []).append(code.strip("` ").upper()); save_state(st)
    await inter.followup.send(f"Couldn't send: {err}" if err else f"Sent to `{code}`.", ephemeral=True)



# ---------------------------------------------------------------- saved settings and what's been seen / replied
STATE = os.path.join(HERE, "state.json")
DEFAULTS = {"format": "claude", "settings": True, "pc": False, "screenshots": True, "crash": True,
            "newest": 0, "days": 0, "unreplied": False, "new_only": False, "max_chars": 1500}


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), indent=1, default=str)


def mark_replied(mid):
    st = load_state(); st.setdefault("replied_ids", []).append(mid); save_state(st)


def is_replied(p, st):
    return p["id"] in st.get("replied_ids", []) or p["code"].upper() in st.get("replied_codes", [])


def opts():
    d = dict(DEFAULTS); d.update(load_state().get("defaults", {})); return d


FORMATS = [app_commands.Choice(name="Short text for Claude (fewest tokens)", value="claude"),
           app_commands.Choice(name="Spreadsheet (CSV)", value="csv"),
           app_commands.Choice(name="Full report (Markdown)", value="full"),
           app_commands.Choice(name="Everything (all three)", value="all")]
KINDS = [app_commands.Choice(name=k, value=k) for k in ("Bug", "Idea", "Question", "Other", "Crash")]


def change_text(p):
    return "; ".join(f"{n} {a}→{b}" if a is not None else n for n, a, b in p["changes"])


def clip(t, n):
    t = (t or "").strip()
    return t if not n or len(t) <= n else t[:n].rstrip() + "…"


def img_name(p, i, a):
    ext = os.path.splitext(a.filename)[1] or ".png"
    return f"screenshots/{p['dt'].strftime('%Y-%m-%d_%H%M')}_{p['code']}" + (f"_{i+1}" if i else "") + ext


def build_claude(fb, o):
    # one compact block per message: only what helps, no labels repeated per field
    out = [f"TopBar feedback, {len(fb)} messages. Format: date | type | who (reply code) | version",
           ""]
    for p in fb:
        out.append(f"{p['dt'].strftime('%m-%d')} | {p['kind']} | {p['who']} ({p['code']}) | {p['version']}")
        if p["title"]:
            out.append("Title: " + p["title"])
        out.append(clip(p["full_text"], o["max_chars"]))
        if p["was_doing"] and p["was_doing"] != "—":
            out.append("Doing: " + p["was_doing"])
        if o["settings"] and p["changes"]:
            out.append(f"Settings ({len(p['changes'])}): " + change_text(p))
        if o["pc"]:
            out.append("PC: " + " ".join((p["windows"] + " " + p["pc"] + " " + p["setup"]).split()))
        if o["crash"] and p["crash"]:
            out.append("Crash: " + " ".join(p["crash"].strip("`").split()))
        if o["screenshots"] and p["images"]:
            out.append("Screenshot: " + ", ".join(img_name(p, i, a) for i, a in enumerate(p["images"])))
        out.append("")
    return "\n".join(out)


def build_csv(fb, o):
    sheet = io.StringIO(); w = csv.writer(sheet)
    cols = ["When", "Type", "From", "Reply code", "Version", "Title", "What they said", "Was doing", "Replied"]
    if o["settings"]: cols += ["Settings changed (count)", "Settings changed"]
    if o["pc"]: cols += ["Windows", "PC", "Setup"]
    if o["crash"]: cols += ["Crash"]
    if o["screenshots"]: cols += ["Screenshots"]
    w.writerow(cols + ["Link"])
    for p in fb:
        r = [p["when"], p["kind"], p["who"], p["code"], p["version"], p["title"], p["full_text"], p["was_doing"], "yes" if p["replied"] else ""]
        if o["settings"]: r += [len(p["changes"]), change_text(p)]
        if o["pc"]: r += [p["windows"], p["pc"], p["setup"]]
        if o["crash"]: r += [p["crash"]]
        if o["screenshots"]: r += [", ".join(img_name(p, i, a) for i, a in enumerate(p["images"]))]
        w.writerow(r + [p["link"]])
    return sheet.getvalue()


def build_full(fb, o):
    kinds = Counter(p["kind"] for p in fb)
    md = [f"# TopBar feedback ({len(fb)} messages from {len({p['code'] for p in fb})} people)", ""]
    md += [f"- {k}: {n}" for k, n in kinds.most_common()]
    if o["settings"]:
        top = Counter(n for p in fb for n, _, _ in p["changes"]).most_common(25)
        md += ["", "## Settings changed most", ""] + [f"- {n} — {c}" for n, c in top]
    md += ["", "## Messages", ""]
    for p in fb:
        md += [f"### {p['when']} · {p['kind']} from {p['who']} · `{p['code']}`" + (" · replied" if p["replied"] else ""), ""]
        if p["title"]: md += [f"**{p['title']}**", ""]
        md += [p["full_text"], "", f"Version {p['version']} · Windows {p['windows']} · doing: {p['was_doing']}", ""]
        if o["settings"]:
            md += [f"{len(p['changes'])} settings changed"] + [f"  - {n}" + (f": {a} → {b}" if a is not None else "") for n, a, b in p["changes"]] + [""]
        if o["pc"]: md += ["PC: " + " · ".join(x for x in (p["pc"], p["setup"]) if x).replace("\n", " · "), ""]
        if o["crash"] and p["crash"]: md += ["Crash:", p["crash"], ""]
        if o["screenshots"]:
            md += [f"![screenshot]({img_name(p, i, a)})" for i, a in enumerate(p["images"])] + [""]
    return "\n".join(md)


async def gather(o, kind=None, person=None, search=None, version=None):
    st = load_state(); fb = await all_feedback()
    if o["days"]:
        since = datetime.now(timezone.utc) - timedelta(days=o["days"]); fb = [p for p in fb if p["dt"] >= since]
    if o["new_only"] and st.get("last_export"):
        last = datetime.fromisoformat(st["last_export"]); fb = [p for p in fb if p["dt"] > last]
    if kind: fb = [p for p in fb if kind.lower() in p["kind"].lower() or (kind == "Crash" and p["crash"])]
    if person:
        q = person.lower().strip("` "); fb = [p for p in fb if q in p["who"].lower() or q == p["code"].lower()]
    if version: fb = [p for p in fb if version.lower() in p["version"].lower()]
    for p in fb: p["replied"] = is_replied(p, st)
    if o["unreplied"]: fb = [p for p in fb if not p["replied"]]
    if search:
        q = search.lower(); fb = [p for p in fb if q in (p["title"] + " " + p["message"]).lower()]
    if o["newest"]: fb = fb[-o["newest"]:]
    async with aiohttp.ClientSession() as s:   # long messages come as message.txt: use the whole text
        for p in fb:
            p["full_text"] = p["message"]
            if p["longtext"]:
                try:
                    async with s.get(p["longtext"].url) as r: p["full_text"] = (await r.text()).strip()
                except Exception: pass
    return fb


@tree.command(description="Export feedback. Anything you leave empty uses your /defaults")
@app_commands.describe(
    format="What you get back", newest="Only the newest N messages", days="Only the last N days",
    new_only="Only what came in since your last export", unreplied="Only messages you haven't replied to",
    kind="Only one type", person="Only one person (name or reply code)", search="Only messages containing this text",
    version="Only this version (e.g. 1.0.1)", settings="Include which settings they changed", pc="Include PC / Windows / setup info",
    screenshots="Include screenshots (zip)", crash="Include crash details", max_chars="Cut each message to this many characters in the Claude text (0 = never)")
@app_commands.choices(format=FORMATS, kind=KINDS)
async def export(inter: discord.Interaction, format: app_commands.Choice[str] = None, newest: int = None, days: int = None,
                 new_only: bool = None, unreplied: bool = None, kind: app_commands.Choice[str] = None, person: str = None,
                 search: str = None, version: str = None, settings: bool = None, pc: bool = None, screenshots: bool = None,
                 crash: bool = None, max_chars: int = None):
    await inter.response.defer(ephemeral=True)
    o = opts()
    for k, v in (("format", format.value if format else None), ("newest", newest), ("days", days), ("new_only", new_only),
                 ("unreplied", unreplied), ("settings", settings), ("pc", pc), ("screenshots", screenshots),
                 ("crash", crash), ("max_chars", max_chars)):
        if v is not None: o[k] = v
    fb = await gather(o, kind.value if kind else None, person, search, version)
    if not fb:
        return await inter.followup.send("No feedback matches that.", ephemeral=True)

    texts = {}
    if o["format"] in ("claude", "all"): texts["topbar-feedback.txt"] = build_claude(fb, o)
    if o["format"] in ("csv", "all"): texts["topbar-feedback.csv"] = "﻿" + build_csv(fb, o)
    if o["format"] in ("full", "all"): texts["topbar-feedback.md"] = build_full(fb, o)

    # screenshots: downloaded and zipped with the text, split into parts under Discord's upload limit
    files, LIMIT = [], 9_500_000
    shots = [(img_name(p, i, a), a) for p in fb for i, a in enumerate(p["images"])] if o["screenshots"] else []
    if shots:
        zips, cur, size = [], None, 0
        def new_zip():
            b = io.BytesIO(); z = zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED); zips.append((b, z)); return z
        cur = new_zip()
        for n, t in texts.items(): cur.writestr(n, t)
        size = sum(len(t) for t in texts.values()) // 3
        async with aiohttp.ClientSession() as s:
            for name, a in shots:
                try:
                    async with s.get(a.url) as r: data = await r.read()
                except Exception: continue
                if size + len(data) > LIMIT and size > 0:
                    cur = new_zip(); size = 0
                cur.writestr(name, data); size += len(data)
        for b, z in zips: z.close()
        for i, (b, z) in enumerate(zips):
            files.append(discord.File(io.BytesIO(b.getvalue()), "topbar-feedback.zip" if len(zips) == 1 else f"topbar-feedback-part{i+1}.zip"))
    else:
        files = [discord.File(io.BytesIO(t.encode("utf-8")), n) for n, t in texts.items()]

    st = load_state(); st["last_export"] = datetime.now(timezone.utc).isoformat(); save_state(st)
    words = sum(len(t.split()) for n, t in texts.items() if n.endswith(".txt"))
    info = f"{len(fb)} messages from {len({p['code'] for p in fb})} people" + (f", {len(shots)} screenshots" if shots else "") + (f" · about {int(words * 1.4):,} tokens of text" if words else "")
    for i in range(0, len(files), 10):
        await inter.followup.send(info if i == 0 else "…more", files=files[i:i + 10], ephemeral=True)


@tree.command(name="defaults", description="Change what /export gives you by default (leave empty to just see them)")
@app_commands.describe(format="Default format", settings="Include settings changes", pc="Include PC info", screenshots="Include screenshots",
                       crash="Include crash details", newest="Only newest N (0 = all)", days="Only last N days (0 = all)",
                       new_only="Only new since last export", unreplied="Only unreplied", max_chars="Cut long messages in the Claude text (0 = never)",
                       reset="Put everything back to normal")
@app_commands.choices(format=FORMATS)
async def defaults(inter: discord.Interaction, format: app_commands.Choice[str] = None, settings: bool = None, pc: bool = None,
                   screenshots: bool = None, crash: bool = None, newest: int = None, days: int = None, new_only: bool = None,
                   unreplied: bool = None, max_chars: int = None, reset: bool = False):
    if not allowed(inter.user, inter.permissions):
        return await inter.response.send_message("Only the developer can change this.", ephemeral=True)
    st = load_state(); d = {} if reset else st.get("defaults", {})
    for k, v in (("format", format.value if format else None), ("settings", settings), ("pc", pc), ("screenshots", screenshots),
                 ("crash", crash), ("newest", newest), ("days", days), ("new_only", new_only), ("unreplied", unreplied), ("max_chars", max_chars)):
        if v is not None: d[k] = v
    st["defaults"] = d; save_state(st)
    o = opts(); names = {c.value: c.name for c in FORMATS}
    lines = [f"**Format:** {names.get(o['format'], o['format'])}",
             f"**Include:** " + ", ".join(n for n, k in (("settings", "settings"), ("PC info", "pc"), ("screenshots", "screenshots"), ("crashes", "crash")) if o[k]) or "nothing extra",
             f"**Only:** " + (", ".join(x for x in (f"newest {o['newest']}" if o["newest"] else "", f"last {o['days']} days" if o["days"] else "",
                                                     "new since last export" if o["new_only"] else "", "unreplied" if o["unreplied"] else "") if x) or "everything"),
             f"**Cut messages at:** {o['max_chars'] or 'never'} characters"]
    await inter.response.send_message(embed=discord.Embed(title="Export defaults", description="\n".join(lines), color=0x5865F2), ephemeral=True)

@tree.command(name="update", description="Get the newest version of this bot now")
async def update_cmd(inter: discord.Interaction):
    await inter.response.send_message("Checking\u2026 if there's a new version the bot restarts in a few seconds.", ephemeral=True)
    await self_update(force=True)


@tree.command(name="settings", description="Which settings people change the most")
async def settings_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    fb = await all_feedback()
    top = Counter(n for p in fb for n, _, _ in p["changes"]).most_common(20)
    text = "\n".join(f"`{c:>3}`  {n}" for n, c in top) or "No settings changes yet."
    await inter.followup.send(embed=discord.Embed(title="Settings changed most", description=text, color=0x5865F2), ephemeral=True)


client.run(TOKEN)
