#!/bin/bash
# Save a key-value pair to avatar prefs
# Usage: save-pref.sh <key> <value>
set -euo pipefail

KEY="${1:?Usage: save-pref.sh <key> <value>}"
VALUE="${2:?Usage: save-pref.sh <key> <value>}"

python3 - "$KEY" "$VALUE" <<'PYEOF'
import json, sys
from pathlib import Path

key, value = sys.argv[1], sys.argv[2]
# Convert string booleans
if value.lower() == "true":
    value = True
elif value.lower() == "false":
    value = False

prefs_dir = Path.home() / ".config" / "ai-meeting-avatar"
prefs_dir.mkdir(parents=True, exist_ok=True)
prefs_file = prefs_dir / "prefs.json"

prefs = {}
if prefs_file.exists():
    try:
        prefs = json.loads(prefs_file.read_text())
    except Exception:
        pass

prefs[key] = value
prefs_file.write_text(json.dumps(prefs, indent=2))
print(f"Saved {key}={value}")
PYEOF
