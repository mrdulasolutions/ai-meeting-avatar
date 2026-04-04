# AI Meeting Avatar — Claude Code Skill

## Trigger phrases

Use this skill when the user says any of:
- "join my meeting"
- "attend this call"
- "join the call"
- "join [room name]"
- "start avatar agent"
- "join Google Meet / Zoom"
- "run the meeting agent"

---

## What this skill does

Launches the `ai-meeting-avatar` agent into a LiveKit room so it can:

1. **Listen** — transcribes speech via local Whisper STT
2. **Respond** — generates replies via local Ollama LLM
3. **Speak** — synthesises audio with the cloned voice via Coqui XTTS-v2
4. *(Phase 2)* **Show avatar** — renders lip-synced video via SadTalker → OBS virtual camera

---

## Skill workflow

### Step 1 — Identify the meeting details

Ask the user for:
- **Room name** (LiveKit room, or meeting URL if using a bridge)
- **LiveKit server URL** (default: `ws://localhost:7880`)
- Whether they want to override the LLM model or voice sample

### Step 2 — Pre-flight checks

```bash
# From the ai-meeting-avatar project directory:
ai-avatar check-deps
```

If any dependency is missing, guide the user through:
```bash
pip install -e .
./scripts/setup_models.sh
```

### Step 3 — Ensure voice sample exists

```bash
ls assets/voice_samples/speaker.wav
```

If missing, prompt the user to add a 6-30 second WAV clip:
```bash
cp ~/path/to/your_voice.wav assets/voice_samples/speaker.wav
```

### Step 4 — Start LiveKit (if running locally)

```bash
docker run --rm -p 7880:7880 -p 7881:7881 \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev
```

### Step 5 — Join the room

```bash
ai-avatar join <ROOM_NAME>
# or with explicit flags:
ai-avatar join my-meeting \
  --url ws://localhost:7880 \
  --api-key devkey \
  --api-secret secret
```

### Step 6 — Verify it works

In a second terminal, run the pipeline smoke test:
```bash
ai-avatar test-pipeline --text "Hello, can you hear me?"
```

---

## Common configuration changes

| Goal | Edit in `config.yaml` |
|------|-----------------------|
| Change LLM model | `llm.model: mistral` |
| Use GPU for TTS | `tts.gpu: true` |
| Change Whisper model size | `stt.model_size: small` |
| Enable avatar video | `avatar.enabled: true` |
| Point to different voice sample | `tts.speaker_wav: ./path/to/voice.wav` |
| Adjust response verbosity | Edit `agent.system_prompt` |

---

## Architecture reference

```
Microphone (remote participant)
        │
        ▼
  EnergyVAD  ──── silence threshold ────►  discard
        │
        │ speech segment (float32 array)
        ▼
  WhisperSTT (faster-whisper, local)
        │ transcript text
        ▼
  OllamaLLM  (local Ollama server)
        │ reply text
        ▼
  CoquiXTTS  (XTTS-v2, local, voice-cloned)
        │ audio chunks
        ▼
  LiveKit AudioSource  ──►  room participants hear the avatar
        │ (Phase 2 only)
        ▼
  SadTalker / LivePortrait  ──►  MP4 video
        │
        ▼
  OBS WebSocket  ──►  Virtual Camera  ──►  Zoom/Google Meet webcam
```

---

## Troubleshooting

**"Speaker WAV not found"**
→ `cp your_voice.wav assets/voice_samples/speaker.wav`

**Ollama connection refused**
→ Ensure Ollama is running: `ollama serve`

**LiveKit connection refused**
→ Start the LiveKit dev server (Step 4 above)

**Transcription is empty / very slow**
→ Use a smaller Whisper model: `stt.model_size: tiny` in `config.yaml`

**TTS model download hangs**
→ XTTS-v2 is ~1.8 GB. Let it complete or check `~/.local/share/tts`
