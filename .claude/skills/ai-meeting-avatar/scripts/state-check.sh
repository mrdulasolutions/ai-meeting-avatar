#!/bin/bash
# State check for the installable AI Meeting Avatar skill.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

if [ ! -f "$PROJECT/pyproject.toml" ]; then
  echo "PROJECT=missing"
  exit 0
fi

cd "$PROJECT"
echo "PROJECT=ok"

COMPATIBLE_PYTHON=""
for candidate in python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1; then
    COMPATIBLE_PYTHON="$candidate"
    break
  fi
done

if [ -n "$COMPATIBLE_PYTHON" ]; then
  PY_FULL=$($COMPATIBLE_PYTHON --version 2>&1 | awk '{print $2}')
  echo "PYTHON=$COMPATIBLE_PYTHON ($PY_FULL)"
else
  echo "PYTHON=missing"
fi

if [ -f ".venv/bin/activate" ]; then
  echo "VENV=ok"
else
  echo "VENV=missing"
  exit 0
fi

JSON_OUTPUT="$(source .venv/bin/activate && ai-avatar doctor --json-output)"

python3 - "$JSON_OUTPUT" <<'PYEOF'
import json
import sys

state = json.loads(sys.argv[1])
print(f"BACKEND={state['backend']}")
print(f"VOICE={state['voice']}")
print(f"AVATAR_ENABLED={state['avatar_enabled']}")
print(f"ROOM={state['room']}")
print(f"SETUP_COMPLETE={state['setup_complete']}")
print(f"READY_TO_JOIN={state['ready_to_join']}")
print(f"LIVEKIT_RUNNING={state['livekit_running']}")
print(f"AVATAR_RUNNING={state['avatar_running']}")
print(f"RENDERER_READY={state['renderer_ready']}")
print(f"PHOTO_READY={state['photo_ready']}")
print(f"KOKORO_READY={state['kokoro_ready']}")
print(f"GEMMA_READY={state['gemma_ready']}")
print(f"ANTHROPIC_READY={state['anthropic_key_ready']}")
print(f"CAMERA_OUTPUT={state['camera_output']}")
PYEOF
