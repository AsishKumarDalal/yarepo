"""
Always-on OpenAI-compatible LLM endpoint on Kaggle using Ollama
for K2 Horizon MoVA 36B A4B (MoE, 36B total / 4B active) + quantized KV cache + Cloudflare tunnel.
Needs: Internet ON, GPU T4 x2. Run via Save Version -> Save & Run All (Commit).

No compiling needed: Ollama ships its own CUDA runtime, so startup is just install + pull (~21 GB download).

IMPORTANT: this model is brand new. Ollama must know its architecture.
The installer fetches the latest Ollama. If loading fails with "unknown model architecture",
the script stops and says so (try OLLAMA_VERSION = a newer/pre-release tag, or wait for support).

Context: tries the largest value first and restarts Ollama with the next smaller one if the model
does not fit ENTIRELY on the GPUs (Ollama silently offloads to CPU otherwise, which is very slow,
so the script treats a partial offload as a failure).

Endpoint:  <public_url>/v1   (OpenAI-compatible)     model name = MODEL_ALIAS
"""
import os, re, sys, time, socket, signal, shutil, subprocess, requests

# ---------- config ----------
OLLAMA_VERSION = ""             # "" = latest. Or e.g. "0.13.0" (needs to be a real release tag)

# Model source. Option A: set OLLAMA_MODEL to a ready Ollama name (e.g. from ollama.com/library).
# Option B: leave it empty and the script pulls a GGUF from Hugging Face via hf.co/<repo>:<quant>.
OLLAMA_MODEL = ""               # e.g. "k2-horizon-mova:36b"
HF_REPO = ""                    # e.g. "unsloth/K2-Horizon-MoVA-36B-A4B-GGUF", "" = auto-search Hugging Face
HF_QUANT = "Q4_K_M"             # ~21 GB for 36B. Try "Q4_K_S" / "IQ4_XS" / "Q3_K_M" if VRAM is tight
SEARCH_TERMS = ["K2-Horizon-MoVA-36B-A4B", "K2-Horizon-MoVA", "MoVA-36B-A4B"]
MODEL_ALIAS = "k2-horizon-mova-36b-a4b"   # the name your clients use

# Context sizes to try, largest first. If a size does not fit fully on the GPUs, the next one is tried.
CTX_CANDIDATES = [50000, 32768, 20000, 16384, 8192]
# CTX_CANDIDATES = [20000, 16384, 12288, 8192]          # <- quicker, safer start

# KV cache quantization: "f16" (most VRAM), "q8_0" (about half, near lossless), "q4_0" (about a quarter)
KV_CACHE_TYPE = "q8_0"
MIN_GPU_FRACTION = 0.98         # require at least this share of the model in VRAM, else treat as failed

PORT = 11434
LOCAL = f"http://127.0.0.1:{PORT}"
OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
MODELS_DIR = "/tmp/ollama_models"
LOG_PATH = f"{OUT}/ollama_server.log"
STARTUP_TIMEOUT = 20 * 60       # first load of a 21 GB model can be slow


class ArchUnsupported(Exception):
    pass


def sh(cmd, check=True, env=None):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, check=check, env=env)


# ---------- 1. install ----------
sh(f"{sys.executable} -m pip install -q huggingface_hub requests")
sh("apt-get update -qq && apt-get install -y -qq zstd curl pciutils lshw", check=False)   # installer needs zstd
ver = f"OLLAMA_VERSION={OLLAMA_VERSION} " if OLLAMA_VERSION else ""
sh(f"curl -fsSL https://ollama.com/install.sh | {ver}sh", check=False)    # systemd warnings are fine on Kaggle
if not shutil.which("ollama"):
    raise RuntimeError("ollama install failed, check the output above (is Internet ON?)")
sh("ollama --version", check=False)
sh("wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/"
   "cloudflared-linux-amd64 -O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared")
subprocess.run("nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv", shell=True)
os.makedirs(MODELS_DIR, exist_ok=True)


# ---------- 2. resolve which model to pull ----------
def resolve_model_ref():
    if OLLAMA_MODEL:
        return OLLAMA_MODEL
    from huggingface_hub import HfApi, list_repo_files
    api = HfApi()
    repos = [HF_REPO] if HF_REPO else []
    if not repos:
        seen = set()
        for term in SEARCH_TERMS:
            try:
                for m in api.list_models(search=term, limit=50):
                    if m.id not in seen and "gguf" in m.id.lower():
                        seen.add(m.id)
                        repos.append(m.id)
            except Exception as e:
                print("search failed for", term, repr(e), flush=True)
        print("GGUF repos found:", repos, flush=True)
    for repo in repos:
        try:
            files = list_repo_files(repo)
        except Exception as e:
            print("cannot list", repo, repr(e), flush=True)
            continue
        if any(f.endswith(".gguf") and HF_QUANT.lower() in f.lower() and "mmproj" not in f.lower() for f in files):
            return f"hf.co/{repo}:{HF_QUANT}"
    raise RuntimeError(
        f"No GGUF repo with quant '{HF_QUANT}' found. Set HF_REPO (or OLLAMA_MODEL) at the top of the script.")


# ---------- 3. ollama server helpers ----------
server_proc = None
tunnel_proc = None
public_url = None
active_ctx = None


def serve_env(ctx):
    return {**os.environ,
            "OLLAMA_HOST": f"0.0.0.0:{PORT}",
            "OLLAMA_MODELS": MODELS_DIR,
            "OLLAMA_CONTEXT_LENGTH": str(ctx),      # default context for every request (clients can't set it via /v1)
            "OLLAMA_FLASH_ATTENTION": "1",          # required for a quantized KV cache
            "OLLAMA_KV_CACHE_TYPE": KV_CACHE_TYPE,
            "OLLAMA_NUM_PARALLEL": "1",             # one slot: full context for one request
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_KEEP_ALIVE": "-1",              # never unload the model
            "OLLAMA_SCHED_SPREAD": "1",             # spread layers over both T4s instead of filling one first
            "OLLAMA_ORIGINS": "*"}


def api_up():
    try:
        return requests.get(LOCAL, timeout=3).status_code == 200
    except Exception:
        return False


def port_open():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=3):
            return True
    except OSError:
        return False


def log_since(offset):
    try:
        with open(LOG_PATH, errors="ignore") as f:
            f.seek(offset)
            return f.read()
    except Exception:
        return ""


def dump_diag(n=60):
    print("---- last ollama_server.log lines ----", flush=True)
    try:
        print("\n".join(open(LOG_PATH, errors="ignore").read().splitlines()[-n:]), flush=True)
    except Exception as e:
        print("no log:", e, flush=True)
    subprocess.run("nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader", shell=True)
    subprocess.run("free -m | head -2; df -h /tmp /kaggle/working 2>/dev/null", shell=True)


def stop_server():
    global server_proc
    if server_proc and server_proc.poll() is None:
        try:
            os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
            server_proc.wait(timeout=20)
        except Exception:
            try:
                os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
                server_proc.wait(timeout=30)
            except Exception:
                pass
    subprocess.run(["pkill", "-9", "-x", "ollama"], check=False)   # also kills leftover runner processes
    for _ in range(60):   # wait until VRAM is released
        try:
            out = subprocess.check_output("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits",
                                          shell=True, text=True)
            if max(int(x) for x in out.split()) < 1500:
                break
        except Exception:
            break
        time.sleep(1)


def start_server(ctx):
    global server_proc
    stop_server()
    env = serve_env(ctx)
    print(f"starting ollama serve (ctx={ctx}, kv={KV_CACHE_TYPE})", flush=True)
    log = open(LOG_PATH, "a")
    server_proc = subprocess.Popen(["ollama", "serve"], env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    for _ in range(90):
        if server_proc.poll() is not None:
            dump_diag()
            raise RuntimeError("ollama serve exited immediately")
        if api_up():
            return
        time.sleep(1)
    dump_diag()
    raise RuntimeError("ollama serve did not come up")


ARCH_ERRORS = ("unknown model architecture", "unsupported architecture", "unknown architecture")


def pull_and_alias(ref):
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{PORT}", "OLLAMA_MODELS": MODELS_DIR}
    sh(f"ollama pull '{ref}'", env=env)
    sh(f"ollama cp '{ref}' {MODEL_ALIAS}", env=env)


def load_and_check(ctx):
    """Load the model with the current server settings and verify it sits fully in VRAM."""
    offset = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    try:
        r = requests.post(f"{LOCAL}/api/generate",
                          json={"model": MODEL_ALIAS, "prompt": "hi", "stream": False, "keep_alive": -1,
                                "options": {"num_predict": 8}},
                          timeout=STARTUP_TIMEOUT)
    except Exception as e:
        text = (log_since(offset) + repr(e)).lower()
        if any(k in text for k in ARCH_ERRORS):
            raise ArchUnsupported(text[-300:])
        raise
    if r.status_code != 200:
        text = (r.text + log_since(offset)).lower()
        if any(k in text for k in ARCH_ERRORS):
            raise ArchUnsupported(text[-300:])
        raise RuntimeError(f"load failed (HTTP {r.status_code}): {r.text[:300]}")
    models = requests.get(f"{LOCAL}/api/ps", timeout=10).json().get("models", [])
    m = next((x for x in models if MODEL_ALIAS in x.get("name", "") or MODEL_ALIAS in x.get("model", "")), None)
    if not m:
        raise RuntimeError("model not listed in /api/ps after load")
    size, vram = m.get("size", 0), m.get("size_vram", 0)
    frac = vram / size if size else 0
    print(f"loaded: {size/1e9:.1f} GB total, {vram/1e9:.1f} GB in VRAM ({frac*100:.0f}%), "
          f"context_length={m.get('context_length')}", flush=True)
    if frac < MIN_GPU_FRACTION:
        raise RuntimeError(f"only {frac*100:.0f}% of the model fits in VRAM at ctx={ctx} (CPU offload = slow)")


def start_with_fallback():
    """Restart ollama with each context size until the model loads fully on the GPUs."""
    global active_ctx
    for ctx in ([active_ctx] if active_ctx else CTX_CANDIDATES):
        try:
            start_server(ctx)
            load_and_check(ctx)
            active_ctx = ctx
            print(time.strftime("%H:%M:%S"), f"running: ctx={ctx}, kv={KV_CACHE_TYPE}", flush=True)
            return
        except ArchUnsupported:
            print("=" * 60)
            print("This Ollama version does not know the model's architecture.")
            print("Try OLLAMA_VERSION = a newer (pre-)release, or wait until support lands.")
            print("Check:  https://github.com/ollama/ollama/releases")
            print("=" * 60, flush=True)
            raise
        except Exception as e:
            print(f"ctx={ctx} failed:", repr(e), flush=True)
            dump_diag()
    raise RuntimeError("all context sizes failed, check ollama_server.log")


def start_tunnel():
    global tunnel_proc, public_url
    if tunnel_proc and tunnel_proc.poll() is None:
        tunnel_proc.kill()
    logpath = f"{OUT}/cloudflared.log"
    open(logpath, "w").close()
    # Ollama rejects unknown Host headers, so present it as localhost
    tunnel_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", LOCAL, "--http-host-header", f"localhost:{PORT}", "--no-autoupdate"],
        stdout=open(logpath, "a"), stderr=subprocess.STDOUT)
    for _ in range(60):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", open(logpath).read())
        if m:
            public_url = m.group(0)
            return
        time.sleep(1)
    raise RuntimeError(f"tunnel failed, check {logpath}")


def print_usage():
    print("=" * 60)
    print(f"ENDPOINT READY  (ollama | context: {active_ctx} | kv cache: {KV_CACHE_TYPE})")
    print("base_url :", public_url + "/v1")
    print("api_key  : anything (e.g. 'ollama')")
    print("model    :", MODEL_ALIAS)
    print("=" * 60)
    print(f'''
from openai import OpenAI
client = OpenAI(base_url="{public_url}/v1", api_key="ollama", timeout=600)
r = client.chat.completions.create(model="{MODEL_ALIAS}", messages=[{{"role":"user","content":"Hello"}}],
    max_tokens=4096)   # prompt + output must stay under {active_ctx}; this is a reasoning model, leave room
print(r.choices[0].message.content)
# optional: reasoning_effort="none" can switch thinking off on recent Ollama versions
''', flush=True)


# ---------- 4. start everything ----------
model_ref = resolve_model_ref()
print("model to pull:", model_ref, flush=True)
start_server(CTX_CANDIDATES[0])          # need a running server to pull into
pull_and_alias(model_ref)
start_with_fallback()
start_tunnel()
print_usage()

# ---------- 5. watchdog ----------
tick = 0
port_fails = 0
while True:
    time.sleep(30)
    tick += 1
    try:
        if server_proc.poll() is not None:
            print(time.strftime("%H:%M:%S"), f"ollama EXITED (code {server_proc.returncode}), restarting", flush=True)
            dump_diag()
            start_with_fallback()
            port_fails = 0
        elif not port_open():
            port_fails += 1
            print(time.strftime("%H:%M:%S"), f"port closed ({port_fails}/6)", flush=True)
            if port_fails >= 6:
                dump_diag()
                start_with_fallback()
                port_fails = 0
        else:
            port_fails = 0

        if tunnel_proc.poll() is not None:
            print(time.strftime("%H:%M:%S"), "tunnel down, restarting", flush=True)
            start_tunnel()
            print_usage()
    except Exception as e:
        print("watchdog error:", repr(e), flush=True)
    if tick % 10 == 0:
        print(time.strftime("%H:%M:%S"), "ollama", "UP" if port_open() else "DOWN", "|", public_url, flush=True)
