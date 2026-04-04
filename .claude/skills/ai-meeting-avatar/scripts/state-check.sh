#!/bin/bash
# State check for AI Meeting Avatar skill
# Runs on every skill invocation to determine system readiness
set -euo pipefail

PROJECT=~/Desktop/ai-meeting-avatar

# Project exists?
if [ ! -d "$PROJECT" ]; then
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
  BACKEND=$(grep -m1 '^\s*backend:' config.yaml | sed 's/.*backend:\s*"\?\([^"]*\)"\?.*/\1/' | tr -d '[:space:]')
  VOICE=$(grep -m1 '^\s*voice:' config.yaml | sed 's/.*voice:\s*"\?\([^"]*\)"\?.*/\1/' | tr -d '[:space:]')
  echo "BACKEND=$BACKEND"
  echo "CONFIG_VOICE=$VOICE"
else
  echo "CONFIG=missing"
fi

# Prefs
python3 - <<'PYEOF'
import json
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
