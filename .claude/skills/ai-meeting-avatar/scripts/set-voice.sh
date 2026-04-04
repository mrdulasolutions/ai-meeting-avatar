#!/bin/bash
# Set the TTS voice in config.yaml and prefs
# Usage: set-voice.sh <voice-id>
set -euo pipefail

VOICE="${1:?Usage: set-voice.sh <voice-id>}"
PROJECT=~/Desktop/ai-meeting-avatar
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT"

# Update config.yaml
sed -i '' "s/^  voice: .*/  voice: \"$VOICE\"/" config.yaml
echo "config.yaml voice set to $VOICE"

# Update lang if British voice
case "$VOICE" in
  bf_*|bm_*) sed -i '' "s/^  lang: .*/  lang: \"en-gb\"/" config.yaml; echo "lang set to en-gb" ;;
  af_*|am_*) sed -i '' "s/^  lang: .*/  lang: \"en-us\"/" config.yaml; echo "lang set to en-us" ;;
esac

# Save to prefs
bash "$SKILL_DIR/scripts/save-pref.sh" voice "$VOICE"
