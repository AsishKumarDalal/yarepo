"""
Run OpenCode in a Kaggle notebook and serve the terminal over the web.

Stack:  opencode (CLI/TUI)  ->  ttyd (terminal over websocket)  ->  cloudflared (public https URL)

Usage in Kaggle:
  1. Notebook Settings -> Internet = ON
  2. Paste this whole file in one cell and run it (or: !python kaggle_opencode_web.py)
  3. Open the printed trycloudflare.com URL and log in with the printed user/password
  4. Keep the cell running - stopping it kills the tunnel
"""

import os
import re
import secrets
import shutil
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

TTYD_URL = "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.x86_64"
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
)

os.makedirs(BIN_DIR, exist_ok=True)
OPENCODE_BIN_DIR = os.path.expanduser("~/.opencode/bin")
os.environ["PATH"] = f"{BIN_DIR}:{OPENCODE_BIN_DIR}:{os.environ['PATH']}"


# ----------------------------- helpers --------------------------------
def sh(cmd, check=True):
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, check=check)


def download(url, dest):
    if os.path.exists(dest):
        print(f"[skip] {dest} already exists")
        return
    print(f"[download] {url}")
    urllib.request.urlretrieve(url, dest)
    os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC)


# ----------------------------- 1. opencode ----------------------------
def install_opencode():
    if shutil.which("opencode"):
        print("[skip] opencode already installed")
        return
    # Official installer
    r = sh("curl -fsSL https://opencode.ai/install | bash", check=False)
    if shutil.which("opencode"):
        return
    # Fallback: npm
    print("[info] installer failed, trying npm...")
    sh("npm install -g opencode-ai", check=False)
    if not shutil.which("opencode"):
        sys.exit("Could not install opencode. Is Internet enabled in Kaggle settings?")


# ----------------------------- 2. ttyd + cloudflared ------------------
def install_tools():
    download(TTYD_URL, f"{BIN_DIR}/ttyd")
    download(CLOUDFLARED_URL, f"{BIN_DIR}/cloudflared")


# ----------------------------- 3. run ---------------------------------
def start_ttyd():
    # Starts bash, launches opencode, and drops back to bash if you quit it.
    inner = "opencode; exec bash"
    cmd = [
        f"{BIN_DIR}/ttyd",
        "-W",                                  # writable (allow input)
        "-p", str(PORT),
        "-c", f"{USERNAME}:{PASSWORD}",        # basic auth - tunnel URL is PUBLIC
        "-t", "fontSize=15",
        "bash", "-lc", inner,
    ]
    return subprocess.Popen(cmd, cwd=WORKDIR, env=os.environ.copy(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_tunnel():
    cmd = [
        f"{BIN_DIR}/cloudflared", "tunnel",
        "--url", f"http://localhost:{PORT}",
        "--no-autoupdate",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = None
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.2)
            continue
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
            break
    if not url:
        proc.terminate()
        sys.exit("Failed to get a trycloudflare URL.")
    return proc, url


def main():
    os.makedirs(WORKDIR, exist_ok=True)
    install_opencode()
    install_tools()

    ttyd_proc = start_ttyd()
    time.sleep(2)
    if ttyd_proc.poll() is not None:
        sys.exit("ttyd failed to start.")

    tunnel_proc, url = start_tunnel()

    print("\n" + "=" * 60)
    print(f" URL      : {url}")
    print(f" Username : {USERNAME}")
    print(f" Password : {PASSWORD}")
    print("=" * 60)
    print("Tip: run `opencode auth login` in the web terminal to add your provider key,")
    print("     or export e.g. ANTHROPIC_API_KEY before running this script.")
    print("Keep this cell running. Press Stop to shut everything down.\n")

    try:
        while True:
            time.sleep(30)
            if ttyd_proc.poll() is not None or tunnel_proc.poll() is not None:
                print("A process exited, shutting down.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        for p in (tunnel_proc, ttyd_proc):
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
