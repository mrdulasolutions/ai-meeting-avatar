#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_models.sh — Download all required models for ai-meeting-avatar
#
# Run once after installing Python dependencies:
#   chmod +x scripts/setup_models.sh
#   ./scripts/setup_models.sh
#
# What this script does:
#   1. Pull Ollama LLM (llama3.2 default)
#   2. Cache Coqui XTTS-v2 via a one-shot Python call
#   3. Clone SadTalker + download its checkpoints   (phase 2, optional)
#   4. Clone LivePortrait                             (phase 2, optional)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MODELS_DIR="$PROJECT_DIR/models"

green()  { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }
red()    { echo -e "\033[31m$*\033[0m"; }

mkdir -p "$MODELS_DIR"

# ── 1. Ollama ─────────────────────────────────────────────────────────────────
green "==> [1/4] Ollama LLM"

# Read model from config.yaml if yq is available, else default
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.2}"
if command -v yq &>/dev/null; then
  OLLAMA_MODEL="$(yq '.llm.model' "$PROJECT_DIR/config.yaml" 2>/dev/null || echo "$OLLAMA_MODEL")"
fi

if ! command -v ollama &>/dev/null; then
  yellow "  Ollama CLI not found. Install from https://ollama.com then run:"
  yellow "  ollama pull $OLLAMA_MODEL"
else
  echo "  Pulling model: $OLLAMA_MODEL (may take several minutes) …"
  ollama pull "$OLLAMA_MODEL"
  green "  Done: $OLLAMA_MODEL"
fi

# ── 2. Coqui XTTS-v2 ─────────────────────────────────────────────────────────
green "==> [2/4] Coqui XTTS-v2"
echo "  Coqui TTS downloads models to ~/.local/share/tts on first use."
echo "  Running a quick synthesis to trigger the download (~1.8 GB) …"

python3 - <<'PYEOF'
import os, sys
os.environ["COQUI_TOS_AGREED"] = "1"
try:
    from TTS.api import TTS
    _ = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
    print("  XTTS-v2 model cached.")
except Exception as e:
    print(f"  WARNING: Could not pre-cache XTTS-v2: {e}")
    print("  It will be downloaded on first ai-avatar run instead.")
PYEOF

# ── 3. SadTalker (Phase 2 — optional) ────────────────────────────────────────
green "==> [3/4] SadTalker (avatar rendering — Phase 2)"

SADTALKER_DIR="$MODELS_DIR/SadTalker"
if [ -d "$SADTALKER_DIR" ]; then
  yellow "  SadTalker already cloned at $SADTALKER_DIR — skipping."
else
  if command -v git &>/dev/null; then
    echo "  Cloning SadTalker …"
    git clone --depth 1 https://github.com/OpenTalker/SadTalker.git "$SADTALKER_DIR"

    echo "  Downloading SadTalker checkpoints …"
    cd "$SADTALKER_DIR"

    # Official checkpoint download script
    if [ -f "scripts/download_models.sh" ]; then
      bash scripts/download_models.sh
    else
      # Fallback: manual wget
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
    yellow "  git not found — skipping SadTalker. Install git and re-run."
  fi
fi

# ── 4. LivePortrait (Phase 2 — optional) ─────────────────────────────────────
green "==> [4/4] LivePortrait (avatar rendering — alternative)"

LIVEPORTRAIT_DIR="$MODELS_DIR/LivePortrait"
if [ -d "$LIVEPORTRAIT_DIR" ]; then
  yellow "  LivePortrait already cloned at $LIVEPORTRAIT_DIR — skipping."
else
  if command -v git &>/dev/null; then
    echo "  Cloning LivePortrait …"
    git clone --depth 1 https://github.com/KwaiVGI/LivePortrait.git "$LIVEPORTRAIT_DIR" || \
      yellow "  LivePortrait clone failed (non-fatal). You can clone it manually."
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
echo " Next steps:"
echo "   1. Add a 6-30 s voice sample WAV:"
echo "      cp /path/to/voice.wav assets/voice_samples/speaker.wav"
echo ""
echo "   2. (Optional) Add avatar photo:"
echo "      cp /path/to/photo.jpg assets/avatar.jpg"
echo ""
echo "   3. Start LiveKit server (local dev):"
echo "      docker run --rm -p 7880:7880 -p 7881:7881 livekit/livekit-server --dev"
echo ""
echo "   4. Join a meeting:"
echo "      ai-avatar join my-room"
echo ""
echo "   5. Test the pipeline without LiveKit:"
echo "      ai-avatar test-pipeline"
