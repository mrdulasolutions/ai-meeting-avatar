---
name: ai-meeting-avatar
description: |
  AI Meeting Avatar — join Google Meet or Zoom as a local voice AI agent.
  Whisper STT + Gemma 4 or Claude + Kokoro TTS. Routes audio via BlackHole.
  Use when: "join my meeting", "set up the avatar", "start the avatar",
  "attend this call", "onboard", or any /ai-meeting-avatar command.
argument-hint: "[room-name | voice | brain | status | leave | help]"
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

# AI Meeting Avatar

## Dynamic state (loaded every invocation)

```!
bash "${CLAUDE_SKILL_DIR}/scripts/state-check.sh" 2>/dev/null || echo "STATE_CHECK_FAILED"
```

---

## Decision tree

Use the state output above to branch:

| State | Action |
|-------|--------|
| `PROJECT=missing` | Tell user: "Can't find the ai-meeting-avatar project. Make sure you're in the right directory." Stop. |
| `VENV=missing` | Run **Venv Setup** below, then re-invoke skill. |
| `SETUP_COMPLETE=False` | Run **First-Run Onboarding**. |
| `BACKEND=gemma` AND `GEMMA_MODEL=missing` | Run **First-Run Onboarding**. |
| `BACKEND=claude` AND `ANTHROPIC_KEY=missing` | Tell user: "Claude backend needs an API key. Paste your key and I'll save it to .env." Then `echo "ANTHROPIC_API_KEY=THE_KEY" >> .env` |
| `KOKORO_MODEL=missing` | Run **First-Run Onboarding**. |
| `DOCKER=missing` AND user wants to join | Warn: "Docker not installed — needed for local LiveKit. Install from docker.com or use LiveKit Cloud." |
| Otherwise (ready) | Handle user command (join, status, etc.). |

If `$ARGUMENTS` is provided, handle as a command:
- `$ARGUMENTS` starts with a room name → **Join a Call** with that room
- `$ARGUMENTS` = "status" → **Status**
- `$ARGUMENTS` = "leave" → **Leave**
- `$ARGUMENTS` = "voice" → **Change Voice**
- `$ARGUMENTS` = "brain" → **Switch Brain**
- `$ARGUMENTS` = "help" → **Help**
- No arguments + user said "join" → **Join a Call** (ask for room name)
- No arguments + user said "set up" / "onboard" → **First-Run Onboarding**

---

## Venv Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e . -q
```

If Python 3.11 missing: `brew install python@3.11`

---

## First-Run Onboarding

Tell the user:
> "Welcome! Let me set up your AI meeting avatar. Two quick questions, then I'll run the setup wizard."

### Question 1 — Brain (LLM backend)

> **Which AI brain should the avatar use?**
>
> **1) Local Gemma 4** — offline, private, no API key needed
>    - Runs entirely on your Mac. ~2.6 GB one-time download.
>    - Needs free Hugging Face account with Gemma 4 access.
>
> **2) Claude** — smarter, needs internet + Anthropic API key
>    - Better reasoning and conversation. ~$0.01/meeting.
>    - No large download — works immediately.

Wait for choice. Apply:

```bash
source .venv/bin/activate
ai-avatar brain --set gemma   # or: ai-avatar brain --set claude
```

### Question 2 — Voice

Read `${CLAUDE_SKILL_DIR}/references/voices.md` and show the voice table.

> "Pick a voice by number, or press Enter for the default (af_heart)."

Apply with the helper script:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/set-voice.sh" "VOICE_ID"
```

### Run the wizard

```bash
source .venv/bin/activate
ai-avatar onboard
```

The wizard is interactive — it handles dependencies, model downloads, LiveKit, and a pipeline test. Let it run to completion without interruption.

### After wizard completes

Mark setup complete:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/save-pref.sh" setup_complete true
```

Tell user: "You're all set! Say `/ai-meeting-avatar my-room` to join a call."

### If wizard fails

Read `${CLAUDE_SKILL_DIR}/references/troubleshooting.md` for the fix, apply it, then re-run `ai-avatar onboard`.

---

## Join a Call

### 1. LiveKit must be running

If state shows `LIVEKIT=stopped`:

**Local (Docker):**
```bash
docker run --rm -d --name livekit-dev \
  -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev --bind 0.0.0.0
sleep 3
```

**LiveKit Cloud (for remote/phone access):**
1. Sign up free at cloud.livekit.io → create project
2. Add to `.env` in the project root:
   ```
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=APxxxx
   LIVEKIT_API_SECRET=xxxx
   ```

### 2. Get room name

- From `$ARGUMENTS` if provided
- From `LAST_ROOM` in state if user says "join last room"
- Otherwise ask: "What room name? (e.g. 'standup', 'interview')"

Save room:
```bash
bash "${CLAUDE_SKILL_DIR}/scripts/save-pref.sh" last_room "ROOM_NAME"
```

### 3. Join

```bash
source .venv/bin/activate
ai-avatar join "ROOM_NAME"
```

Or run in background:
```bash
source .venv/bin/activate
nohup ai-avatar join "ROOM_NAME" > /tmp/ai-avatar.log 2>&1 &
echo "PID: $!"
```

### 4. Route audio to Google Meet / Zoom

Tell the user about BlackHole setup:

> **One-time setup (2 minutes):**
> 1. Install BlackHole: `brew install blackhole-2ch`
> 2. Open Audio MIDI Setup → click + → Create Multi-Output Device
> 3. Check both BlackHole 2ch and your speakers
> 4. System Settings → Sound → Output → select Multi-Output Device
>
> **In your meeting app:**
> - Google Meet: Settings → Microphone → BlackHole 2ch
> - Zoom: Settings → Audio → Microphone → BlackHole 2ch

Report status:
> "Avatar is live in room 'ROOM'. Use `/ai-meeting-avatar status` to check, `/ai-meeting-avatar leave` to stop."

---

## Leave

```bash
pkill -f "ai-avatar join" 2>/dev/null && echo "Avatar stopped." || echo "Avatar was not running."
```

---

## Status

Use the dynamic state output from the top of this skill. Format as:

```
Avatar Status
───────────────────────────────────
LiveKit:     [running / stopped]
Avatar:      [running in room 'X' / stopped]
Brain:       [gemma (local) / claude (cloud)]
Voice:       [voice ID]
Gemma model: [ok / missing]
Kokoro TTS:  [ok / missing]
Last room:   [room / —]
Setup:       [complete / not started]
```

If avatar is running in background, also show:
```bash
tail -10 /tmp/ai-avatar.log 2>/dev/null
```

---

## Change Voice

Read `${CLAUDE_SKILL_DIR}/references/voices.md` and show the table.

Wait for choice, then apply:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/set-voice.sh" "VOICE_ID"
```

> "Voice set to VOICE_ID. Takes effect next `/ai-meeting-avatar [room]`."

---

## Switch Brain

> **Which brain?**
> 1) Local Gemma 4 — offline, private
> 2) Claude — smarter, needs API key

```bash
source .venv/bin/activate
ai-avatar brain --set gemma   # or: ai-avatar brain --set claude
```

If switching to Claude and `ANTHROPIC_API_KEY` is not in `.env`:
> "Paste your Anthropic API key (from console.anthropic.com):"

```bash
echo "ANTHROPIC_API_KEY=THE_KEY" >> .env
```

> "Brain switched. Takes effect next `/ai-meeting-avatar [room]`."

---

## Avatar Management

### Set Photo

> "Drop the path to a front-facing photo (JPG or PNG)."

```bash
source .venv/bin/activate
ai-avatar avatar set-photo "PATH_FROM_USER"
```

### Enable Avatar

```bash
source .venv/bin/activate
ai-avatar avatar enable
```

### Disable Avatar

```bash
source .venv/bin/activate
ai-avatar avatar disable
```

### Avatar Status

```bash
source .venv/bin/activate
ai-avatar avatar status
```

### Test Avatar Pipeline

```bash
source .venv/bin/activate
ai-avatar avatar test
```

### Full Avatar Setup (guided)

```bash
source .venv/bin/activate
ai-avatar avatar-setup
```

This installs deps, validates photo, downloads SadTalker, and enables the avatar.

> After any avatar change: leave then rejoin to reload.

---

## Generate Token (for phone/remote access)

```bash
source .venv/bin/activate
ai-avatar generate-token --room "ROOM" --identity "phone-user"
```

Give the token to the user for connecting from LiveKit Meet or a mobile client.

---

## Help

```
AI Meeting Avatar — commands
──────────────────────────────────────────────────────────
/ai-meeting-avatar [room]      Join a LiveKit room
/ai-meeting-avatar leave       Stop the avatar
/ai-meeting-avatar status      Show health, brain, voice, room
/ai-meeting-avatar voice       Change the TTS voice
/ai-meeting-avatar brain       Switch Gemma 4 ↔ Claude
/ai-meeting-avatar avatar      Manage lip-sync avatar (Phase 2)
/ai-meeting-avatar help        Show this message

Avatar subcommands:
  ai-avatar avatar enable      Enable lip-sync video
  ai-avatar avatar disable     Audio-only mode
  ai-avatar avatar set-photo   Set source photo
  ai-avatar avatar test        Test render pipeline
  ai-avatar avatar status      Check config & readiness
  ai-avatar avatar-setup       Guided setup (photo + deps + models)

First time? Say "set up the avatar" for guided onboarding.
Audio routing: brew install blackhole-2ch
```

---

## Troubleshooting

Read `${CLAUDE_SKILL_DIR}/references/troubleshooting.md` for the full table.

---

## Configuration reference

Key `config.yaml` fields:

```yaml
llm:
  backend: "gemma"          # "gemma" | "claude"
stt:
  model_size: "base"        # tiny | base | small | medium | large-v3
  device: "cpu"             # cpu | mps (Apple Silicon) | cuda
  vad_energy_threshold: 0.02
tts:
  voice: "af_heart"
  speed: 1.0
  lang: "en-us"             # en-us | en-gb
avatar:
  enabled: false            # true to enable lip-sync video
  photo_path: "./assets/avatar.jpg"
  model: "sadtalker"        # "sadtalker" | "liveportrait"
  camera_output: "auto"     # "auto" | "pyvirtualcam" | "obs" | "none"
  render_width: 256
  render_height: 256
  device: "cpu"             # "cpu" | "mps" | "cuda"
agent:
  system_prompt: |
    You are attending this meeting on behalf of [Name].
    Keep answers under 2 sentences unless asked for detail.
```

After config changes: leave then rejoin to reload.

---

## Completion status

Report at the end of every workflow:

- **DONE** — Action completed. State the room, brain, and voice.
- **DONE_WITH_CONCERNS** — Completed but issues exist. List each concern.
- **BLOCKED** — Cannot proceed. State blocker and what was tried.
- **NEEDS_CONTEXT** — Missing info. State exactly what's needed.
