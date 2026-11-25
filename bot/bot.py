"""TopBar feedback bot.

- Reply to a feedback message in Discord (normal Discord reply) -> the person gets it in TopBar, with any files or pictures.
- /reply code message   -> same thing, by reply code.
- /export               -> every feedback message: what they wrote, their whole PC, and their pictures. No options.

Replies are written to replies.txt in the GitHub repo, which TopBar already checks.
Setup: see README.md in this folder. Secrets come from keys.txt next to this file only.
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
    ver = re.sub(r"[*]", "", field(e, "Version"))
    return {
        "when": msg.created_at.strftime("%Y-%m-%d %H:%M"),
        "kind": kind, "who": who, "title": e.title or "",
        "message": e.description or "", "version": ver,
        "windows": field(e, "Windows"), "code": code,
        "was_doing": field(e, "Was doing"), "setup": field(e, "Setup"),
        "link": msg.jump_url, "id": msg.id, "dt": msg.created_at,
        "pc": field(e, "PC"), "crash": field(e, "Crash details"),
        "images": images_of(msg),
        "longtext": next((a for a in msg.attachments if a.filename == "message.txt"), None),
    }


IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def images_of(msg):
    """Every picture on a feedback message: the screenshot TopBar puts inside the embed
    (attachment://screen.png shows up as the embed image, not as an attachment) plus any attachments."""
    out, seen = [], set()
    def add(url, name):
        key = url.split("?")[0]
        if url and key not in seen:
            seen.add(key); out.append({"url": url, "filename": name or os.path.basename(key) or "screen.png"})
    for e in msg.embeds:
        for part in (e.image, e.thumbnail):
            u = getattr(part, "proxy_url", None) or getattr(part, "url", None)
            if u and not u.startswith("attachment://"):
                add(getattr(part, "url", None) or u, os.path.basename(u.split("?")[0]))
    for a in msg.attachments:
        if (a.content_type or "").startswith("image/") or a.filename.lower().endswith(IMG_EXT):
            add(a.url, a.filename)
    return out


async def all_feedback():
    ch = client.get_channel(CHANNEL) or await client.fetch_channel(CHANNEL)
    out = []
    async for m in ch.history(limit=None, oldest_first=True):
        p = parse(m)
        if p:
            out.append(p)
    return out


async def upload_files(code: str, atts) -> list:
    """Put each attachment (pictures, videos, any file) in the repo under replies/, so TopBar can show or download it. Returns raw URLs."""
    urls = []
    h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    async with aiohttp.ClientSession(headers=h) as s:
        for a in atts:   # every file, however many
            if a.size > 95_000_000:   # GitHub's own limit for one file
                continue
            data = await a.read()
            name = re.sub(r"[^A-Za-z0-9._-]", "_", a.filename)[:80] or "file"
            path = f"replies/{code}-{a.id}-{name}"
            body = {"message": f"Reply file for {code}", "content": base64.b64encode(data).decode()}
            async with s.put(f"https://api.github.com/repos/{REPO}/contents/{path}", json=body) as r:
                if r.status in (200, 201):
                    urls.append(f"https://raw.githubusercontent.com/{REPO}/main/{path}")
    return urls


async def send_reply(code: str, text: str, atts=()) -> str:
    """Add 'CODE | text ||| file-url ...' to replies.txt on GitHub. Returns '' or an error."""
    code = re.sub(r"[^0-9A-Fa-f]", "", code).upper()
    if not code:
        return "That isn't a reply code."
    urls = await upload_files(code, list(atts)) if atts else []
    line = f"{code} | " + (text.replace("\r", "").replace("\n", "\\n").strip() or "(see the attachment)") + "".join(f" ||| {u}" for u in urls)
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
_etag, _sha, _pause = None, None, 0


async def self_update(force=False) -> bool:
    import subprocess
    global _etag, _sha
    async with aiohttp.ClientSession(headers=GH) as s:
        h = {} if force or not _etag else {"If-None-Match": _etag}
        async with s.get(f"{API}/commits", params={"path": "bot", "per_page": "1"}, headers=h) as r:
            if r.status in (403, 429):  # GitHub asked us to slow down: wait what it says (at least a minute)
                global _pause
                _pause = max(60, int(r.headers.get("Retry-After", "60") or 60))
                return False
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
                import asyncio
                await asyncio.to_thread(subprocess.run, [py, "-m", "pip", "install", "-q", "-r", path], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # never freezes the bot
    if changed:
        print("restarting into the new version")
        import subprocess
        # start the new copy first (properly quoted, so a Python in "Program Files" works), then leave. os.execv broke on paths with spaces
        # and left the bot offline, which is why Discord said "The application did not respond".
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, os.path.join(HERE, "bot.py")], cwd=HERE, creationflags=flags, close_fds=True)
        await client.close()
        os._exit(0)
    return changed


async def update_loop():
    import asyncio
    global _pause
    while True:
        if _pause:
            p, _pause = _pause, 0
            await asyncio.sleep(p)
        try:
            await self_update()
        except Exception as ex:
            print("update check failed:", ex)
        await asyncio.sleep(5)


# ---------------------------------------------------------------- keeps the feedback link safe (nothing to set up)
# The app finds its feedback webhook in feedback.txt (scrambled, but public), so someone could try to rename it, delete it or
# post junk through it. Once a minute the bot puts its name, picture and channel back; if it was deleted, it makes a new one in
# the feedback channel and publishes that (apps pick it up on their next update check, within minutes). Anything posted through
# it that isn't a TopBar report is deleted. Needs the bot's "Manage Webhooks" permission; without it this quietly does nothing.
def hook_unscramble(hx: str) -> str:
    b = bytes.fromhex(hx.strip())
    return bytes(c ^ 0x5A for c in reversed(b)).decode("utf-8", "replace")


def hook_scramble(url: str) -> str:
    return "".join(f"{c ^ 0x5A:02x}" for c in reversed(url.encode("utf-8")))


_hook_id = None


async def guard_once():
    global _hook_id
    import base64 as b64
    url = f"{API}/contents/feedback.txt"
    async with aiohttp.ClientSession(headers=GH) as s:
        async with s.get(url) as r:
            if r.status != 200:
                return
            j = await r.json()
        body = b64.b64decode(j["content"]).decode("utf-8")
        m = re.search(r"hookx=([0-9a-fA-F]+)", body)
        if not m:
            return
        hm = re.search(r"/webhooks/(\d+)/", hook_unscramble(m.group(1)))
        if not hm:
            return
        _hook_id = int(hm.group(1))
        st = load_state()
        good = st.get("hook_good")
        try:
            wh = await client.fetch_webhook(_hook_id)
        except discord.NotFound:
            wh = None
        except discord.Forbidden:
            return
        if wh is not None:
            if not good or good.get("id") != wh.id:
                av = None
                if wh.avatar:
                    try:
                        av = b64.b64encode(await wh.avatar.read()).decode()
                    except Exception:
                        av = None
                st["hook_good"] = {"id": wh.id, "name": wh.name, "channel": wh.channel_id, "avatar": av}
                save_state(st)
                return
            changes = {}
            if wh.name != good["name"]:
                changes["name"] = good["name"]
            if wh.channel_id != good["channel"]:
                ch = client.get_channel(good["channel"]) or await client.fetch_channel(good["channel"])
                changes["channel"] = ch
            if bool(wh.avatar) != bool(good.get("avatar")) and good.get("avatar"):
                changes["avatar"] = b64.b64decode(good["avatar"])
            if changes:
                await wh.edit(reason="TopBar: put the feedback link back the way it was", **changes)
                print("feedback webhook restored:", list(changes))
            return
        # deleted: make a new one and publish it
        ch = client.get_channel(CHANNEL) or await client.fetch_channel(CHANNEL)
        name = (good or {}).get("name") or "TopBar Feedback"
        av = b64.b64decode(good["avatar"]) if good and good.get("avatar") else None
        nw = await ch.create_webhook(name=name, avatar=av, reason="TopBar: the feedback link was deleted")
        body2 = re.sub(r"hookx=[0-9a-fA-F]+", "hookx=" + hook_scramble(nw.url), body)
        data = {"message": "New feedback link", "sha": j["sha"], "content": b64.b64encode(body2.encode("utf-8")).decode()}
        async with s.put(url, json=data) as r:
            ok = r.status in (200, 201)
        st["hook_good"] = {"id": nw.id, "name": name, "channel": nw.channel_id, "avatar": (good or {}).get("avatar")}
        save_state(st)
        _hook_id = nw.id
        try:
            await ch.send("The feedback link was deleted by someone, so I made a new one" + (" and published it. Apps switch to it on their next update check." if ok else ", but couldn't publish it to GitHub."))
        except Exception:
            pass


async def guard_loop():
    import asyncio
    while True:
        try:
            await guard_once()
        except Exception as ex:
            print("webhook guard:", ex)
        await asyncio.sleep(60)


def is_report(msg: discord.Message) -> bool:
    return any(f.name == "Reply code" for e in msg.embeds for f in e.fields)


@client.event
async def on_ready():
    if not getattr(client, "_upd", None):
        client._upd = client.loop.create_task(update_loop())
        client._guard = client.loop.create_task(guard_loop())
    await tree.sync()
    print(f"Logged in as {client.user}")


@client.event
async def on_message(msg: discord.Message):
    # junk sent through the feedback link (not a TopBar report) is removed
    if msg.webhook_id and _hook_id and msg.webhook_id == _hook_id and not is_report(msg):
        try:
            await msg.delete()
        except Exception:
            pass
        return
    # replying to a feedback message in Discord sends that reply to the person
    if msg.author.bot or msg.channel.id != CHANNEL or not msg.reference or not (msg.content.strip() or msg.attachments):
        return
    if not allowed(msg.author, msg.channel.permissions_for(msg.author) if msg.guild else None):
        return await msg.reply("Only the developer can reply to feedback (check OWNER_ID in keys.txt is your Discord user ID).", mention_author=False)
    try:
        src = msg.reference.resolved or await msg.channel.fetch_message(msg.reference.message_id)
    except discord.HTTPException:
        return
    p = parse(src) if isinstance(src, discord.Message) else None
    if not p:
        return await msg.reply("That message has no reply code, so it can't be answered in TopBar.", mention_author=False)
    err = await send_reply(p["code"], msg.content, msg.attachments)   # pictures, videos and files you attach go along
    if not err:
        mark_replied(p["id"])
    if err:
        await msg.reply(f"Couldn't send: {err}", mention_author=False)
    else:
        await msg.add_reaction("\u2705")  # sent; shows up in their TopBar within about 20 seconds


@tree.command(description="Send a reply to someone's TopBar by their reply code (a file or picture can go with it)")
async def reply(inter: discord.Interaction, code: str, message: str, file: discord.Attachment = None):
    if not allowed(inter.user, inter.permissions):
        return await inter.response.send_message("Only the developer can reply.", ephemeral=True)
    await inter.response.defer(ephemeral=True)
    err = await send_reply(code, message, [file] if file else [])
    if not err:
        st = load_state(); st.setdefault("replied_codes", []).append(code.strip("` ").upper()); save_state(st)
    await inter.followup.send(f"Couldn't send: {err}" if err else f"Sent to `{code}`.", ephemeral=True)



# ---------------------------------------------------------------- saved settings and what's been seen / replied
STATE = os.path.join(HERE, "state.json")


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




def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def folder(p):
    kind = re.sub(r"[^A-Za-z]", "", p["kind"])[:12] or "Feedback"
    return f"messages/{p['dt'].strftime('%Y-%m-%d_%H%M')}_{kind}_{p['code']}"


def img_name(p, i, a):
    ext = os.path.splitext(a["filename"])[1].lower() or ".png"
    return f"{folder(p)}/picture-{i+1}{ext}"


def pc_text(p):
    return "\n".join(x for x in (p["windows"], p["pc"]) if x).strip()


def build_txt(fb):
    out = [f"TopBar feedback: {len(fb)} messages", ""]
    for p in fb:
        out += [f"=== {p['when']} | {p['kind']} | {p['who']} ({p['code']}) | TopBar {p['version']}" + (" | replied" if p.get("replied") else "")]
        if p["title"]: out.append("Title: " + p["title"])
        out += [p["full_text"] or "(nothing written)", "", "PC:", pc_text(p) or "(not sent)"]
        if p["images"]: out.append("Pictures: " + ", ".join(img_name(p, i, a) for i, a in enumerate(p["images"])))
        out.append("")
    return "\n".join(out)


def build_html(fb):
    css = ("body{font:15px/1.5 -apple-system,Segoe UI,sans-serif;background:#f5f5f7;color:#1d1d1f;margin:0;padding:32px}"
           ".m{background:#fff;border-radius:16px;padding:20px 24px;margin:0 auto 18px;max-width:900px;box-shadow:0 1px 3px #0001}"
           ".h{color:#6e6e73;font-size:13px}.t{font-weight:600;font-size:17px;margin:4px 0}.b,.pc{white-space:pre-wrap}.pc{color:#6e6e73;font-size:13px;margin-top:10px}"
           "img{max-width:100%;border-radius:10px;margin-top:12px;border:1px solid #0001}")
    h = [f"<!doctype html><meta charset=utf-8><title>TopBar feedback</title><style>{css}</style>", f"<h1 style='max-width:900px;margin:0 auto 20px'>TopBar feedback · {len(fb)} messages</h1>"]
    for p in fb:
        h.append("<div class=m>")
        h.append(f"<div class=h>{esc(p['when'])} · {esc(p['kind'])} from {esc(p['who'])} · <code>{esc(p['code'])}</code> · TopBar {esc(p['version'])} · <a href='{p['link']}'>open in Discord</a></div>")
        if p["title"]: h.append(f"<div class=t>{esc(p['title'])}</div>")
        h.append(f"<div class=b>{esc(p['full_text'])}</div><div class=pc>{esc(pc_text(p))}</div>")
        h += [f"<a href='{img_name(p, i, a)}'><img src='{img_name(p, i, a)}'></a>" for i, a in enumerate(p["images"])]
        h.append("</div>")
    return "\n".join(h)


@tree.command(description="Export all feedback: what people wrote, their PC and their pictures")
async def export(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True, thinking=True)   # answer Discord right away (it gives up after 3 seconds)
    if not allowed(inter.user, inter.permissions):
        return await inter.followup.send("Only the developer can export.", ephemeral=True)
    try:
        st = load_state(); fb = await all_feedback()
        if not fb:
            return await inter.followup.send("There's no feedback yet.", ephemeral=True)
        async with aiohttp.ClientSession() as s:
            for p in fb:   # long messages come as message.txt: use the whole text
                p["replied"] = is_replied(p, st); p["full_text"] = p["message"]
                if p["longtext"]:
                    try:
                        async with s.get(p["longtext"].url) as r: p["full_text"] = (await r.text()).strip()
                    except Exception: pass
            texts = {"feedback.txt": build_txt(fb), "feedback.html": build_html(fb)}
            zips, LIMIT = [], 9_000_000   # under Discord's upload limit; a message is never split between parts
            def new_zip():
                b = io.BytesIO(); z = zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED)
                for n, t in texts.items(): z.writestr(n, t)
                zips.append([b, z, 0]); return zips[-1]
            cur = new_zip(); shots = 0
            for p in fb:
                pics = []
                for i, a in enumerate(p["images"]):
                    try:
                        async with s.get(a["url"]) as r:
                            if r.status == 200: pics.append((img_name(p, i, a), await r.read()))
                    except Exception: pass
                need = sum(len(d) for _, d in pics)
                if cur[2] and cur[2] + need > LIMIT: cur = new_zip()
                for n, d in pics: cur[1].writestr(n, d)
                cur[2] += need; shots += len(pics)
        for b, z, _ in zips: z.close()
        files = [discord.File(io.BytesIO(b.getvalue()), "topbar-feedback.zip" if len(zips) == 1 else f"topbar-feedback-part{i+1}.zip") for i, (b, z, _) in enumerate(zips)]
        info = f"{len(fb)} messages from {len({p['code'] for p in fb})} people, {shots} pictures. Open feedback.html in the zip to read them with their pictures."
        for i in range(0, len(files), 10):
            await inter.followup.send(info if i == 0 else "\u2026more", files=files[i:i + 10], ephemeral=True)
    except Exception as ex:
        print("export failed:", repr(ex))
        await inter.followup.send(f"The export failed: {ex}", ephemeral=True)


@tree.command(name="update", description="Get the newest version of this bot now")
async def update_cmd(inter: discord.Interaction):
    await inter.response.send_message("Checking\u2026 if there's a new version the bot restarts in a few seconds.", ephemeral=True)
    await self_update(force=True)


client.run(TOKEN)
