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

| State | Action |
|-------|--------|
| `PROJECT=missing` | Tell the user the project is not available and stop. |
| `PYTHON=missing` | Tell the user Python 3.11–3.13 is required and suggest `brew install python@3.13`. |
| `PYTHON=incompatible (...)` | Tell the user they need Python 3.11–3.13 and should recreate the venv with a compatible version. |
| `VENV=missing` | Run **Venv setup** below, then re-check state. |
| `SETUP_COMPLETE=False` or `READY_TO_JOIN=False` | Run **Setup**. |
| Otherwise | Handle the requested command. |

- If the user asks for `status`: run **Status**.
- If the user asks for `doctor`: run **Doctor**.
- If the user asks for `voice`: run **Voice**.
- If the user asks for `brain`: run **Brain**.
- If the user asks for `leave`: run **Leave**.
- Otherwise, treat the argument as a room name and run **Join**.

## Safety rules

- Never kill a process just because a port is in use.
- Never say "let me kill it and retry."
- If a port is occupied, first treat that as a possible already-running dependency and check whether it is usable.
- If the port belongs to another process and cannot be reused, pick or suggest a different port instead of killing anything.
- Only stop an existing avatar process when the user explicitly asked to `leave` or stop it.

## Venv setup

Use the `PYTHON` value from the state check to pick a compatible binary.

```bash
for py in python3.13 python3.12 python3.11; do
  if command -v "$py" >/dev/null 2>&1; then PYTHON="$py"; break; fi
done
$PYTHON -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e . -q
```

## Setup

Tell the user:
> "I’m going to run the avatar-first setup flow so the skill has one reliable room-join path."

Before any onboarding that might install packages or download models, always verify dependencies first:

```bash
source .venv/bin/activate
ai-avatar check-deps
```

If `check-deps` fails, stop and tell the user what is missing before attempting onboarding or downloads.

Never silently choose a brain or voice when the user did not provide one.
Use `AskUserQuestion` to collect:
- brain: `gemma` or `claude`
- voice: a Kokoro voice ID from `references/voices.md`
- avatar photo path: a real JPG/PNG path for the talking avatar

Run onboarding with non-interactive flags only after you already know the user’s brain and voice choice:

```bash
source .venv/bin/activate
ai-avatar onboard --brain BRAIN --voice VOICE_ID --yes
```

If you do not yet know all required values, ask first.
Do not bypass `AskUserQuestion` by defaulting to `claude`, `af_heart`, or a placeholder avatar photo.

If you intentionally want the CLI to ask interactively instead:

```bash
source .venv/bin/activate
ai-avatar onboard
```

Main setup must include avatar setup.
Do not skip avatar setup during the primary onboarding flow.
Use `ai-avatar avatar-setup` only as a follow-up command after the main setup has already completed.

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

If the state says `LIVEKIT_RUNNING=False` and the configured URL is local, start local LiveKit:

```bash
docker run --rm -d --name livekit-dev \
  -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev --bind 0.0.0.0
sleep 3
```

If Docker or another local helper reports that a port is already in use:

- do not kill the process holding the port
- check whether the existing service is already the dependency you need
- if it is usable, reuse it
- if it is not usable, tell the user which port is occupied and suggest switching to another port or stopping the conflicting app themselves

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

After the avatar is running, default to generating a ready-to-open browser link instead of making the user assemble server URL, room, and token manually:

```bash
source .venv/bin/activate
ai-avatar generate-link --room "ROOM_NAME" --identity browser-guest
```

Share that link directly with the user.
Do not surface raw API keys to the user, and do not ask them to manually combine server URL + token unless link generation fails.

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

If the user asks "what's the address", "how do I join", or similar, prefer:

```bash
source .venv/bin/activate
ai-avatar generate-link --room "ROOM_NAME" --identity browser-guest
```

Return the link, not the API key.

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
Do not invent or hardcode any API key or token value.

## Leave

```bash
pkill -f "ai-avatar join" 2>/dev/null && echo "Avatar stopped." || echo "Avatar was not running."
rm -f /tmp/ai-avatar.pid /tmp/ai-avatar-room
```
