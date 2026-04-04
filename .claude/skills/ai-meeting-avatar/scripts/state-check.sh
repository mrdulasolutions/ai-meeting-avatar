#!/bin/bash
# State check for AI Meeting Avatar skill
# Runs on every skill invocation to determine system readiness
set -euo pipefail

# Derive project root from skill dir (scripts/ → skill/ → skills/ → .claude/ → project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Project exists and has pyproject.toml?
if [ ! -f "$PROJECT/pyproject.toml" ]; then
  echo "PROJECT=missing"
  exit 0
fi
cd "$PROJECT"

# Venv?
if [ -f ".venv/bin/activate" ]; then
  echo "VENV=ok"
else
  echo "VENV=missing"
fi

# Config backend
if [ -f "config.yaml" ]; then
  BACKEND=$(grep -m1 '^\s*backend:' config.yaml | sed 's/^[^:]*:\s*//' | tr -d '"[:space:]')
  VOICE=$(grep -m1 '^\s*voice:' config.yaml | sed 's/^[^:]*:\s*//' | tr -d '"[:space:]')
  AVATAR_ENABLED=$(grep -m1 '^\s*enabled:' config.yaml | sed 's/^[^:]*:\s*//' | tr -d '"[:space:]')
  echo "BACKEND=$BACKEND"
  echo "CONFIG_VOICE=$VOICE"
  echo "AVATAR_ENABLED=${AVATAR_ENABLED:-false}"
else
  echo "CONFIG=missing"
fi

# Prefs (use venv python if available, fall back to system python3)
PYTHON="python3"
if [ -f ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

$PYTHON - <<'PYEOF'
import json, os
from pathlib import Path

prefs_file = Path.home() / ".config" / "ai-meeting-avatar" / "prefs.json"
prefs = {}
if prefs_file.exists():
    try:
        prefs = json.loads(prefs_file.read_text())
    except Exception:
        pass

print(f"SETUP_COMPLETE={prefs.get('setup_complete', False)}")
print(f"PREF_VOICE={prefs.get('voice', 'af_heart')}")
print(f"LAST_ROOM={prefs.get('last_room', '')}")

# Check for ANTHROPIC_API_KEY in .env or environment
env_file = Path(".env")
has_key = bool(os.getenv("ANTHROPIC_API_KEY", ""))
if not has_key and env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY=") and len(line.split("=", 1)[1].strip()) > 0:
            has_key = True
            break
print(f"ANTHROPIC_KEY={'ok' if has_key else 'missing'}")
PYEOF

# Models
if ls "$PROJECT/models/gemma-4-e2b/"*.litertlm >/dev/null 2>&1; then
  echo "GEMMA_MODEL=ok"
else
  echo "GEMMA_MODEL=missing"
fi

if [ -f "$PROJECT/models/kokoro/kokoro-v1.0.onnx" ]; then
  echo "KOKORO_MODEL=ok"
else
  echo "KOKORO_MODEL=missing"
fi

# Docker
if command -v docker >/dev/null 2>&1; then
  echo "DOCKER=ok"
else
  echo "DOCKER=missing"
fi

# LiveKit
if curl -s --max-time 2 http://localhost:7880 > /dev/null 2>&1; then
  echo "LIVEKIT=running"
else
  echo "LIVEKIT=stopped"
fi

# Avatar process
if pgrep -f "ai-avatar join" > /dev/null 2>&1; then
  ROOM=$(pgrep -a -f "ai-avatar join" 2>/dev/null | grep -o 'join [^ ]*' | awk '{print $2}' || echo "unknown")
  echo "AVATAR=running:$ROOM"
else
  echo "AVATAR=stopped"
fi
