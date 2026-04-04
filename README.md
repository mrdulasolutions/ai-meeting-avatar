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
docker run --rm -d --name livekit-dev \
  -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server --dev --bind 0.0.0.0
```

> **Important**: `--bind 0.0.0.0` is required. Without it, LiveKit binds to `127.0.0.1` only (loopback), which breaks the Docker port mapping and causes `Connection reset by peer` errors from the agent and browser clients.

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

## Architecture note: LiveKit rooms, not direct Meet/Zoom dial-in

**The agent does NOT connect directly to a Google Meet or Zoom meeting URL.** It joins a LiveKit room — a separate real-time media server. Audio is then routed from the LiveKit room to Meet/Zoom through a virtual audio device on your computer.

This is an important distinction:
- `ai-avatar join my-room` connects to **your LiveKit server** (local Docker or cloud)
- To hear the avatar in Google Meet, you route the LiveKit audio output through a virtual mic (BlackHole on macOS) and select that mic in Meet
- Direct dial-in to Google Meet (e.g. via Puppeteer or a Meet bot) is a planned roadmap item

### Current integration options

**Option A — Virtual audio device (recommended, works today)**
1. Install BlackHole: `brew install blackhole-2ch`
2. Create a Multi-Output Device in macOS Audio MIDI Setup (BlackHole + speakers)
3. Set System Output to the Multi-Output Device
4. Start the avatar: `ai-avatar join my-room`
5. In Google Meet → Settings → Microphone → select **BlackHole 2ch**
6. The avatar's voice flows: `LiveKit room → BlackHole → Google Meet mic`

**Option B — LiveKit SIP Bridge (Zoom)**
Zoom supports SIP dial-in. Point a LiveKit SIP trunk at your Zoom meeting's SIP address. The agent joins as a SIP participant.

**Option C — livekit-meet browser client**
Run the open-source [livekit-meet](https://github.com/livekit-examples/meet) web app connected to the same LiveKit room — join from a browser, share screen into Google Meet.

### Joining from another device (phone, remote machine)

The LiveKit dev server on `localhost` is only reachable from the same machine. To join from a phone or remote device you need either:

**Local network**: Use your Mac's LAN IP instead of `localhost`:
```bash
# Find your Mac's IP
ipconfig getifaddr en0

# Join from phone via meet.livekit.io/custom/
# URL: ws://192.168.x.x:7880
# Token: generate with: ai-avatar generate-token --room my-room --identity user
```

Then open from the phone:
```
https://meet.livekit.io/custom/?liveKitUrl=ws://192.168.x.x:7880&token=<token>
```

**From anywhere (internet)**: Deploy to LiveKit Cloud or a cloud server — see [Deploying to production](#deploying-to-production) below.

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

## Deploying to production

For remote access (phone, another country, embedding into a product), the LiveKit server must be publicly accessible.

> **Note**: LiveKit Cloud project creation requires browser-based OAuth — there is no API or CLI to script it. Create the project in the dashboard once, then paste the credentials into `.env`.

### LiveKit Cloud (easiest, free tier available)

1. Sign up at [cloud.livekit.io](https://cloud.livekit.io) and create a project
2. From the project dashboard, copy:
   - **WebSocket URL** — looks like `wss://your-project.livekit.cloud`
   - **API Key** and **API Secret**
3. Add to your `.env` file (never commit these):
   ```bash
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=APIxxxxxxxxxxxxxxx
   LIVEKIT_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
4. Run the agent — it reads from `.env` automatically:
   ```bash
   ai-avatar join my-room
   ```
5. Generate a token for browser/phone clients:
   ```bash
   ai-avatar generate-token --room my-room --identity user
   ```
6. On the phone, open:
   ```
   https://meet.livekit.io/custom/?liveKitUrl=wss://your-project.livekit.cloud&token=<token>
   ```

The agent runs on your Mac and connects *out* to the cloud server. Phone and browser clients also connect to the cloud server. No port forwarding, no localhost restrictions.

### Self-hosted (VPS / cloud server)

Deploy LiveKit Server on any Linux VPS with a public IP. See the [LiveKit self-hosting docs](https://docs.livekit.io/home/self-hosting/local/). Same `.env` pattern as above once it's running.

---

## Known limitations

- **No direct Google Meet / Zoom dial-in** — the agent joins a LiveKit room, not a Meet/Zoom meeting URL directly. Audio routing via BlackHole (macOS) is the current workaround.
- **localhost-only by default** — the local Docker LiveKit server is not reachable from other devices or the internet without a cloud deployment or LAN IP access.
- **First response is slow** — Gemma 4 models load on the first room join, which takes 30–120 seconds depending on hardware. Subsequent responses are much faster.
- **macOS / Linux only** — LiteRT-LM (local Gemma 4) does not support Windows yet.
- **No interruption handling** — the agent processes speech segments sequentially; it cannot be interrupted mid-response.
- **CPU-only TTS** — Kokoro-ONNX runs on CPU. Real-time synthesis is fine for typical responses but very long replies may lag.

---

## Roadmap

- [ ] **Google Meet direct join** — headless Chrome (Puppeteer) bot that joins a Meet URL and bridges audio to LiveKit, so no BlackHole setup is needed
- [ ] **Zoom direct join** — LiveKit SIP bridge for Zoom dial-in
- [ ] **LiveKit Cloud one-click setup** — `ai-avatar cloud-setup` command that provisions a LiveKit Cloud project and updates config automatically
- [ ] **Windows support** — pending LiteRT-LM Windows builds
- [ ] **Interruption handling** — barge-in / cancel-in-progress support
- [ ] **Persistent memory** — conversation history stored across sessions
- [ ] **Phase 2: Avatar video** — SadTalker / LivePortrait lip-sync from a photo, pushed to OBS Virtual Camera → Zoom/Meet webcam

---

## Bugs fixed during development

These issues were discovered during live testing and have been patched:

### 1. CLI `join` command — `No such command 'join'` (Click vs Typer argv conflict)

**Symptom**: Running `ai-avatar join my-room` printed "Joining room…" then crashed with `No such command 'join'`.

**Cause**: `livekit.agents` uses [Typer](https://typer.tiangolo.com/) internally and calls `run_app()`, which re-parses `sys.argv` from scratch. At that point `sys.argv` still contained `['ai-avatar', 'join', 'my-room', '--url', ...]`, so Typer saw `join` as an unknown subcommand (it expects `start`, `dev`, `connect`, etc.).

**Fix** (`main.py`): Rewrite `sys.argv` to the livekit-agents format before calling `run_app()`:
```python
sys.argv = ["ai-avatar", "start", "--url", cfg.livekit.url,
            "--api-key", cfg.livekit.api_key, "--api-secret", cfg.livekit.api_secret]
agent_cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

### 2. Docker LiveKit — `Connection reset by peer`

**Symptom**: LiveKit container was running and ports were mapped, but the agent and browser both got `Connection reset by peer` on `ws://localhost:7880`.

**Cause**: LiveKit Server defaults to `--bind 127.0.0.1` (loopback). Inside a Docker container, the loopback interface is not the same as the container's network interface (`172.17.0.2`). Docker's port mapping routes host traffic to the container's network interface — which LiveKit wasn't listening on.

**Fix**: Add `--bind 0.0.0.0` to the `docker run` command so LiveKit listens on all container interfaces.

### 3. Hugging Face CLI renamed (`huggingface-cli` → `hf`)

**Symptom**: `setup_models.sh` calls `huggingface-cli` which is not found after installing `huggingface-hub>=0.24`.

**Cause**: The CLI binary was renamed from `huggingface-cli` to `hf` in newer releases.

**Fix**: Use the Python API for model downloads instead of the CLI:
```python
from huggingface_hub import snapshot_download
snapshot_download(repo_id="litert-community/gemma-4-E2B-it-litert-lm",
                  allow_patterns=["*.litertlm", "*.json", "*.md"],
                  local_dir="./models/gemma-4-e2b")
```
Or login with: `hf auth login --token $HF_TOKEN`

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
