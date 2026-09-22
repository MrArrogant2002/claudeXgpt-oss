#!/usr/bin/env bash
# One-shot setup for the Local Code Agent on Ubuntu/Debian with an NVIDIA GPU.
#
#   bash scripts/setup.sh              # system deps + venv + python deps + tokenizer vocab
#   bash scripts/setup.sh --llama      # ALSO clone + build llama.cpp with CUDA
#   bash scripts/setup.sh --model      # ALSO download the gpt-oss-20b GGUF (~13 GB) via HF
#   bash scripts/setup.sh --llama --model   # everything
#
# Safe to re-run. Heavy/optional steps (llama build, model download) are opt-in.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DO_LLAMA=0; DO_MODEL=0
for a in "$@"; do
  case "$a" in
    --llama) DO_LLAMA=1 ;;
    --model) DO_MODEL=1 ;;
    *) echo "unknown flag: $a"; exit 2 ;;
  esac
done

say() { printf "\n\033[1;36m== %s ==\033[0m\n" "$1"; }

# --- 0. sanity: GPU + CUDA toolchain ---------------------------------------
say "checking GPU + CUDA"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || true
else
  echo "!! nvidia-smi not found — is the NVIDIA driver installed?"
fi
if command -v nvcc >/dev/null 2>&1; then
  nvcc --version | grep -i release || true
else
  echo "!! nvcc (CUDA toolkit) not found. Needed to BUILD llama.cpp with CUDA."
  echo "   Install it with:  sudo apt install -y nvidia-cuda-toolkit"
  echo "   (or the NVIDIA CUDA apt repo for the newest toolkit)."
fi

# --- 1. system packages -----------------------------------------------------
say "installing system packages (sudo)"
sudo apt-get update
sudo apt-get install -y \
  git cmake build-essential ccache pkg-config \
  python3 python3-venv python3-pip \
  ripgrep curl libcurl4-openssl-dev

# --- 2. python venv + deps --------------------------------------------------
say "creating .venv and installing python deps"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
[ -f requirements-ui.txt ] && pip install -r requirements-ui.txt || true

# --- 3. tokenizer vocab (offline, one-time) --------------------------------
say "fetching the Harmony tokenizer vocab (o200k_base)"
VOCAB="vendor/tiktoken/o200k_base.tiktoken"
EXPECT_SHA="446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
mkdir -p vendor/tiktoken
if [ -f "$VOCAB" ] && [ "$(sha256sum "$VOCAB" | cut -d' ' -f1)" = "$EXPECT_SHA" ]; then
  echo "vocab already present and valid."
else
  curl -L -o "$VOCAB" \
    https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken
  GOT="$(sha256sum "$VOCAB" | cut -d' ' -f1)"
  if [ "$GOT" != "$EXPECT_SHA" ]; then
    echo "!! vocab sha256 mismatch ($GOT). Delete $VOCAB and retry."; exit 1
  fi
  echo "vocab downloaded + verified."
fi

# --- 4. (optional) build llama.cpp with CUDA -------------------------------
if [ "$DO_LLAMA" = "1" ]; then
  say "building llama.cpp (CUDA)"
  if ! command -v nvcc >/dev/null 2>&1; then
    echo "!! nvcc missing — install the CUDA toolkit first (see above). Skipping build."
  else
    mkdir -p "$HOME/src"
    if [ ! -d "$HOME/src/llama.cpp" ]; then
      git clone https://github.com/ggml-org/llama.cpp "$HOME/src/llama.cpp"
    else
      git -C "$HOME/src/llama.cpp" pull --ff-only || true
    fi
    cmake -S "$HOME/src/llama.cpp" -B "$HOME/src/llama.cpp/build" -DGGML_CUDA=ON
    cmake --build "$HOME/src/llama.cpp/build" --config Release -j"$(nproc)"
    echo "llama-server -> $HOME/src/llama.cpp/build/bin/llama-server"
  fi
fi

# --- 5. (optional) download the model --------------------------------------
if [ "$DO_MODEL" = "1" ]; then
  say "downloading gpt-oss-20b GGUF (~13 GB) via huggingface_hub"
  pip install -U "huggingface_hub[cli]"
  mkdir -p "$HOME/models/gpt-oss-20b"
  huggingface-cli download ggml-org/gpt-oss-20b-GGUF \
    --include "*.gguf" --local-dir "$HOME/models/gpt-oss-20b"
  echo "model files in $HOME/models/gpt-oss-20b:"
  ls -lh "$HOME/models/gpt-oss-20b"/*.gguf || true
fi

# --- done -------------------------------------------------------------------
say "next steps"
cat <<'EOF'
1) (if not done) build llama.cpp:   bash scripts/setup.sh --llama
2) (if not done) get the model:     bash scripts/setup.sh --model
3) start the server (in tmux so it survives a remote-desktop disconnect):
     tmux new -s llama
     ~/src/llama.cpp/build/bin/llama-server \
       -m ~/models/gpt-oss-20b/<FILE>.gguf --host 127.0.0.1 --port 8081 \
       -ngl 999 -c 65536 -fa on -ctk q8_0 -ctv q8_0
   (Ctrl-b then d to detach; `tmux attach -t llama` to return)
4) run the agent (new terminal):
     source .venv/bin/activate
     python tui.py --project ./demo-project --allow-exec --allow-edit
EOF
