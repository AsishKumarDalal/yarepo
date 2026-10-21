"""
Always-on OpenAI-compatible LLM endpoint on Kaggle using llama-server (llama.cpp)
+ speculative decoding + Cloudflare tunnel.
Needs: Internet ON, GPU T4 x2. Run via Save Version -> Save & Run All (Commit).

llama-server source order (first that works wins):
  1. cached build from a Dataset added as Input   (instant)
  2. third-party prebuilt Linux CUDA build         (~1 min)
  3. compile from source for T4                    (slow, 30-60 min)
     -> if this happens the script saves the binary to /kaggle/working/llama_bin and EXITS,
        so the commit finishes and you can save the output as a Dataset (add it as Input next time).

Spec modes tried in order: dflash (draft model) -> ngram (no draft) -> off.
"""
import os, re, sys, glob, shutil, time, socket, signal, subprocess, requests

# ---------- config ----------
EXIT_AFTER_COMPILE = True       # if we had to compile, save the binary and exit (see above)
TARGET_REPO = "unsloth/Qwen3.8-27B-GGUF"
TARGET_FILE = "Qwen3.8-27B-UD-Q4_K_M.gguf"
DRAFT_REPO = "incoai/Qwen3.8-27B-DFlash2-GGUF"   # mirror: z-lab/Qwen3.8-27B-DFlash2-GGUF
DRAFT_QUANT = "Q4_K_M"
MODEL_ALIAS = "qwen3.8-27b"
N_CTX = 8192                    # lower to 4096 if OOM
PORT = 8000
LOCAL = f"http://127.0.0.1:{PORT}"
OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
CACHE_DIR = f"{OUT}/llama_bin"
MODEL_DIR = "/tmp/models"
LLAMA_DIR = "/tmp/llama.cpp"
STARTUP_TIMEOUT = 15 * 60
SPEC_MODES = ["dflash", "ngram", "off"]


def sh(cmd, check=True):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, check=check)


def lib_env(binary):
    d = os.path.dirname(binary)
    paths = [d, os.path.join(os.path.dirname(d), "lib"), os.path.join(d, "lib")]
    return {**os.environ, "LD_LIBRARY_PATH": ":".join(paths) + ":" + os.environ.get("LD_LIBRARY_PATH", "")}


def binary_works(path):
    try:
        return subprocess.run([path, "--version"], env=lib_env(path), capture_output=True).returncode == 0
    except Exception:
        return False


# ---------- 1. install ----------
sh(f"{sys.executable} -m pip install -q huggingface_hub")
sh("wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/"
   "cloudflared-linux-amd64 -O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared")
subprocess.run("nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv", shell=True)


# ---------- 2. get llama-server: cache -> prebuilt -> compile ----------
def use_cached():
    """Binary from a Dataset added as Input (read-only), copied to a writable dir."""
    for src in glob.glob("/kaggle/input/**/llama-server", recursive=True):
        dst_dir = f"{LLAMA_DIR}/cached"
        shutil.rmtree(dst_dir, ignore_errors=True)
        shutil.copytree(os.path.dirname(src), dst_dir)
        dst = f"{dst_dir}/llama-server"
        os.chmod(dst, 0o755)
        if binary_works(dst):
            print("using cached build from", src, flush=True)
            return dst
        print("cached binary found but not runnable:", src, flush=True)
    return None


def try_prebuilt():
    """Third-party prebuilt Linux CUDA builds (official llama.cpp has none). Falls back to compiling."""
    sources = [
        ("keypaa/llamaup", lambda n: re.search(r"sm[_-]?75|t4|[-_.]75[-_.]", n) and n.endswith((".tar.gz", ".tgz", ".zip"))),
        ("ai-dock/llama.cpp-cuda", lambda n: "cuda-12" in n and "amd64" in n and n.endswith(".tar.gz")),
    ]
    for repo, ok in sources:
        try:
            rels = requests.get(f"https://api.github.com/repos/{repo}/releases?per_page=5", timeout=30).json()
            for rel in rels:
                for a in rel.get("assets", []):
                    n = a["name"].lower()
                    if not ok(n):
                        continue
                    print("trying prebuilt:", repo, a["name"], flush=True)
                    dst = f"{LLAMA_DIR}/prebuilt_{repo.split('/')[0]}"
                    shutil.rmtree(dst, ignore_errors=True)
                    os.makedirs(dst)
                    arc = "/tmp/llama_pre.archive"
                    sh(f"wget -q '{a['browser_download_url']}' -O {arc}", check=False)
                    sh(f"tar -xf {arc} -C {dst} || unzip -q -o {arc} -d {dst}", check=False)
                    for hit in glob.glob(f"{dst}/**/llama-server", recursive=True):
                        os.chmod(hit, 0o755)
                        r = subprocess.run([hit, "--list-devices"], env=lib_env(hit), capture_output=True, text=True)
                        if r.returncode == 0 and "cuda" in (r.stdout + r.stderr).lower():
                            print("prebuilt OK:", hit, flush=True)
                            return hit
                        print("prebuilt not usable:", (r.stdout + r.stderr)[-300:], flush=True)
        except Exception as e:
            print("prebuilt source failed:", repo, repr(e), flush=True)
    return None


def cuda_cmake_args():
    """Help CMake find nvcc + libcuda on Kaggle (fixes: CUDA::cuda_driver not found)."""
    nvcc = shutil.which("nvcc") or next(iter(glob.glob("/usr/local/cuda*/bin/nvcc")), None)
    if not nvcc:
        raise RuntimeError("nvcc not found, paste output of: which nvcc; ls /usr/local | grep cuda")
    root = os.path.dirname(os.path.dirname(os.path.realpath(nvcc)))
    args = [f"-DCUDAToolkit_ROOT={root}", f"-DCMAKE_CUDA_COMPILER={nvcc}"]
    stubs = glob.glob(f"{root}/**/stubs/libcuda.so", recursive=True)
    if stubs:
        lib_dir = os.path.dirname(stubs[0])
    else:
        real = next(iter(glob.glob("/usr/lib/**/libcuda.so.1", recursive=True)
                         + glob.glob("/usr/local/nvidia/**/libcuda.so.1", recursive=True)), None)
        if not real:
            raise RuntimeError("libcuda not found anywhere")
        lib_dir = "/tmp/cudastub"
        os.makedirs(lib_dir, exist_ok=True)
        link = f"{lib_dir}/libcuda.so"
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(real, link)
    args.append(f"-DCMAKE_LIBRARY_PATH={lib_dir}")
    print("cuda cmake args:", args, flush=True)
    return " ".join(args)


def build_jobs():
    """nvcc uses ~2-3 GB per job. Too many jobs -> swapping -> much slower."""
    cpus = os.cpu_count() or 2
    try:
        mem_kb = int(re.search(r"MemAvailable:\s+(\d+)", open("/proc/meminfo").read()).group(1))
        by_mem = max(1, int(mem_kb / 1024 / 1024 / 3))
    except Exception:
        by_mem = cpus
    return max(1, min(cpus, by_mem))


def build_from_source():
    print("building from source for sm_75...", flush=True)
    sh("apt-get install -y -qq ninja-build", check=False)          # much faster than make
    gen = "-G Ninja" if shutil.which("ninja") else ""
    src = f"{LLAMA_DIR}/src"
    sh(f"rm -rf {src} && git clone --depth 1 https://github.com/ggml-org/llama.cpp {src}")
    sh(f"cmake -S {src} -B {src}/build {gen} -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 "
       f"-DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF "
       f"-DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF {cuda_cmake_args()}")
    sh(f"cmake --build {src}/build --target llama-server -j{build_jobs()}")
    hits = glob.glob(f"{src}/build/**/llama-server", recursive=True)
    if not hits:
        raise RuntimeError("llama-server build failed")
    return hits[0]


def save_cache(binary):
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    shutil.copytree(os.path.dirname(binary), CACHE_DIR)
    print(f"cached build saved to {CACHE_DIR}", flush=True)


def get_llama_server():
    p = use_cached()
    if p:
        return p
    p = try_prebuilt()
    if p:
        return p
    p = build_from_source()
    save_cache(p)
    if EXIT_AFTER_COMPILE:
        print("=" * 60)
        print("Compiled and cached. Wait for this version to finish, open its Output tab,")
        print("click 'New Dataset', add that dataset as Input, then run again (starts in minutes).")
        print("=" * 60, flush=True)
        sys.exit(0)
    return p


LLAMA_SERVER = get_llama_server()
LLAMA_ENV = lib_env(LLAMA_SERVER)
print("llama-server:", LLAMA_SERVER, flush=True)

# ---------- 3. download models ----------
from huggingface_hub import hf_hub_download, list_repo_files
os.makedirs(MODEL_DIR, exist_ok=True)
target_path = hf_hub_download(TARGET_REPO, TARGET_FILE, local_dir=MODEL_DIR)
draft_path = None
try:
    files = [f for f in list_repo_files(DRAFT_REPO) if f.endswith(".gguf") and DRAFT_QUANT.lower() in f.lower()]
    draft_path = hf_hub_download(DRAFT_REPO, files[0], local_dir=MODEL_DIR)
except Exception as e:
    print("draft model unavailable, dflash mode will be skipped:", repr(e), flush=True)
print("target:", target_path, "| draft:", draft_path, flush=True)

# ---------- 4. helpers ----------
server_proc = None
tunnel_proc = None
public_url = None
spec_mode = None


def port_open():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=3):
            return True
    except OSError:
        return False


def server_ready():
    """Startup only. /health is 200 when the model is loaded, 503 while loading."""
    try:
        return requests.get(f"{LOCAL}/health", timeout=5).status_code == 200
    except Exception:
        return False


def dump_diag(n=50):
    print("---- last llama_server.log lines ----", flush=True)
    try:
        print("\n".join(open(f"{OUT}/llama_server.log", errors="ignore").read().splitlines()[-n:]), flush=True)
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
    subprocess.run(["pkill", "-9", "-x", "llama-server"], check=False)
    for _ in range(60):   # wait until VRAM is released
        try:
            out = subprocess.check_output("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits",
                                          shell=True, text=True)
            if max(int(x) for x in out.split()) < 1500:
                break
        except Exception:
            break
        time.sleep(1)


def build_cmd(mode):
    cmd = [LLAMA_SERVER, "-m", target_path,
           "--alias", MODEL_ALIAS, "--host", "0.0.0.0", "--port", str(PORT),
           "-c", str(N_CTX), "-ngl", "99", "-fa", "on",
           "--split-mode", "layer", "--tensor-split", "1,1",
           "-np", "1",              # one slot: full context, no splitting across slots
           "--no-mmproj",           # speculative decoding is disabled when a vision projector is loaded
           "--jinja"]               # use the chat template in the GGUF
    if mode == "dflash":
        cmd += ["-md", draft_path, "--spec-type", "draft-dflash", "--spec-draft-n-max", "7"]
    elif mode == "ngram":
        cmd += ["--spec-type", "ngram-mod"]   # no draft model, helps on repetitive/code output
    return cmd


def start_server(mode):
    global server_proc
    stop_server()
    cmd = build_cmd(mode)
    print("$", " ".join(cmd), flush=True)
    log = open(f"{OUT}/llama_server.log", "a")
    server_proc = subprocess.Popen(cmd, env=LLAMA_ENV, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < STARTUP_TIMEOUT:
        if server_proc.poll() is not None:
            dump_diag()
            raise RuntimeError(f"server exited during startup (mode={mode})")
        if server_ready():
            return
        time.sleep(2)
    dump_diag()
    raise RuntimeError(f"server failed to start in time (mode={mode})")


def start_with_fallback():
    """Try each spec mode until one starts. Remember the winner for watchdog restarts."""
    global spec_mode
    for mode in ([spec_mode] if spec_mode else SPEC_MODES):
        if mode == "dflash" and not draft_path:
            continue
        try:
            start_server(mode)
            spec_mode = mode
            print(time.strftime("%H:%M:%S"), "running with spec mode:", mode, flush=True)
            return
        except Exception as e:
            print("mode", mode, "failed:", repr(e), flush=True)
    raise RuntimeError("all spec modes failed, check llama_server.log")


def warm_model():
    r = requests.post(f"{LOCAL}/v1/chat/completions",
                      json={"model": MODEL_ALIAS, "max_tokens": 8,
                            "messages": [{"role": "user", "content": "hi"}]},
                      timeout=900)
    r.raise_for_status()


def start_tunnel():
    global tunnel_proc, public_url
    if tunnel_proc and tunnel_proc.poll() is None:
        tunnel_proc.kill()
    logpath = f"{OUT}/cloudflared.log"
    open(logpath, "w").close()
    tunnel_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", LOCAL, "--no-autoupdate"],
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
    print("ENDPOINT READY  (spec mode:", spec_mode, ")")
    print("base_url :", public_url + "/v1")
    print("api_key  : anything (e.g. 'sk-local')")
    print("model    :", MODEL_ALIAS)
    print("=" * 60)
    print(f'''
from openai import OpenAI
client = OpenAI(base_url="{public_url}/v1", api_key="sk-local", timeout=600)
r = client.chat.completions.create(model="{MODEL_ALIAS}", messages=[{{"role":"user","content":"Hello"}}],
    extra_body={{"chat_template_kwargs": {{"enable_thinking": False}}}})   # thinking off = faster, may hurt hard reasoning
print(r.choices[0].message.content)
''', flush=True)


# ---------- 5. start everything ----------
start_with_fallback()
warm_model()
start_tunnel()
print_usage()

# ---------- 6. watchdog ----------
tick = 0
port_fails = 0
while True:
    time.sleep(30)
    tick += 1
    try:
        if server_proc.poll() is not None:
            print(time.strftime("%H:%M:%S"), f"server EXITED (code {server_proc.returncode}), restarting", flush=True)
            dump_diag()
            start_with_fallback()
            warm_model()
            port_fails = 0
        elif not port_open():
            port_fails += 1
            print(time.strftime("%H:%M:%S"), f"port closed ({port_fails}/6)", flush=True)
            if port_fails >= 6:
                dump_diag()
                start_with_fallback()
                warm_model()
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
        print(time.strftime("%H:%M:%S"), "server", "UP" if port_open() else "DOWN", "|", public_url, flush=True)
