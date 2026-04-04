#!/bin/bash
# Launch the avatar agent in the background
cd /Users/mrdulasolutions/Desktop/ai-meeting-avatar
source .venv/bin/activate

# Kill anything on port 8081 first
lsof -ti :8081 | xargs kill -9 2>/dev/null
sleep 1

# Launch the agent
export $(cat .env | xargs)
ai-avatar join "$1" > /tmp/ai-avatar.log 2>&1 &
AVATAR_PID=$!
echo "$AVATAR_PID" > /tmp/ai-avatar.pid
sleep 5

# Check if it's still running
if kill -0 "$AVATAR_PID" 2>/dev/null; then
    echo "AVATAR_RUNNING:$AVATAR_PID"
else
    echo "AVATAR_FAILED"
    cat /tmp/ai-avatar.log | head -30
fi
