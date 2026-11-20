"""
Serve a full bash terminal from a Kaggle notebook over the web.
Runs in the BACKGROUND - cell returns immediately, other cells keep working.

Stack:  bash  ->  ttyd (terminal over websocket)  ->  cloudflared (public https URL)

Usage in Kaggle:
  1. Notebook Settings -> Internet = ON
  2. Paste in one cell and run (or: !python kaggle_terminal_web.py)
  3. Open the printed trycloudflare.com URL, log in with printed user/password
  4. Stop later with:  stop()   (or: !pkill ttyd; pkill cloudflared)
"""

import os
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.request

# ----------------------------- config ---------------------------------
PORT = 7681
USERNAME = "kaggle"
PASSWORD = os.environ.get("TTYD_PASSWORD") or secrets.token_urlsafe(12)
WORKDIR = "/kaggle/working"
BIN_DIR = os.path.expanduser("~/bin")
LOG_DIR = "/tmp/terminal_web"

TTYD_URL = "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.x86_64"
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
)

os.makedirs(BIN_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.environ["PATH"] = f"{BIN_DIR}:{os.path.expanduser('~/.opencode/bin')}:{os.environ['PATH']}"


# ----------------------------- helpers --------------------------------
def download(url, dest):
    if os.path.exists(dest):
        print(f"[skip] {dest} already exists")
        return
    print(f"[download] {url}")
    urllib.request.urlretrieve(url, dest)
    os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC)


def stop():
    """Kill background ttyd + cloudflared."""
    subprocess.run("pkill -f '/ttyd' ; pkill -f '/cloudflared'", shell=True)
    print("stopped")


def install_tools():
    download(TTYD_URL, f"{BIN_DIR}/ttyd")
    download(CLOUDFLARED_URL, f"{BIN_DIR}/cloudflared")


# ----------------------------- run (detached) -------------------------
def start_ttyd():
    cmd = [
        f"{BIN_DIR}/ttyd",
        "-W",                               # writable: allow typing input
        "-p", str(PORT),
        "-c", f"{USERNAME}:{PASSWORD}",     # basic auth
        "-t", "fontSize=15",
        "-t", "scrollback=10000",
        "bash", "-l",                       # plain login bash shell
    ]
    log = open(f"{LOG_DIR}/ttyd.log", "w")
    return subprocess.Popen(
        cmd, cwd=WORKDIR, env=os.environ.copy(),
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,  # detach from notebook process group
    )


def start_tunnel():
    log_path = f"{LOG_DIR}/cloudflared.log"
    log = open(log_path, "w")
    cmd = [
        f"{BIN_DIR}/cloudflared", "tunnel",
        "--url", f"http://localhost:{PORT}",
        "--no-autoupdate",
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        with open(log_path) as f:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", f.read())
        if m:
            return proc, m.group(0)
        time.sleep(0.5)
    proc.terminate()
    sys.exit(f"Failed to get a trycloudflare URL. See {log_path}")


def main():
    os.makedirs(WORKDIR, exist_ok=True)
    stop()  # clear any previous run so the port is free
    install_tools()

    ttyd_proc = start_ttyd()
    time.sleep(2)
    if ttyd_proc.poll() is not None:
        sys.exit(f"ttyd failed to start. See {LOG_DIR}/ttyd.log")

    tunnel_proc, url = start_tunnel()

    print("\n" + "=" * 60)
    print(f" URL      : {url}")
    print(f" Username : {USERNAME}")
    print(f" Password : {PASSWORD}")
    print("=" * 60)
    print("Running in background. Logs in", LOG_DIR)
    print("Stop with: stop()  (or !pkill ttyd; pkill cloudflared)\n")


if __name__ == "__main__":
    main()
