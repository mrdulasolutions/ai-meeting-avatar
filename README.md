# ai-meeting-avatar

A fully local AI meeting agent that joins Google Meet or Zoom calls, listens to conversation, and responds with a cloned voice (and optionally a lip-synced avatar). No paid APIs required.

## Architecture

```
Remote participant audio
        │
        ▼
  EnergyVAD                  ← lightweight silence detection
        │ speech segment
        ▼
  WhisperSTT                 ← faster-whisper, runs on CPU/CUDA/MPS
        │ transcript
        ▼
  OllamaLLM                  ← local Ollama server (llama3.2 default)
        │ reply text
        ▼
  CoquiXTTS (XTTS-v2)        ← voice cloning from 6-30 s sample WAV
        │ audio chunks
        ▼
  LiveKit AudioSource ────────► room (everyone hears the avatar)

  (Phase 2 — avatar.enabled: true)
        │
        ▼
  SadTalker / LivePortrait   ← lip-sync portrait from still photo
        │ MP4 video
        ▼
  OBS WebSocket ──────────────► Virtual Camera ──► Zoom/Meet webcam
```

### Component overview

| Module | File | Purpose |
|--------|------|---------|
| Config | `config.py` | Pydantic models, YAML + env loading |
| STT | `stt.py` | `WhisperSTT` + `EnergyVAD` |
| LLM | `llm.py` | `OllamaLLM`, `ChatHistory` |
| TTS | `tts.py` | `CoquiXTTS` with voice cloning |
| Avatar | `avatar.py` | `SadTalkerRenderer`, `LivePortraitRenderer`, `OBSVirtualCamera` |
| Orchestrator | `orchestrator.py` | LiveKit agent wiring STT→LLM→TTS |
| CLI | `main.py` | `ai-avatar` Click commands |

---

## Requirements

- **Python 3.11+**
- **Ollama** — [install](https://ollama.com) and run `ollama serve`
- **LiveKit server** — local dev server via Docker (see below), or any LiveKit cloud deployment
- **ffmpeg** — for audio/video processing (`brew install ffmpeg` / `apt install ffmpeg`)
- A **6-30 second WAV voice sample** for XTTS-v2 voice cloning
- *(Phase 2)* **OBS Studio** with WebSocket server enabled

---

## Installation

```bash
# 1. Clone the project
git clone <this-repo> ai-meeting-avatar
cd ai-meeting-avatar

# 2. Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -e .

# 4. Download models (Whisper, XTTS-v2, Ollama model)
chmod +x scripts/setup_models.sh
./scripts/setup_models.sh

# 5. Add your voice sample
cp /path/to/your_voice.wav assets/voice_samples/speaker.wav

# 6. (Optional) Add avatar photo for Phase 2
cp /path/to/photo.jpg assets/avatar.jpg
```

### Configuration

Copy and edit `config.yaml`:

```yaml
llm:
  model: llama3.2        # any model available via `ollama list`

stt:
  model_size: base       # tiny | base | small | medium | large-v3
  device: cpu            # cpu | cuda | mps

tts:
  speaker_wav: ./assets/voice_samples/speaker.wav
  gpu: false
```

Secrets can go in `.env` (see `.env.example`) — they override config.yaml.

---

## Usage

### Start LiveKit (local dev)

```bash
docker run --rm -p 7880:7880 -p 7881:7881 \
  -e LIVEKIT_KEYS="devkey: secret" \
  livekit/livekit-server --dev
```

### Join a room

```bash
ai-avatar join my-room
```

```bash
# Explicit flags
ai-avatar join my-room \
  --url ws://localhost:7880 \
  --api-key devkey \
  --api-secret secret
```

### Test the pipeline locally (no LiveKit needed)

```bash
# Record 5 s from mic, run through STT → LLM → TTS, play back
ai-avatar test-pipeline

# Skip mic, use text input
ai-avatar test-pipeline --text "What is the speed of light?"

# Transcribe an existing WAV
python scripts/test_pipeline.py --wav recording.wav --output reply.wav
```

### Verify dependencies

```bash
ai-avatar check-deps
```

### Generate a LiveKit token

```bash
ai-avatar generate-token --room my-room --identity avatar-agent
```

---

## Google Meet / Zoom integration

LiveKit does not natively bridge to Google Meet or Zoom. There are two approaches:

### Option A — LiveKit SIP Bridge (recommended for Zoom)
Zoom supports SIP dial-in. Point a LiveKit SIP trunk at your Zoom meeting's SIP address. The agent joins as a SIP participant.

### Option B — Browser automation + virtual audio device
1. Install a virtual audio device (e.g. BlackHole on macOS, VB-Audio on Windows).
2. Route the LiveKit room's output audio to the virtual device.
3. Open Google Meet / Zoom in Chrome and select the virtual device as microphone.
4. *(Phase 2)* Install OBS Virtual Camera; it appears as a webcam in Meet/Zoom.

### Option C — livekit-meet browser client
Run the open-source [livekit-meet](https://github.com/livekit-examples/meet) web app connected to the same LiveKit room — then join that URL from within a Google Meet screen share.

---

## Phase 2 — Avatar video

Enable in `config.yaml`:

```yaml
avatar:
  enabled: true
  photo_path: ./assets/avatar.jpg
  model: sadtalker        # or liveportrait
  enhancer: gfpgan

obs:
  enabled: true
  host: localhost
  port: 4455
  password: your-obs-ws-password
```

SadTalker and LivePortrait are cloned automatically by `setup_models.sh`.

The rendered MP4 is pushed to an OBS **Media Source** named `"AI Avatar"` (configurable), then output through OBS Virtual Camera — which appears as a webcam in Zoom and Google Meet.

---

## Development

```bash
# Run tests (no models or GPU needed — all mocked)
pytest tests/ -v

# Lint
ruff check src/ tests/

# Type-check
mypy src/
```

### Project structure

```
ai-meeting-avatar/
├── src/ai_meeting_avatar/
│   ├── config.py          # Pydantic config
│   ├── stt.py             # WhisperSTT + EnergyVAD
│   ├── llm.py             # OllamaLLM + ChatHistory
│   ├── tts.py             # CoquiXTTS (XTTS-v2)
│   ├── avatar.py          # SadTalker / LivePortrait / OBS
│   ├── orchestrator.py    # LiveKit agent + pipeline
│   └── main.py            # CLI (Click)
├── scripts/
│   ├── setup_models.sh    # Model download automation
│   └── test_pipeline.py   # Standalone pipeline test
├── tests/
│   └── test_pipeline.py   # Pytest unit tests (mocked)
├── assets/
│   └── voice_samples/     # Add speaker.wav here
├── config.yaml            # Main configuration
├── pyproject.toml
└── SKILL.md               # Claude Code skill integration
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `RuntimeError: Call load() first` | You forgot to call `stt.load()` / `tts.load()` / `llm.load()` |
| XTTS download hangs | XTTS-v2 is ~1.8 GB — let it finish, check `~/.local/share/tts` |
| Empty transcription | Use a larger Whisper model (`stt.model_size: small`) |
| Ollama connection refused | Run `ollama serve` in another terminal |
| TTS sounds wrong / robotic | Voice sample too short (<6 s) or too noisy — record a cleaner clip |
| LiveKit `401 Unauthorized` | API key/secret mismatch — check `.env` and LiveKit server flags |
| SadTalker `inference.py not found` | Re-run `./scripts/setup_models.sh` |

---

## License

MIT
