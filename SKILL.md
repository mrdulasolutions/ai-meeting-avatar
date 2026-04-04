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
| 2 — Gemma 4 E2B | Logs into HF, downloads ~2.6 GB model (one-time) |
| 3 — Kokoro TTS | Downloads ~80 MB voice models, plays a 2-second audio preview |
| 4 — LiveKit | Starts the local media server via Docker |
| 5 — Pipeline test | Runs full STT → LLM → TTS with audio playback |
| 6 — Summary | Shows status and exact next command |

**If a step fails**, read the error shown in the terminal and handle it:

- `kokoro_onnx` not found → `pip install kokoro-onnx onnxruntime` then re-run
- HF `401` error → guide user to apply for Gemma 4 access at huggingface.co
- Docker not found → skip LiveKit step, have them install Docker first
- `litert_lm` not found → `pip install litert-lm-nightly` then re-run

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
| Response is very slow | Switch to `stt.model_size: tiny`; Gemma E2B on Apple Silicon MPS is fastest |
| Want a different voice | Change `tts.voice` in config.yaml — no re-download needed |
| `FileNotFoundError: Gemma model not found` | Run `ai-avatar onboard` or `./scripts/setup_models.sh` |
| HF `401` on model download | Apply for Gemma 4 access at huggingface.co/litert-community/gemma-4-E2B-it-litert-lm |
| Kokoro models missing | Run `./scripts/setup_models.sh` step 3 |
| `litert_lm` not found | `pip install litert-lm-nightly` |
| `kokoro_onnx` not found | `pip install kokoro-onnx onnxruntime` |
| "LiveKit connection refused" | Start LiveKit (see Step B above) |
| Can't hear avatar in Meet | BlackHole not set as mic in Meet settings |

---

## Configuration changes (edit config.yaml)

Offer these when the user wants to tweak behaviour:

```yaml
# Use the larger, higher-quality Gemma 4 E4B model
llm:
  model_variant: e4b
  model_path: ./models/gemma-4-e4b/gemma-4-E4B-it.litertlm

# Disable meeting-control tools (mute/unmute) if not needed
llm:
  enable_tools: false

# Faster STT (less accurate)
stt:
  model_size: tiny

# Change voice (no re-download needed — all voices in voices-v1.0.bin)
tts:
  voice: am_adam      # natural male
  # voice: af_sky     # bright female
  # voice: bf_emma    # British female
  # voice: bm_george  # British male
  speed: 1.0
  lang: en-us         # or en-gb for British voices

# Make the avatar more concise / detailed
agent:
  system_prompt: |
    You are attending this meeting on behalf of [Name].
    Keep answers under 2 sentences unless asked for detail.
```

To download E4B instead of E2B:
```bash
GEMMA_VARIANT=e4b ./scripts/setup_models.sh
```

After any config change: restart with `ai-avatar join <room>`.
