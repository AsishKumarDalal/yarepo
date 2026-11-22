#!/usr/bin/env python3
"""
Serve VS Code (code-server) from a Kaggle notebook over the web.
Runs in the BACKGROUND - cell returns immediately, other cells keep working.

Stack:  code-server  ->  cloudflared (public https URL)

No password: code-server runs with --auth none.

Usage in Kaggle:
  1. Notebook Settings -> Internet = ON
  2. Paste in one cell and run (or: !python kaggle_vscode_web.py)
  3. Open the printed trycloudflare.com URL
  4. Stop later with:  !python kaggle_vscode_web.py stop
     (or call stop() if you pasted this file into a cell)

This script only stops the processes it started itself (tracked by PID files).
It never kills other ttyd/cloudflared tunnels that may already be running.
"""

import json
import os
import platform
import re
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

# ----------------------------- config ---------------------------------
PORT = int(os.environ.get("VSCODE_PORT", "8080"))
WORKDIR = os.environ.get("VSCODE_WORKDIR", "/kaggle/working")
BIN_DIR = os.path.expanduser("~/bin")
STATE_DIR = os.environ.get("VSCODE_STATE_DIR", "/tmp/vscode_web")
LOG_DIR = STATE_DIR
USER_DATA_DIR = os.path.expanduser("~/.local/share/code-server")

# Empty = look up the latest release; set e.g. CODE_SERVER_VERSION=4.146.0 to pin.
CODE_SERVER_VERSION = os.environ.get("CODE_SERVER_VERSION", "")
PINNED_CODE_SERVER_VERSION = "4.146.0"

CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
)

os.makedirs(BIN_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(USER_DATA_DIR, exist_ok=True)
os.environ["PATH"] = f"{BIN_DIR}:{os.environ.get('PATH', '')}"


# ----------------------------- helpers --------------------------------
def _download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"[skip] {dest} already exists")
        return
    print(f"[download] {url}")
    tmp = dest + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, dest)


def _arch():
    machine = platform.machine().lower()
    return "arm64" if machine in ("aarch64", "arm64") else "amd64"


def _latest_code_server_version():
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/coder/code-server/releases/latest",
            headers={"User-Agent": "kaggle-vscode-web"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            tag = json.load(resp)["tag_name"]
        return tag.lstrip("v")
    except Exception as exc:
        print(f"[warn] could not look up latest code-server: {exc}")
        return PINNED_CODE_SERVER_VERSION


def install_code_server():
    """Download + extract code-server if needed. Returns the executable path."""
    bin_path = f"{BIN_DIR}/code-server"
    if os.path.exists(bin_path):
        print(f"[skip] {bin_path} already exists")
        return bin_path

    version = CODE_SERVER_VERSION or _latest_code_server_version()
    arch = _arch()
    name = f"code-server-{version}-linux-{arch}"
    url = (
        f"https://github.com/coder/code-server/releases/download/"
        f"v{version}/{name}.tar.gz"
    )
    archive = f"{BIN_DIR}/{name}.tar.gz"

    _download(url, archive)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(BIN_DIR, filter="data")
        except TypeError:  # Python < 3.12
            tar.extractall(BIN_DIR)

    extracted = f"{BIN_DIR}/{name}/bin/code-server"
    os.chmod(extracted, os.stat(extracted).st_mode | stat.S_IEXEC)
    if os.path.lexists(bin_path):
        os.remove(bin_path)
    os.symlink(extracted, bin_path)
    print(f"[installed] code-server {version} -> {bin_path}")
    return bin_path


def install_cloudflared():
    dest = f"{BIN_DIR}/cloudflared"
    if os.path.exists(dest):
        print(f"[skip] {dest} already exists")
        return dest
    _download(CLOUDFLARED_URL, dest)
    os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC)
    return dest


def _pid_path(name):
    return os.path.join(STATE_DIR, f"{name}.pid")


def _read_pid(name):
    try:
        with open(_pid_path(name)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _write_pid(name, pid):
    with open(_pid_path(name), "w") as f:
        f.write(str(pid))


def _remove_pid(name):
    try:
        os.remove(_pid_path(name))
    except OSError:
        pass


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _pid_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode(errors="replace")
    except OSError:
        return ""


def _terminate(pid):
    """Terminate a process and its children, then force-kill if needed."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return

    try:
        if pgid == pid:
            os.killpg(pgid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.time() + 5
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.2)

    try:
        if pgid == pid:
            os.killpg(pgid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop(verbose=True):
    """Stop only the code-server + cloudflared started by this script."""
    stopped = []
    for name, needle in (("cloudflared", "cloudflared"), ("code-server", "code-server")):
        pid = _read_pid(name)
        if _pid_alive(pid) and needle in _pid_cmdline(pid):
            _terminate(pid)
            stopped.append(f"{name} (pid {pid})")
        _remove_pid(name)

    try:
        os.remove(os.path.join(STATE_DIR, "url.txt"))
    except OSError:
        pass

    if verbose:
        print("stopped: " + ", ".join(stopped) if stopped else "nothing to stop")


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _wait_for_port(port, proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def _tail(path, lines=20):
    try:
        with open(path, errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


def start_code_server():
    cmd = [
        f"{BIN_DIR}/code-server",
        "--bind-addr", f"127.0.0.1:{PORT}",
        "--auth", "none",                      # no password
        "--disable-telemetry",
        "--disable-update-check",
        "--disable-workspace-trust",
        "--user-data-dir", USER_DATA_DIR,
        WORKDIR,
    ]
    log_path = f"{LOG_DIR}/code-server.log"
    log = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        cwd=WORKDIR,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,                # detach from notebook process group
    )
    log.close()
    _write_pid("code-server", proc.pid)
    return proc, log_path


def start_tunnel():
    log_path = f"{LOG_DIR}/cloudflared.log"
    log = open(log_path, "w")
    cmd = [
        f"{BIN_DIR}/cloudflared", "tunnel",
        "--url", f"http://localhost:{PORT}",
        "--no-autoupdate",
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    _write_pid("cloudflared", proc.pid)

    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        with open(log_path, errors="replace") as f:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", f.read())
        if match:
            url = match.group(0)
            with open(os.path.join(STATE_DIR, "url.txt"), "w") as f:
                f.write(url + "\n")
            return proc, url
        time.sleep(0.5)

    _terminate(proc.pid)
    _remove_pid("cloudflared")
    sys.exit(f"Failed to get a trycloudflare URL. See {log_path}\n{_tail(log_path)}")


def _verify(url, attempts=6, delay=2):
    """Check the public URL; new trycloudflare hostnames need a moment for DNS."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kaggle-vscode-web"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(delay)
    print(f"[warn] URL check failed: {last_error}")
    return None


def main():
    os.makedirs(WORKDIR, exist_ok=True)

    # Only clears this script's own previous instance, never other tunnels.
    stop(verbose=False)

    if not _port_free(PORT):
        sys.exit(
            f"Port {PORT} is already in use by something this script did not start. "
            f"Set VSCODE_PORT to a free port, e.g. VSCODE_PORT=8081."
        )

    install_code_server()
    install_cloudflared()

    server_proc, server_log = start_code_server()
    if not _wait_for_port(PORT, server_proc, timeout=30):
        stop(verbose=False)
        sys.exit(f"code-server failed to start. See {server_log}\n{_tail(server_log)}")

    tunnel_proc, url = start_tunnel()
    status = _verify(url)

    print("\n" + "=" * 64)
    print(f" VS Code  : {url}")
    print(f" Folder   : {WORKDIR}")
    print(f" Password : none")
    print(f" HTTP     : {status if status is not None else 'unverified'}")
    print("=" * 64)
    print("Running in background. Logs in", LOG_DIR)
    print("Stop with: !python kaggle_vscode_web.py stop")
    print("WARNING: anyone with this URL can access the files and terminal.")
    print()


if __name__ == "__main__":
    command = sys.argv[1].lower() if len(sys.argv) > 1 else "start"
    if command == "stop":
        stop()
    elif command == "status":
        server_pid = _read_pid("code-server")
        tunnel_pid = _read_pid("cloudflared")
        url_path = os.path.join(STATE_DIR, "url.txt")
        url = open(url_path).read().strip() if os.path.exists(url_path) else "(none)"
        print(f"code-server: {'running pid ' + str(server_pid) if _pid_alive(server_pid) else 'stopped'}")
        print(f"cloudflared: {'running pid ' + str(tunnel_pid) if _pid_alive(tunnel_pid) else 'stopped'}")
        print(f"URL: {url}")
    else:
        main()
