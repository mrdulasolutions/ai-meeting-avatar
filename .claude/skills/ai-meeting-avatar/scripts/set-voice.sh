#!/bin/bash
# Set the TTS voice through the CLI and prefs
# Usage: set-voice.sh <voice-id>
set -euo pipefail

VOICE="${1:?Usage: set-voice.sh <voice-id>}"
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="$(cd "$SKILL_DIR/../../.." && pwd)"

cd "$PROJECT"

if [ ! -x ".venv/bin/ai-avatar" ] && [ ! -x ".venv/bin/python" ]; then
  echo "Error: virtual environment not found in $PROJECT"
  exit 1
fi

source .venv/bin/activate
ai-avatar voice --set "$VOICE"

# Save to prefs
bash "$SKILL_DIR/scripts/save-pref.sh" voice "$VOICE"
