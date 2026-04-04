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
- "onboard"
- "first time setup"

---

## Decision tree

```
User wants to join a call
        │
        ├── Has setup been completed?
        │   (voice sample exists at assets/voice_samples/speaker.wav)
        │
        ├── NO  → run onboarding wizard first  (Step A)
        │
        └── YES → join the room directly       (Step B)
```

Check with:
```bash
ls assets/voice_samples/speaker.wav 2>/dev/null && echo "READY" || echo "NEEDS_SETUP"
```

---

## Step A — First-time setup (onboarding wizard)

Tell the user:
> "Let's get you set up. This takes about 5 minutes and I'll walk you through every step — just follow the prompts."

Then run:
```bash
cd ~/Desktop/ai-meeting-avatar
source .venv/bin/activate 2>/dev/null || python3.11 -m venv .venv && source .venv/bin/activate && pip install -e . -q
ai-avatar onboard
```

The wizard handles everything interactively:

| Step | What happens |
|------|-------------|
| 1 — Dependencies | Checks and auto-installs missing packages |
| 2 — Ollama LLM | Starts Ollama if not running, pulls the model |
| 3 — Voice recording | Records 15 s from mic with a countdown bar |
| 4 — LiveKit | Starts the local media server via Docker |
| 5 — Pipeline test | Speaks a test sentence in the cloned voice |
| 6 — Summary | Shows status and exact next command |

**If a step fails**, read the error shown in the terminal and handle it:

- `sounddevice` error → `pip install sounddevice` then re-run
- Ollama not found → guide user to [ollama.com](https://ollama.com), then `ollama serve`
- Docker not found → skip LiveKit step, have them install Docker first
- Voice sample too quiet → re-run `ai-avatar onboard` and re-record closer to mic

---

## Step B — Join a call

### Confirm LiveKit is running first:
```bash
curl -s http://localhost:7880 >/dev/null && echo "LiveKit OK" || echo "Start LiveKit first"
```

If not running:
```bash
docker run --rm -p 7880:7880 -p 7881:7881 \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev &
sleep 3
```

### Join the room:
```bash
cd ~/Desktop/ai-meeting-avatar && source .venv/bin/activate
ai-avatar join <ROOM_NAME>
```

Ask the user for the room name if they haven't given it.

---

## Step C — Connect to Google Meet or Zoom

After the agent is running in its room, the user needs to route audio:

**macOS (BlackHole virtual audio — free):**
```bash
brew install blackhole-2ch
```
Then:
1. Open **Audio MIDI Setup** → create a **Multi-Output Device** (BlackHole + speakers)
2. In **System Settings → Sound → Output** → select the Multi-Output Device
3. Open Google Meet → Settings → Microphone → **BlackHole 2ch**
4. The avatar's voice flows: `LiveKit room → BlackHole → Google Meet mic`

Tell the user:
> "Open Google Meet, go to Settings → Microphone, and select BlackHole 2ch. The avatar will speak through that mic. Done!"

---

## Troubleshooting quick-reference

Ask the user which symptom they're seeing, then apply the fix:

| Symptom | Fix |
|---------|-----|
| "No speech detected" | Speak louder / closer to mic; lower `stt.vad_energy_threshold` in config.yaml |
| Response is very slow | Change `stt.model_size: tiny` and `llm.model: llama3.2:1b` in config.yaml |
| Voice doesn't sound like me | Re-record in a quieter room: `ai-avatar onboard` → skip to Step 3 |
| "Ollama connection refused" | Run `ollama serve` in a separate terminal |
| "LiveKit connection refused" | Start LiveKit (see Step B above) |
| TTS download stuck | XTTS-v2 is ~1.8 GB — wait; check `~/.local/share/tts` |
| Can't hear avatar in Meet | BlackHole not set as mic in Meet settings |

---

## Configuration changes (edit config.yaml)

Offer these when the user wants to tweak behaviour:

```yaml
# Smarter/slower responses
llm:
  model: mistral         # or llama3.1, phi3, etc.

# Faster STT (less accurate)
stt:
  model_size: tiny

# Use Apple Silicon GPU for TTS (faster)
tts:
  gpu: true              # works on CUDA too

# Make the avatar more talkative / brief
agent:
  system_prompt: |
    You are attending this meeting on behalf of [Name].
    Keep answers under 2 sentences unless asked for detail.
```

After any config change: restart with `ai-avatar join <room>`.
