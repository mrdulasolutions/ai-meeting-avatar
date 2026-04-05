#!/bin/bash
# Launch the avatar agent in the background with a readiness check.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOM="${1:-}"

if [ -z "$ROOM" ]; then
  echo "Usage: ./scripts/launch.sh <room-name>"
  exit 1
fi

cd "$PROJECT_DIR"
source .venv/bin/activate

ai-avatar doctor >/tmp/ai-avatar-doctor.log

nohup ai-avatar join "$ROOM" >/tmp/ai-avatar.log 2>&1 &
AVATAR_PID=$!
echo "$AVATAR_PID" >/tmp/ai-avatar.pid
echo "$ROOM" >/tmp/ai-avatar-room
sleep 3

if kill -0 "$AVATAR_PID" 2>/dev/null; then
  echo "AVATAR_RUNNING:$AVATAR_PID"
else
  echo "AVATAR_FAILED"
  head -30 /tmp/ai-avatar.log || true
fi
