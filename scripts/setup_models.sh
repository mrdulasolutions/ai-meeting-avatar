#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_models.sh — Download all required models for ai-meeting-avatar
#
# Run once after installing Python dependencies:
#   chmod +x scripts/setup_models.sh
#   ./scripts/setup_models.sh
#
# What this script does:
#   1. Authenticate with Hugging Face (needed for gated Gemma 4 models)
#   2. Download Gemma 4 E2B weights via huggingface-hub  (~2.6 GB)
#   3. Cache Coqui XTTS-v2 via a one-shot Python call    (~1.8 GB)
#   4. Clone SadTalker + download its checkpoints         (phase 2, optional)
#   5. Clone LivePortrait                                  (phase 2, optional)
#
# Prerequisites:
#   pip install -e .          (installs huggingface-hub CLI among other deps)
#   A Hugging Face account with Gemma 4 model access approved at:
#   https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MODELS_DIR="$PROJECT_DIR/models"

green()  { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }
red()    { echo -e "\033[31m$*\033[0m"; }

mkdir -p "$MODELS_DIR"

# ── 1. Hugging Face authentication ────────────────────────────────────────────
green "==> [1/5] Hugging Face authentication"
echo "  Gemma 4 is a gated model — you need a HF account with access granted."
echo "  Apply for access at:"
echo "  https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm"
echo ""

if ! command -v huggingface-cli &>/dev/null; then
  echo "  huggingface-cli not found. Installing …"
  pip install -q huggingface-hub
fi

# Check if already logged in
if huggingface-cli whoami &>/dev/null 2>&1; then
  HF_USER="$(huggingface-cli whoami 2>/dev/null | head -1 || echo 'logged in')"
  green "  Already authenticated as: $HF_USER"
else
  echo "  Logging in to Hugging Face …"
  echo "  (You'll need your HF token from https://huggingface.co/settings/tokens)"
  huggingface-cli login
fi

# ── 2. Gemma 4 E2B model weights ──────────────────────────────────────────────
green "==> [2/5] Gemma 4 E2B model (LiteRT-LM format)"

E2B_DIR="$MODELS_DIR/gemma-4-e2b"
E2B_REPO="litert-community/gemma-4-E2B-it-litert-lm"

# Allow user to pick E4B instead via env var
if [ "${GEMMA_VARIANT:-e2b}" = "e4b" ]; then
  E2B_DIR="$MODELS_DIR/gemma-4-e4b"
  E2B_REPO="litert-community/gemma-4-E4B-it-litert-lm"
  green "  Using E4B variant (larger, higher quality)"
fi

if ls "$E2B_DIR"/*.litertlm &>/dev/null 2>&1; then
  yellow "  Model already downloaded at $E2B_DIR — skipping."
  yellow "  Delete $E2B_DIR to re-download."
else
  mkdir -p "$E2B_DIR"
  echo "  Downloading from: $E2B_REPO"
  echo "  Destination: $E2B_DIR"
  echo "  Size: ~2.6 GB (E2B) — this will take a few minutes …"
  echo ""

  huggingface-cli download \
    "$E2B_REPO" \
    --local-dir "$E2B_DIR" \
    --local-dir-use-symlinks False \
    --include "*.litertlm" "*.json" "*.md"

  # Verify at least one .litertlm file landed
  if ls "$E2B_DIR"/*.litertlm &>/dev/null 2>&1; then
    LITERTLM_FILE="$(ls "$E2B_DIR"/*.litertlm | head -1)"
    SIZE="$(du -sh "$LITERTLM_FILE" 2>/dev/null | cut -f1)"
    green "  Downloaded: $(basename "$LITERTLM_FILE") ($SIZE)"
  else
    red "  ERROR: No .litertlm file found in $E2B_DIR"
    echo "  Check your HF token has access to the gated model."
    echo "  Apply at: https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm"
    exit 1
  fi
fi

# ── Write resolved model path to .env so config.py picks it up ───────────────
LITERTLM_FILE="$(ls "$E2B_DIR"/*.litertlm | head -1)"
ENV_FILE="$PROJECT_DIR/.env"

if grep -q "GEMMA_MODEL_PATH" "$ENV_FILE" 2>/dev/null; then
  # Update existing entry
  sed -i.bak "s|^GEMMA_MODEL_PATH=.*|GEMMA_MODEL_PATH=$LITERTLM_FILE|" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
else
  echo "GEMMA_MODEL_PATH=$LITERTLM_FILE" >> "$ENV_FILE"
fi
green "  Model path written to .env: GEMMA_MODEL_PATH=$LITERTLM_FILE"

# Also update config.yaml model_path if yq is available
if command -v yq &>/dev/null; then
  yq -i ".llm.model_path = \"$LITERTLM_FILE\"" "$PROJECT_DIR/config.yaml"
  green "  config.yaml llm.model_path updated."
else
  yellow "  yq not found — update config.yaml llm.model_path manually if needed:"
  yellow "  model_path: \"$LITERTLM_FILE\""
fi

# ── 3. Kokoro TTS models ──────────────────────────────────────────────────────
green "==> [3/5] Kokoro TTS models (~80 MB)"
echo "  Downloading kokoro-v1.0.onnx and voices-v1.0.bin …"

KOKORO_DIR="$MODELS_DIR/kokoro"
mkdir -p "$KOKORO_DIR"

KOKORO_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

for fname in "kokoro-v1.0.onnx" "voices-v1.0.bin"; do
  dest="$KOKORO_DIR/$fname"
  if [ -f "$dest" ]; then
    yellow "  $fname already present — skipping."
  else
    echo "  Downloading $fname …"
    if command -v curl &>/dev/null; then
      curl -L --progress-bar -o "$dest" "$KOKORO_BASE/$fname"
    elif command -v wget &>/dev/null; then
      wget -q --show-progress -O "$dest" "$KOKORO_BASE/$fname"
    else
      python3 -c "
import urllib.request, sys
url = '$KOKORO_BASE/$fname'
dest = '$dest'
print(f'  Fetching {url} …')
urllib.request.urlretrieve(url, dest)
print(f'  Saved to {dest}')
"
    fi
    green "  Downloaded: $fname ($(du -sh "$dest" | cut -f1))"
  fi
done

green "  Kokoro TTS ready."

# ── 4. SadTalker (Phase 2 — optional) ────────────────────────────────────────
green "==> [4/5] SadTalker (avatar rendering — Phase 2)"

SADTALKER_DIR="$MODELS_DIR/SadTalker"
if [ -d "$SADTALKER_DIR" ]; then
  yellow "  SadTalker already cloned at $SADTALKER_DIR — skipping."
else
  if command -v git &>/dev/null; then
    echo "  Cloning SadTalker …"
    git clone --depth 1 https://github.com/OpenTalker/SadTalker.git "$SADTALKER_DIR"

    echo "  Downloading SadTalker checkpoints …"
    cd "$SADTALKER_DIR"

    if [ -f "scripts/download_models.sh" ]; then
      bash scripts/download_models.sh
    else
      CKPT_DIR="$SADTALKER_DIR/checkpoints"
      GFPGAN_DIR="$SADTALKER_DIR/gfpgan/weights"
      mkdir -p "$CKPT_DIR" "$GFPGAN_DIR"

      BASE_URL="https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc"
      for f in SadTalker_V0.0.2_256.safetensors mapping_00109-model.pth.tar mapping_00229-model.pth.tar; do
        wget -q --show-progress -O "$CKPT_DIR/$f" "$BASE_URL/$f" || true
      done

      GFPGAN_URL="https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0"
      wget -q --show-progress -O "$GFPGAN_DIR/GFPGANv1.4.pth" "$GFPGAN_URL/GFPGANv1.4.pth" || true
    fi

    green "  SadTalker ready."
    cd "$PROJECT_DIR"
  else
    yellow "  git not found — skipping SadTalker."
  fi
fi

# ── 5. LivePortrait (Phase 2 — optional) ─────────────────────────────────────
green "==> [5/5] LivePortrait (avatar rendering — alternative)"

LIVEPORTRAIT_DIR="$MODELS_DIR/LivePortrait"
if [ -d "$LIVEPORTRAIT_DIR" ]; then
  yellow "  LivePortrait already cloned at $LIVEPORTRAIT_DIR — skipping."
else
  if command -v git &>/dev/null; then
    echo "  Cloning LivePortrait …"
    git clone --depth 1 https://github.com/KwaiVGI/LivePortrait.git "$LIVEPORTRAIT_DIR" || \
      yellow "  LivePortrait clone failed (non-fatal). Clone manually if needed."
    green "  LivePortrait cloned."
  else
    yellow "  git not found — skipping LivePortrait."
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
green "════════════════════════════════════════════"
green " Model setup complete!"
green "════════════════════════════════════════════"
echo ""
echo " Gemma 4 model: $LITERTLM_FILE"
echo ""
echo " Next steps:"
echo "   1. (Optional) Add avatar photo for Phase 2:"
echo "      cp /path/to/photo.jpg assets/avatar.jpg"
echo ""
echo "   2. Start LiveKit server (local dev):"
echo "      docker run --rm -p 7880:7880 -p 7881:7881 \\"
echo "        -e LIVEKIT_KEYS=\"devkey: secret\" \\"
echo "        livekit/livekit-server --dev"
echo ""
echo "   3. Join a meeting:"
echo "      ai-avatar join my-room"
echo ""
echo "   4. Test the pipeline without LiveKit:"
echo "      ai-avatar test-pipeline"
echo ""
echo " To download the larger E4B model instead:"
echo "   GEMMA_VARIANT=e4b ./scripts/setup_models.sh"
echo ""
echo " To change the TTS voice, edit config.yaml:"
echo "   tts.voice: af_heart   (default, warm female)"
echo "   tts.voice: am_adam    (natural male)"
echo "   tts.voice: bf_emma    (British female)"
