---
name: ai-meeting-avatar
description: |
  Installable meeting-avatar skill. Sets up and runs a synced video avatar
  backed by Whisper STT, Gemma or Claude, Kokoro TTS, and a virtual camera path.
  Use when the user wants to set up the avatar, preview readiness, join a room,
  change voice, switch brain, or inspect status.
argument-hint: "[room-name | status | doctor | setup | voice | brain | leave]"
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

## Dynamic state

```!
bash "${CLAUDE_SKILL_DIR}/scripts/state-check.sh" 2>/dev/null || echo "STATE_CHECK_FAILED"
```

Use the state output above as the source of truth.

## Core behavior

- If `PROJECT=missing`: tell the user the project is not available and stop.
- If `VENV=missing`: run the venv setup block below, then re-check state.
- If `SETUP_COMPLETE=False` or `READY_TO_JOIN=False`: run **Setup**.
- If the user asks for `status`: run **Status**.
- If the user asks for `doctor`: run **Doctor**.
- If the user asks for `voice`: run **Voice**.
- If the user asks for `brain`: run **Brain**.
- If the user asks for `leave`: run **Leave**.
- Otherwise, treat the argument as a room name and run **Join**.

## Venv setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e . -q
```

## Setup

Tell the user:
> "I’m going to run the avatar-first setup flow so the skill has one reliable room-join path."

Then run:

```bash
source .venv/bin/activate
ai-avatar onboard
```

After setup succeeds:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/save-pref.sh" setup_complete true
```

If setup leaves any failed checks, run:

```bash
source .venv/bin/activate
ai-avatar doctor
```

Use `${CLAUDE_SKILL_DIR}/references/troubleshooting.md` only when a concrete setup step fails.

## Join

Before joining, ensure Docker / LiveKit is available when the configured URL is local.

If the state says `LIVEKIT_RUNNING=False` and the configured URL is `ws://localhost:7880`, start local LiveKit:

```bash
docker run --rm -d --name livekit-dev \
  -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev --bind 0.0.0.0
sleep 3
```

Save the room:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/save-pref.sh" last_room "ROOM_NAME"
```

Start the avatar:

```bash
source .venv/bin/activate
nohup ai-avatar join "ROOM_NAME" > /tmp/ai-avatar.log 2>&1 &
echo "PID: $!"
```

Tell the user which virtual microphone and camera path to select in the meeting app.

## Status

```bash
source .venv/bin/activate
ai-avatar doctor
```

If `/tmp/ai-avatar.log` exists and the avatar is running, also show the last few lines:

```bash
tail -10 /tmp/ai-avatar.log 2>/dev/null
```

## Doctor

```bash
source .venv/bin/activate
ai-avatar doctor
```

## Voice

Read `${CLAUDE_SKILL_DIR}/references/voices.md`, help the user pick a voice, then apply:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/set-voice.sh" "VOICE_ID"
```

## Brain

Ask which brain to use if the user did not specify one, then run:

```bash
source .venv/bin/activate
ai-avatar brain --set gemma
```

or:

```bash
source .venv/bin/activate
ai-avatar brain --set claude
```

If Claude is selected and no API key is present, ask for it and append it to `.env`.

## Leave

```bash
pkill -f "ai-avatar join" 2>/dev/null && echo "Avatar stopped." || echo "Avatar was not running."
rm -f /tmp/ai-avatar.pid /tmp/ai-avatar-room
```
