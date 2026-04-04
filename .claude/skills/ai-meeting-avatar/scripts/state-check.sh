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

# ── Find compatible Python (3.11–3.13, kokoro-onnx doesn't support 3.14+) ──
COMPATIBLE_PYTHON=""
for candidate in python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1; then
    COMPATIBLE_PYTHON="$candidate"
    break
  fi
done

# Fall back to python3 if it's in the right range
if [ -z "$COMPATIBLE_PYTHON" ] && command -v python3 >/dev/null 2>&1; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
  PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
  if [ "$PY_MINOR" -ge 11 ] && [ "$PY_MINOR" -le 13 ] 2>/dev/null; then
    COMPATIBLE_PYTHON="python3"
  fi
fi

if [ -n "$COMPATIBLE_PYTHON" ]; then
  PY_FULL=$($COMPATIBLE_PYTHON --version 2>&1 | awk '{print $2}')
  echo "PYTHON=$COMPATIBLE_PYTHON ($PY_FULL)"
else
  # Check if they have python3 but wrong version
  if command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "unknown")
    echo "PYTHON=incompatible ($PY_VER — need 3.11–3.13)"
  else
    echo "PYTHON=missing"
  fi
fi

# Venv?
if [ -f ".venv/bin/activate" ]; then
  VENV_PY=$(.venv/bin/python --version 2>&1 | awk '{print $2}' || echo "unknown")
  VENV_MINOR=$(echo "$VENV_PY" | cut -d. -f2)
  if [ "$VENV_MINOR" -ge 11 ] && [ "$VENV_MINOR" -le 13 ] 2>/dev/null; then
    echo "VENV=ok ($VENV_PY)"
  else
    echo "VENV=wrong_python ($VENV_PY — need 3.11–3.13)"
  fi
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

# Prefs (use venv python if available, fall back to compatible python)
PYTHON_CMD="python3"
if [ -f ".venv/bin/python" ]; then
  PYTHON_CMD=".venv/bin/python"
elif [ -n "$COMPATIBLE_PYTHON" ]; then
  PYTHON_CMD="$COMPATIBLE_PYTHON"
fi

$PYTHON_CMD - <<'PYEOF'
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
