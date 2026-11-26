"""Keeps the TopBar bot running: starts bot.py, and if it ever stops (crash, update, lost connection), starts it again
a few seconds later. Runs hidden; start.bat sets it to start with Windows."""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "bot.log")


def log(t):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + t + "\n")
    except Exception:
        pass


fails = 0
while True:
    start = time.time()
    env = dict(os.environ, TOPBAR_RUNNER="1")
    try:
        p = subprocess.run([sys.executable, os.path.join(HERE, "bot.py")], cwd=HERE, env=env)
        code = p.returncode
    except Exception as ex:
        code = -1
        log(f"runner: couldn't start the bot: {ex}")
    if code == 3:                      # it updated itself: start the new version straight away
        fails = 0
        time.sleep(1)
        continue
    fails = fails + 1 if time.time() - start < 60 else 0
    log(f"runner: the bot stopped (code {code}), starting it again")
    time.sleep(min(60, 3 * max(1, fails)))   # quick, but never a tight loop if something is badly wrong
