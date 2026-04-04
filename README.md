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
  GemmaLLM (LiteRT-LM)       ← Gemma 4 E2B/E4B, in-process, no server
        │ reply text          ← supports function calling (mute/unmute, etc.)
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
| LLM (Gemma) | `llm.py` | `GemmaLLM` (LiteRT-LM), `ChatHistory`, meeting tools |
| LLM (Claude) | `llm_claude.py` | `ClaudeLLM` (Anthropic API), same interface |
| TTS | `tts.py` | `CoquiXTTS` with voice cloning |
| Avatar | `avatar.py` | `SadTalkerRenderer`, `LivePortraitRenderer`, `OBSVirtualCamera` |
| Orchestrator | `orchestrator.py` | LiveKit agent wiring STT→LLM→TTS |
| CLI | `main.py` | `ai-avatar` Click commands |

---

## Stack

| Layer | Technology |
|-------|-----------|
| **LLM** | [Google AI Edge LiteRT-LM](https://ai.google.dev/edge/litert-lm/overview) + Gemma 4 E2B/E4B — runs in-process, no server *(default)* |
| **LLM (cloud)** | [Anthropic Claude API](https://docs.anthropic.com/) — smarter, needs `ANTHROPIC_API_KEY` |
| **STT** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — local Whisper |
| **TTS** | [Kokoro-ONNX](https://github.com/thewh1teagle/kokoro-onnx) — 82M params, CPU-only, ~80 MB, multiple voice presets |
| **Transport** | [LiveKit Agents](https://docs.livekit.io/agents/) — real-time media |
| **Avatar** | SadTalker / LivePortrait (Phase 2) |

---

## Requirements

- **Python 3.11+**
- **Linux or macOS** (LiteRT-LM platform requirement; Windows coming soon)
- **LiveKit server** — local dev server via Docker (see below), or any LiveKit cloud deployment
- **ffmpeg** — for audio/video processing (`brew install ffmpeg` / `apt install ffmpeg`)
- *(Phase 2)* **OBS Studio** with WebSocket server enabled

**For local Gemma 4 (default):**
- Hugging Face account with Gemma 4 access — apply at [litert-community/gemma-4-E2B-it-litert-lm](https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm)
- ~2.6 GB disk for E2B weights (~4 GB for E4B)

**For Claude API (optional):**
- `ANTHROPIC_API_KEY` environment variable — get one at [console.anthropic.com](https://console.anthropic.com)
- No model download or GPU required

No voice samples or GPU needed for either option.

---

## Installation

```bash
# 1. Clone the project
git clone <this-repo> ai-meeting-avatar
cd ai-meeting-avatar

# 2. Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install core dependencies
pip install -e .

# Optional: add Claude API support
pip install -e ".[claude]"

# 4. Run the setup wizard
ai-avatar onboard
```

The onboarding wizard handles:
- Checking and installing missing packages
- Downloading Gemma 4 E2B (~2.6 GB, one-time, requires free HF account)
- Downloading Kokoro TTS models (~80 MB, automatic)
- Starting LiveKit dev server via Docker
- Running a full pipeline test with audio playback

### Configuration

Key fields in `config.yaml`:

```yaml
llm:
  # "gemma" (local, offline) or "claude" (cloud, needs API key)
  backend: gemma

  # Gemma settings (used when backend: gemma)
  model_variant: e2b     # "e2b" (~2.6 GB, fast) or "e4b" (~4 GB, higher quality)

  # Claude settings (used when backend: claude)
  claude_model: claude-sonnet-4-6   # or claude-opus-4-6, claude-haiku-4-5
  anthropic_api_key: ""              # leave blank — set ANTHROPIC_API_KEY in env

  # Shared
  enable_tools: true     # lets the LLM call mute/unmute/etc. during the call
  max_tokens: 512
  history_turns: 10

stt:
  model_size: base       # tiny | base | small | medium | large-v3
  device: cpu            # cpu | cuda | mps

tts:
  voice: af_heart        # af_heart, af_sky, am_adam, am_michael, bf_emma, bm_george …
  speed: 1.0             # 0.5 = slower, 2.0 = faster
  lang: en-us            # en-us or en-gb
```

The `GEMMA_MODEL_PATH` env var overrides `llm.model_path`. `ANTHROPIC_API_KEY` and `LLM_BACKEND` env vars also override config values.

Secrets go in `.env` (see `.env.example`) — they override config.yaml.

#### Switching brains at any time

```bash
# Interactive menu
ai-avatar brain

# Direct switch
ai-avatar brain --set claude
ai-avatar brain --set gemma

# One-shot env override (no file edit)
LLM_BACKEND=claude ai-avatar join my-room
```

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
│   ├── llm.py             # GemmaLLM + ChatHistory (local)
│   ├── llm_claude.py      # ClaudeLLM — Anthropic API backend
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
| `FileNotFoundError: Gemma model not found` | Run `./scripts/setup_models.sh` or `ai-avatar onboard` |
| HF download `401 Unauthorized` | Apply for Gemma 4 access at the HF model page |
| Empty transcription | Use a larger Whisper model: `stt.model_size: small` |
| Gemma responses are slow | E2B on CPU ~15-30 tok/s; set `stt.device: mps` on Apple Silicon |
| Want a different voice | Change `tts.voice` in config.yaml (see voice list in the file) |
| Kokoro models missing | Run `./scripts/setup_models.sh` — downloads ~80 MB automatically |
| LiveKit `401 Unauthorized` | API key/secret mismatch — check `.env` and LiveKit server flags |
| `litert_lm` import error | `pip install litert-lm-nightly` |
| `kokoro_onnx` import error | `pip install kokoro-onnx onnxruntime` |
| `anthropic` import error | `pip install -e ".[claude]"` |
| Claude `AuthenticationError` | Set `ANTHROPIC_API_KEY` in `.env` or shell |
| Want to switch LLM backend | `ai-avatar brain` (interactive) or `--set gemma\|claude` |

---

## License

MIT
