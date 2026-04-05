"""
User preferences — persisted to ~/.config/ai-meeting-avatar/prefs.json.

Saves first-run answers (voice, avatar photo, last room) so the user
never has to set up again across Claude Code sessions.
"""

from __future__ import annotations

import json
from pathlib import Path

PREFS_DIR = Path.home() / ".config" / "ai-meeting-avatar"
PREFS_FILE = PREFS_DIR / "prefs.json"

_DEFAULTS: dict = {
    "setup_complete": False,
    "voice": "af_heart",
    "avatar_photo": None,
    "last_room": None,
    "livekit_url": "ws://localhost:7880",
    "livekit_api_key": "devkey",
    "livekit_api_secret": "secret",
}


def load() -> dict:
    """Return saved prefs, merged with defaults for any missing keys."""
    if not PREFS_FILE.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(PREFS_FILE.read_text())
        return {**_DEFAULTS, **data}
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)


def save(prefs: dict) -> None:
    """Persist prefs dict to disk, creating the directory if needed."""
    PREFS_DIR.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))


def get(key: str):
    """Read a single preference value."""
    return load().get(key, _DEFAULTS.get(key))


def set(key: str, value) -> None:
    """Write a single preference value."""
    prefs = load()
    prefs[key] = value
    save(prefs)


def is_setup_complete() -> bool:
    """True if the user has completed first-run setup."""
    return bool(get("setup_complete"))


def models_downloaded() -> bool:
    """True if both the Gemma model file and Kokoro ONNX model exist on disk."""
    project_root = Path(__file__).parent.parent.parent.parent
    gemma_ok = any(project_root.glob("models/gemma-4-e2b/*.litertlm"))
    kokoro_ok = (project_root / "models" / "kokoro" / "kokoro-v1.0.onnx").exists()
    return gemma_ok and kokoro_ok


def needs_first_run() -> bool:
    """True when the user should be walked through the onboarding questions."""
    return not is_setup_complete() or not models_downloaded()
