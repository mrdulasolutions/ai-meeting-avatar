# AI Meeting Avatar — Claude Code Skill

## Trigger phrases

Activate this skill when the user says any of:
- "join my meeting"
- "attend this call"
- "join the call"
- "join [room name]"
- "start the avatar"
- "set up the avatar"
- "set up my meeting avatar"
- "onboard" / "first time setup"
- Any slash command below

---

## Slash commands

| Command | What it does |
|---------|-------------|
| `/help` | Show all commands and a quick-start guide |
| `/join [room]` | Join a LiveKit room (asks for room name if omitted) |
| `/join [google-meet-url]` | Route audio to a Google Meet link via BlackHole |
| `/leave` | Stop the running avatar and disconnect from the room |
| `/mute` | Mute the avatar's microphone in the current room |
| `/unmute` | Unmute the avatar's microphone |
| `/status` | Show whether the avatar is running, which room, and pipeline health |
| `/brain` | Switch the LLM between local Gemma 4 and cloud Claude |
| `/voice` | Change the TTS voice interactively (no restart needed) |
| `/avatar` | Set or update the avatar photo for Phase 2 lip-sync video |

---

## Decision tree

```
User triggers the skill
        │
        ├── First run? (models not downloaded OR prefs not saved)
        │
        ├── YES → conversational onboarding  (Step A)
        │
        └── NO  → join the room directly     (Step B)
```

### Check for first run:

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from ai_meeting_avatar.prefs import needs_first_run
print('FIRST_RUN' if needs_first_run() else 'READY')
"
```

---

## Step A — First-run onboarding (conversational, ~5 minutes)

Tell the user:
> "Welcome! Let me get you set up — it'll take about 5 minutes and I'll handle everything. Just answer two quick questions."

### Question 1 — Voice

Say:
> "What kind of voice do you want the avatar to use? Here are your options:"

```
1.  af_heart   — warm American female  (default)
2.  af_sky     — bright American female
3.  af_nova    — natural American female
4.  am_adam    — natural American male
5.  am_michael — deep American male
6.  bf_emma    — British female
7.  bm_george  — British male
```

> "Type a number (1–7) or just press Enter for the default (af_heart)."

Map the choice to a voice ID and save it:
```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from ai_meeting_avatar import prefs
prefs.set('voice', 'CHOSEN_VOICE_ID')
"
```

### Question 2 — Avatar photo (optional, Phase 2)

Say:
> "Do you want a talking avatar video (Phase 2)? If yes, drop a photo path here — or press Enter to skip for now (audio-only mode works great)."

If the user provides a path:
```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from ai_meeting_avatar import prefs
prefs.set('avatar_photo', 'PATH_FROM_USER')
"
```

### Run the setup wizard

```bash
cd ~/Desktop/ai-meeting-avatar
source .venv/bin/activate 2>/dev/null || (python3.11 -m venv .venv && source .venv/bin/activate && pip install -e . -q)
ai-avatar onboard
```

The wizard handles:

| Step | What happens |
|------|-------------|
| 1 — Dependencies | Checks and auto-installs missing packages |
| 2 — Gemma 4 E2B | HF login, downloads ~2.6 GB model (one-time) |
| 3 — Kokoro TTS | Downloads ~80 MB voice models, plays a 2-second preview |
| 4 — LiveKit | Starts local media server via Docker |
| 5 — Pipeline test | Full STT → LLM → TTS test with audio playback |
| 6 — Summary | Status table + exact next command |

After the wizard exits successfully, mark setup complete and save the chosen voice:
```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from ai_meeting_avatar import prefs
p = prefs.load()
p['setup_complete'] = True
prefs.save(p)
"
```

**If a wizard step fails**, apply the fix and re-run:

| Error | Fix |
|-------|-----|
| `kokoro_onnx` not found | `pip install kokoro-onnx onnxruntime` |
| HF `401` on Gemma download | Guide user: apply at huggingface.co/litert-community/gemma-4-E2B-it-litert-lm |
| Docker not found | Skip LiveKit step — tell user to install Docker Desktop first |
| `litert_lm` not found | `pip install litert-lm-nightly` |

---

## Step B — Join a call

### 1. Make sure LiveKit is running

```bash
curl -s http://localhost:7880 >/dev/null && echo "LiveKit OK" || echo "Need to start LiveKit"
```

If not running:
```bash
docker run --rm -p 7880:7880 -p 7881:7881 \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev &
sleep 3
```

### 2. Get the room name

If the user said `/join my-room`, use `my-room`.
If no room name was given, ask:
> "What's the room name? (You can make one up — e.g., 'standup' or 'interview'.)"

Save it for next time:
```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from ai_meeting_avatar import prefs
prefs.set('last_room', 'ROOM_NAME')
"
```

### 3. Apply saved voice preference

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from ai_meeting_avatar import prefs
v = prefs.get('voice')
import re, pathlib
cfg = pathlib.Path('config.yaml').read_text()
cfg = re.sub(r'(voice:\s*)[\"\']\S+[\"\'']', f'voice: \"{v}\"', cfg)
pathlib.Path('config.yaml').write_text(cfg)
"
```

### 4. Join the room

```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
ai-avatar join ROOM_NAME
```

Tell the user:
> "Avatar is live in room 'ROOM_NAME'. Use /status to check it, /leave to stop it."

---

## Step C — Connect to Google Meet or Zoom

After the agent is running, route audio to your call:

**macOS (BlackHole — free virtual audio):**
```bash
brew install blackhole-2ch
```
Then:
1. Open **Audio MIDI Setup** → create a **Multi-Output Device** (BlackHole + your speakers)
2. **System Settings → Sound → Output** → select Multi-Output Device
3. In Google Meet → Settings → Microphone → select **BlackHole 2ch**
4. Avatar voice flows: `LiveKit room → BlackHole → Google Meet mic`

Tell the user:
> "Open Google Meet, go to Settings → Microphone, and pick BlackHole 2ch. The avatar will speak through that mic."

---

## Step D — Remote control (phone or another device)

The user can control the avatar from their phone or a second device without touching the terminal:

1. **iMessage / Slack / any chat that triggers Claude**: Send a message like "mute avatar" or "/leave" — Claude will handle it via this skill.
2. **Dedicated room URL**: If using [livekit-meet](https://github.com/livekit-examples/meet), point it at `ws://YOUR_MACHINE_IP:7880` — the user joins that URL from their phone, and the avatar is already in the same room.
3. **Shortcut / Siri**: Create an iOS Shortcut that sends an HTTP request to a local webhook, or use SSH to your Mac and run `ai-avatar join ROOM_NAME`.

---

## Slash command handlers

### `/brain`

```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
ai-avatar brain
```

Or switch directly without prompting:
```bash
ai-avatar brain --set claude    # switch to Claude API
ai-avatar brain --set gemma     # switch back to Gemma 4 (local)
```

After switching to Claude, remind the user:
> "Set your Anthropic API key: `export ANTHROPIC_API_KEY=sk-ant-...`
> Or add it to .env: `ANTHROPIC_API_KEY=sk-ant-...`
> Install the SDK if needed: `pip install -e '.[claude]'`"

After switching to Gemma:
> "Gemma runs fully offline — no API key needed."

> "Brain switched. Restart the avatar (`/leave` then `/join`) to apply the change."

### `/voice`

Say:
> "Here are the available voices. Pick a number:"
(show the same table as onboarding — af_heart, af_sky, …)

Save the choice to prefs and update `config.yaml`:
```bash
python3 -c "... prefs.set('voice', 'NEW_VOICE') ..."
```
> "Voice updated. It'll take effect next time you /join a room."

### `/status`

Run:
```bash
curl -s http://localhost:7880/rtc/health 2>/dev/null && echo "LiveKit running" || echo "LiveKit stopped"
```

Report back:
- LiveKit: running / stopped
- Avatar process: running in room X / not running
- Models: downloaded / missing (use `needs_first_run()`)
- Voice: current voice from prefs
- Last room: from prefs

### `/leave`

```bash
pkill -f "ai-avatar join" && echo "Avatar stopped"
```
> "Avatar disconnected."

### `/mute` / `/unmute`

The Gemma LLM has built-in `mute_microphone()` / `unmute_microphone()` tools. Send a chat message in the LiveKit room to trigger them, or restart with `--muted` flag if supported.

> For now: tell the user to use the host's mute button in Meet/Zoom, or `/leave` and `/join` again.

### `/avatar`

Ask:
> "Drop the path to your photo (JPG or PNG)."

Save it and enable avatar in config:
```bash
python3 -c "
import sys, re, pathlib; sys.path.insert(0, 'src')
from ai_meeting_avatar import prefs
prefs.set('avatar_photo', 'PHOTO_PATH')
cfg = pathlib.Path('config.yaml').read_text()
cfg = re.sub(r'enabled:\s*false', 'enabled: true', cfg, count=1)
cfg = re.sub(r'photo_path:.*', f'photo_path: PHOTO_PATH', cfg)
pathlib.Path('config.yaml').write_text(cfg)
"
```
> "Avatar photo saved. Phase 2 will activate next time you /join."

### `/help`

Print:
```
AI Meeting Avatar — quick reference
────────────────────────────────────
/join [room]   Join a LiveKit room (routes audio to Meet/Zoom via BlackHole)
/leave         Disconnect the avatar
/mute          Mute the avatar mic
/unmute        Unmute the avatar mic
/brain         Switch LLM between Gemma 4 (local) and Claude (cloud)
/voice         Change the TTS voice
/avatar        Set a photo for lip-sync video (Phase 2)
/status        Check if everything is running
/help          Show this message

First time? Just say "join my meeting" and I'll walk you through setup.
```

---

## Troubleshooting quick-reference

| Symptom | Fix |
|---------|-----|
| "No speech detected" | Speak louder; lower `stt.vad_energy_threshold` in config.yaml |
| Response is very slow | Use `stt.model_size: tiny`; Gemma E2B on Apple Silicon MPS is fastest |
| Want a different voice | `/voice` command — no re-download needed |
| `FileNotFoundError: Gemma model` | Run `ai-avatar onboard` or `./scripts/setup_models.sh` |
| HF `401` on Gemma download | Apply at huggingface.co/litert-community/gemma-4-E2B-it-litert-lm |
| Kokoro models missing | Run `./scripts/setup_models.sh` |
| `litert_lm` not found | `pip install litert-lm-nightly` |
| `kokoro_onnx` not found | `pip install kokoro-onnx onnxruntime` |
| "LiveKit connection refused" | Run the `docker run …` command in Step B |
| Can't hear avatar in Meet | BlackHole not selected as mic in Meet settings |
| Claude `anthropic` not found | `pip install -e ".[claude]"` |
| Claude `AuthenticationError` | Set `ANTHROPIC_API_KEY` in `.env` or shell env |

---

## Configuration changes (edit config.yaml)

```yaml
# Higher-quality model (needs more RAM, slower to load)
llm:
  model_variant: e4b
  model_path: ./models/gemma-4-e4b/gemma-4-E4B-it.litertlm

# Faster (less accurate) speech recognition
stt:
  model_size: tiny

# Change voice without restarting (update prefs via /voice instead)
tts:
  voice: am_adam     # or: af_sky, af_nova, am_michael, bf_emma, bm_george
  speed: 1.0
  lang: en-us        # en-gb for British voices

# Make the avatar more concise
agent:
  system_prompt: |
    You are attending this meeting on behalf of [Name].
    Keep answers under 2 sentences unless asked for detail.
```

To download E4B instead of E2B:
```bash
GEMMA_VARIANT=e4b ./scripts/setup_models.sh
```

After any config change: `/leave` then `/join ROOM_NAME` to restart.
