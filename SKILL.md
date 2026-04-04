---
name: ai-meeting-avatar
version: 2.0.0
description: |
  AI Meeting Avatar — join Google Meet or Zoom as a local voice AI agent.
  Whisper STT + Gemma 4 (local, offline) or Claude (cloud) + Kokoro TTS.
  Routes audio to calls via BlackHole virtual mic. Full first-run wizard,
  voice selection, LLM backend switching, room management, status monitoring.
  Trigger: "join my meeting", "set up the avatar", "start the avatar",
  /join, /leave, /status, /voice, /brain, /avatar, /mute, /unmute, /help
allowed-tools:
  - Bash
  - Read
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

# AI Meeting Avatar — Claude Code Skill

## Trigger phrases

Activate when the user says any of:
- "join my meeting" / "join the call" / "attend this call"
- "join [room name]"
- "start the avatar" / "set up the avatar" / "set up my meeting avatar"
- "onboard" / "first time setup"
- Any slash command: `/join`, `/leave`, `/status`, `/voice`, `/brain`, `/avatar`, `/mute`, `/unmute`, `/help`

---

## Project root

All commands run from the project directory with the venv active:

```
PROJECT=~/Desktop/ai-meeting-avatar
```

---

## System state check (run every invocation)

Run this block immediately on every invocation to understand current state:

```bash
PROJECT=~/Desktop/ai-meeting-avatar
cd "$PROJECT" 2>/dev/null || { echo "PROJECT_NOT_FOUND"; exit 0; }

# Check venv
if [ ! -f ".venv/bin/activate" ]; then
  echo "VENV=missing"
else
  echo "VENV=ok"
fi

# Check prefs
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

project = Path.home() / "Desktop" / "ai-meeting-avatar"
gemma_ok = bool(list((project / "models" / "gemma-4-e2b").glob("*.litertlm"))) if (project / "models" / "gemma-4-e2b").exists() else False
kokoro_ok = (project / "models" / "kokoro" / "kokoro-v1.0.onnx").exists()

print(f"SETUP_COMPLETE={prefs.get('setup_complete', False)}")
print(f"VOICE={prefs.get('voice', 'af_heart')}")
print(f"LAST_ROOM={prefs.get('last_room', '')}")
print(f"LIVEKIT_URL={prefs.get('livekit_url', 'ws://localhost:7880')}")
print(f"GEMMA_OK={gemma_ok}")
print(f"KOKORO_OK={kokoro_ok}")
PYEOF

# LiveKit health
if curl -s --max-time 2 http://localhost:7880 > /dev/null 2>&1; then
  echo "LIVEKIT=running"
else
  echo "LIVEKIT=stopped"
fi

# Avatar process
if pgrep -f "ai-avatar join" > /dev/null 2>&1; then
  ROOM=$(pgrep -a -f "ai-avatar join" | grep -o 'join [^ ]*' | awk '{print $2}')
  echo "AVATAR=running:$ROOM"
else
  echo "AVATAR=stopped"
fi

# Current config voice
if [ -f "config.yaml" ]; then
  grep -m1 "^  voice:" config.yaml | sed 's/.*voice: *"\?\([^"]*\)"\?.*/CONFIGVOICE=\1/'
fi
```

### Branch on results:

| Condition | Action |
|-----------|--------|
| `PROJECT_NOT_FOUND` | Tell user: "Can't find ~/Desktop/ai-meeting-avatar. Is the project there?" Stop. |
| `VENV=missing` | Go to **Appendix A — Venv setup**, then re-run state check |
| `SETUP_COMPLETE=False` OR (`GEMMA_OK=False` AND config backend is gemma) | Go to **Step A — First-run onboarding** |
| `SETUP_COMPLETE=True` AND (backend is claude OR `GEMMA_OK=True`) AND `KOKORO_OK=True` | Go to **Step B — Join a call** |

---

## Step A — First-run onboarding

> "Welcome! Let me get you set up — it takes about 5 minutes. I'll ask two questions, then let the setup wizard handle everything."

### Question 1 — LLM backend (choose the brain)

Ask the user:

> "Which AI brain should the avatar use?"
>
> **1. Local Gemma 4** (offline, private, no API key)
>    - Runs entirely on your Mac — nothing leaves the device
>    - Requires a free Hugging Face account with Gemma 4 access
>    - One-time ~2.6 GB download
>
> **2. Claude** (smarter, needs internet + Anthropic API key)
>    - Much more capable reasoning and conversation
>    - Requires an Anthropic API key (pay-per-use, ~$0.01/meeting)
>    - No large model download — works right away

Wait for the user's choice. Apply it:

```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
ai-avatar brain --set gemma   # or: ai-avatar brain --set claude
```

### Question 2 — Voice

Ask the user:

> "What voice should the avatar speak with? Type a number or press Enter for the default."
>
> ```
> 1.  af_heart   — warm American female       (default)
> 2.  af_sky     — bright American female
> 3.  af_nova    — expressive American female
> 4.  af_sarah   — clear American female
> 5.  am_adam    — natural American male
> 6.  am_michael — deep American male
> 7.  bf_emma    — British female
> 8.  bm_george  — British male
> ```

Map choice to voice ID:

| # | Voice ID |
|---|----------|
| 1 / Enter | af_heart |
| 2 | af_sky |
| 3 | af_nova |
| 4 | af_sarah |
| 5 | am_adam |
| 6 | am_michael |
| 7 | bf_emma |
| 8 | bm_george |

Apply the voice to `config.yaml` and save to prefs:

```bash
cd ~/Desktop/ai-meeting-avatar

# Edit config.yaml tts.voice field (sed -i '' for macOS)
VOICE="af_heart"   # ← substitute chosen voice ID
sed -i '' "s/^  voice: .*/  voice: \"$VOICE\"/" config.yaml

# Save to prefs
python3 - <<PYEOF
import json
from pathlib import Path

prefs_dir = Path.home() / ".config" / "ai-meeting-avatar"
prefs_dir.mkdir(parents=True, exist_ok=True)
prefs_file = prefs_dir / "prefs.json"
prefs = {}
if prefs_file.exists():
    try:
        prefs = json.loads(prefs_file.read_text())
    except Exception:
        pass
prefs["voice"] = "$VOICE"
prefs_file.write_text(json.dumps(prefs, indent=2))
print("Voice saved to prefs.")
PYEOF
```

### Run the setup wizard

Tell the user what is about to happen, then run:

```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
ai-avatar onboard
```

The wizard handles every remaining step interactively:

| Step | What happens |
|------|-------------|
| 1 — Dependencies | Checks and auto-installs all Python packages |
| 2 — Choose brain | Confirms/changes the backend (gemma or claude) |
| 3 — LLM setup | Downloads Gemma ~2.6 GB (one-time) OR collects Anthropic API key |
| 4 — Kokoro TTS | Downloads ~80 MB voice models, plays a 2-second voice preview |
| 5 — LiveKit | Starts local media server via Docker (or guides manual start) |
| 6 — Pipeline test | Full STT → LLM → TTS end-to-end test with audio playback |
| 7 — Summary | Status table + exact next command |

**Do not interrupt the wizard.** Let it run to completion. It is interactive and expects user input at several prompts.

### After wizard completes successfully

Mark setup complete in prefs:

```bash
python3 - <<'PYEOF'
import json
from pathlib import Path

prefs_dir = Path.home() / ".config" / "ai-meeting-avatar"
prefs_dir.mkdir(parents=True, exist_ok=True)
prefs_file = prefs_dir / "prefs.json"
prefs = {}
if prefs_file.exists():
    try:
        prefs = json.loads(prefs_file.read_text())
    except Exception:
        pass
prefs["setup_complete"] = True
prefs_file.write_text(json.dumps(prefs, indent=2))
print("Setup marked complete.")
PYEOF
```

Then tell the user:
> "You're all set! Say '/join [room name]' to join a call, or '/status' to check that everything is running."

### If the wizard fails at a step

| Error | Fix before re-running |
|-------|----------------------|
| `kokoro_onnx` not found | `pip install kokoro-onnx onnxruntime` |
| `litert_lm` not found | `pip install litert-lm-nightly` |
| HF `401` on Gemma download | User must apply at `huggingface.co/litert-community/gemma-4-E2B-it-litert-lm` first |
| HF `GatedRepoError` | Same — gated model, requires approved access |
| `anthropic` not found | `pip install -e '.[claude]'` |
| Docker not found | Install Docker Desktop from docker.com, then re-run |
| `sounddevice` not found | `pip install sounddevice` |
| Pipeline test times out | Models are slow to load first time — run `ai-avatar test-pipeline --text 'hello'` manually |

Re-run after fixing: `ai-avatar onboard`

---

## Step B — Join a call

### 1. Ensure LiveKit is running

Check from state block. If `LIVEKIT=stopped`:

**Option A — Local (Docker, same machine only):**
```bash
docker run --rm -d --name livekit-dev \
  -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev --bind 0.0.0.0
sleep 3
curl -s http://localhost:7880 > /dev/null && echo "LiveKit running" || echo "Still starting — try again in 5s"
```

**Option B — LiveKit Cloud (remote access, phone can join from anywhere):**

If the user wants remote access or to connect from a phone, guide them to use LiveKit Cloud:
1. Sign up free at `cloud.livekit.io`
2. Create a project → copy the URL, API key, and API secret from the dashboard
3. Add to `.env` in the project:
   ```
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=APxxxx
   LIVEKIT_API_SECRET=xxxx
   ```
4. Save to prefs:
   ```bash
   python3 - <<PYEOF
   import json
   from pathlib import Path
   prefs_dir = Path.home() / ".config" / "ai-meeting-avatar"
   prefs_dir.mkdir(parents=True, exist_ok=True)
   prefs_file = prefs_dir / "prefs.json"
   prefs = {}
   if prefs_file.exists():
       try:
           prefs = json.loads(prefs_file.read_text())
       except Exception:
           pass
   prefs["livekit_url"] = "wss://your-project.livekit.cloud"
   prefs_file.write_text(json.dumps(prefs, indent=2))
   print("LiveKit URL saved.")
   PYEOF
   ```

### 2. Get the room name

- If user said `/join my-room` → use `my-room`
- If `/join` with no room → ask: "What room name? (e.g. 'standup', 'interview', 'my-room')"

Save room to prefs:

```bash
ROOM="my-room"   # ← substitute actual room name
python3 - <<PYEOF
import json
from pathlib import Path
prefs_dir = Path.home() / ".config" / "ai-meeting-avatar"
prefs_dir.mkdir(parents=True, exist_ok=True)
prefs_file = prefs_dir / "prefs.json"
prefs = {}
if prefs_file.exists():
    try:
        prefs = json.loads(prefs_file.read_text())
    except Exception:
        pass
prefs["last_room"] = "$ROOM"
prefs_file.write_text(json.dumps(prefs, indent=2))
PYEOF
```

### 3. Join the room

```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
ai-avatar join "$ROOM"
```

The process runs in the foreground. To run it in the background instead:

```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
nohup ai-avatar join "$ROOM" > /tmp/ai-avatar.log 2>&1 &
echo "Avatar PID: $!"
```

Tell the user:
> "Avatar is joining room '$ROOM'. It will listen for speech, think with [brain], and speak back with the [voice] voice."
>
> "If you're using Google Meet or Zoom, see Step C below to route audio through BlackHole."

---

## Step C — Route audio to Google Meet or Zoom

After the avatar is in the LiveKit room, route its audio output to the meeting:

### Install BlackHole (free virtual audio cable)

```bash
brew install blackhole-2ch
```

Or download from `existential.audio/blackhole`.

### macOS audio routing (one-time setup)

1. Open **Audio MIDI Setup** (`/Applications/Utilities/Audio MIDI Setup.app`)
2. Click **+** (bottom-left) → **Create Multi-Output Device**
3. Check both **BlackHole 2ch** and your speakers/headphones
4. Name it "Avatar + Speakers"
5. Open **System Settings → Sound → Output** → select **Avatar + Speakers**
   (You'll still hear everything, AND BlackHole captures it)

### In Google Meet / Zoom

- Google Meet: Settings (gear icon) → **Microphone** → select **BlackHole 2ch**
- Zoom: Settings → **Audio** → **Microphone** → select **BlackHole 2ch**

Audio flow: `LiveKit room → your Mac speakers → BlackHole 2ch → Meet/Zoom mic`

Tell the user:
> "In Google Meet, go to Settings → Microphone and pick 'BlackHole 2ch'. The avatar's voice will come through as your mic."

---

## Slash command handlers

### `/join [room]`

Follow Step B above. If avatar is already running, ask:

> "Avatar is already running in room '[room]'. Leave that room first, or join a new room?"

Options: A) `/leave` then join new room, B) Keep current session

### `/leave`

```bash
pkill -f "ai-avatar join" 2>/dev/null && echo "Avatar stopped." || echo "Avatar was not running."
```

Tell user: "Avatar disconnected."

### `/status`

Run the system state check block, then report:

```
Avatar status
─────────────────────────────────────
LiveKit:      [running at ws://... / stopped]
Avatar:       [running in room 'X' / not running]
LLM brain:    [gemma (local) / claude (cloud)]
Voice:        [voice ID]
Gemma model:  [downloaded / missing]
Kokoro TTS:   [ready / missing]
Last room:    [room name / —]
```

Also check the avatar log if running in background:
```bash
tail -20 /tmp/ai-avatar.log 2>/dev/null || echo "(No log file — avatar was run in foreground)"
```

### `/voice`

Show the voice table:

```
1.  af_heart   — warm American female       (default)
2.  af_sky     — bright American female
3.  af_nova    — expressive American female
4.  af_sarah   — clear American female
5.  am_adam    — natural American male
6.  am_michael — deep American male
7.  bf_emma    — British female
8.  bm_george  — British male
```

Wait for choice. Apply to `config.yaml` and prefs:

```bash
cd ~/Desktop/ai-meeting-avatar
VOICE="af_heart"   # ← substitute chosen voice ID
sed -i '' "s/^  voice: .*/  voice: \"$VOICE\"/" config.yaml

python3 - <<PYEOF
import json
from pathlib import Path
prefs_dir = Path.home() / ".config" / "ai-meeting-avatar"
prefs_dir.mkdir(parents=True, exist_ok=True)
prefs_file = prefs_dir / "prefs.json"
prefs = {}
if prefs_file.exists():
    try:
        prefs = json.loads(prefs_file.read_text())
    except Exception:
        pass
prefs["voice"] = "$VOICE"
prefs_file.write_text(json.dumps(prefs, indent=2))
print("Voice updated.")
PYEOF
```

Tell user: "Voice set to '$VOICE'. Takes effect next time you `/join` a room."

### `/brain`

Ask the user:

> "Which AI brain do you want?"
> - **1. Local Gemma 4** — offline, private, no API key (needs ~2.6 GB download if not done)
> - **2. Claude** — smarter, needs internet + Anthropic API key

Apply:

```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
ai-avatar brain --set gemma    # or: ai-avatar brain --set claude
```

If switching to Claude and `ANTHROPIC_API_KEY` is not set, prompt for it:
> "You'll need an Anthropic API key. Get one at `console.anthropic.com` → API Keys."
> "Paste it here (stored in .env, never committed to git):"

```bash
cd ~/Desktop/ai-meeting-avatar
echo "ANTHROPIC_API_KEY=PASTE_KEY_HERE" >> .env
```

Tell user: "Brain switched to [backend]. Takes effect next `/join`."

### `/avatar`

Ask:
> "Drop the path to your photo (JPG or PNG, front-facing, clear background works best)."

Apply:

```bash
cd ~/Desktop/ai-meeting-avatar
PHOTO_PATH="/path/to/photo.jpg"   # ← substitute actual path

# Update config.yaml
sed -i '' "s|  enabled: false|  enabled: true|" config.yaml
sed -i '' "s|  photo_path:.*|  photo_path: \"$PHOTO_PATH\"|" config.yaml

# Save to prefs
python3 - <<PYEOF
import json
from pathlib import Path
prefs_dir = Path.home() / ".config" / "ai-meeting-avatar"
prefs_dir.mkdir(parents=True, exist_ok=True)
prefs_file = prefs_dir / "prefs.json"
prefs = {}
if prefs_file.exists():
    try:
        prefs = json.loads(prefs_file.read_text())
    except Exception:
        pass
prefs["avatar_photo"] = "$PHOTO_PATH"
prefs_file.write_text(json.dumps(prefs, indent=2))
print("Avatar photo saved.")
PYEOF
```

Tell user: "Avatar photo set to '$PHOTO_PATH'. Phase 2 lip-sync will activate next `/join`. Note: Phase 2 requires additional deps — run `pip install -e '.[avatar]'` if not done."

### `/mute`

The avatar doesn't yet support in-band mute without a restart. Tell the user:

> "Mute in the video call app directly (Meet/Zoom mute button) — that mutes the BlackHole input at the source. Or use `/leave` to fully stop the avatar."

### `/unmute`

> "If you muted in Meet/Zoom, unmute there. The avatar is still listening in the LiveKit room."

### `/help`

Print:

```
AI Meeting Avatar — commands
──────────────────────────────────────────────────────────
/join [room]   Join a LiveKit room (or create a new one)
/leave         Stop the avatar and disconnect
/status        Show avatar health, brain, voice, and room
/voice         Change the TTS voice (no restart needed)
/brain         Switch between local Gemma 4 and cloud Claude
/avatar        Set a photo for lip-sync video (Phase 2)
/mute          Mute guidance (handled in Meet/Zoom)
/unmute        Unmute guidance
/help          Show this message

First time? Say "set up the avatar" and I'll walk you through everything.
Audio routing to Meet/Zoom: brew install blackhole-2ch
```

---

## Step D — Remote control from phone or second device

### Option 1 — iMessage / Slack

Send a message to Claude (via iMessage, Siri Shortcuts, or any interface that routes to Claude Code). Say "mute avatar", "/leave", or "/join standup" — Claude will handle it via this skill.

### Option 2 — LiveKit meet app (join from phone)

Use the open-source LiveKit Meet app to join the same room from your phone:

1. Point it at your LiveKit server (local IP or cloud URL)
2. Generate a participant token:
   ```bash
   cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
   ai-avatar generate-token --room my-room --identity phone-user
   ```
3. Use that token to join from the LiveKit Meet web app or mobile client

The avatar is already in the room — you'll hear it speak and it will hear you.

### Option 3 — iOS Shortcut

Create a Shortcut that SSHs to your Mac and runs:
```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate && ai-avatar join my-room
```

---

## Appendix A — Venv setup

If `.venv` is missing, create it and install all dependencies:

```bash
cd ~/Desktop/ai-meeting-avatar
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e . -q
echo "Venv ready."
```

If Python 3.11 is not installed:
```bash
brew install python@3.11
```

Then re-run the state check block.

---

## Appendix B — Configuration reference (config.yaml)

```yaml
# Switch LLM brain without restarting:
#   ai-avatar brain --set claude

# Faster (less accurate) speech recognition:
stt:
  model_size: tiny      # tiny | base | small | medium | large-v3
  device: mps           # mps (Apple Silicon) | cpu | cuda

# Change voice (also via /voice command):
tts:
  voice: am_adam        # af_heart | af_sky | af_nova | am_adam | am_michael | bf_emma | bm_george
  speed: 1.1            # 0.5 = slow, 2.0 = fast

# Smarter model, more RAM:
llm:
  model_variant: e4b
  model_path: ./models/gemma-4-e4b/gemma-4-E4B-it.litertlm

# Custom system prompt:
agent:
  system_prompt: |
    You are attending this meeting on behalf of [Name].
    Keep answers under 2 sentences unless asked for detail.

# Lower VAD threshold if avatar misses quiet speech:
stt:
  vad_energy_threshold: 0.01   # default 0.02 — lower = more sensitive
```

Download the E4B model instead of E2B:
```bash
GEMMA_VARIANT=e4b ./scripts/setup_models.sh
```

After any config change: `/leave` then `/join ROOM_NAME` to restart with new settings.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "No speech detected" | Speak louder; lower `stt.vad_energy_threshold` in config.yaml (try 0.01) |
| Avatar responds to everything / background noise | Raise `stt.vad_energy_threshold` (try 0.05) |
| Response is very slow | Use `stt.model_size: tiny`; Gemma E2B on Apple Silicon MPS is fastest |
| `FileNotFoundError: Gemma model` | Run `ai-avatar onboard` or `./scripts/setup_models.sh` |
| HF `401` or `GatedRepoError` | Apply for access at `huggingface.co/litert-community/gemma-4-E2B-it-litert-lm` |
| `litert_lm` not found | `pip install litert-lm-nightly` |
| `kokoro_onnx` not found | `pip install kokoro-onnx onnxruntime` |
| `sounddevice` not found | `pip install sounddevice` |
| "LiveKit connection refused" | Start LiveKit: `docker run --rm -d --name livekit-dev -p 7880:7880 -p 7881:7881 -e LIVEKIT_KEYS="devkey: secret" livekit/livekit-server --dev` |
| Can't hear avatar in Meet | BlackHole not selected as mic — Meet Settings → Microphone → BlackHole 2ch |
| Avatar exits immediately | Check log: `tail -50 /tmp/ai-avatar.log` — often an import error or missing model |
| `ANTHROPIC_API_KEY` missing | Add to `.env`: `ANTHROPIC_API_KEY=sk-ant-...` |
| Want to switch from Gemma to Claude | `/brain` command — no reinstall needed, just needs API key |
| Want to switch back to Gemma | `/brain` — Gemma model must already be downloaded |
| Config changes not taking effect | `/leave` then `/join ROOM_NAME` — config is loaded at startup |

---

## Completion status

Report at the end of every workflow:

- **DONE** — Avatar is running (or action completed). State what room it's in and which brain/voice.
- **DONE_WITH_CONCERNS** — Completed but issues exist. List them: e.g. "Pipeline test failed but LiveKit is up — TTS may not work."
- **BLOCKED** — Cannot proceed. State the blocker and what was tried.
- **NEEDS_CONTEXT** — Missing info needed to continue (e.g. no room name given).
