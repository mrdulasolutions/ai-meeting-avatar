# AI Meeting Avatar

An AI that joins your meetings for you. It listens, thinks, and talks back — using your voice style, running on your Mac.

**No GPU. No cloud account. No voice samples. Just run the setup and go.**

---

## What it does

```
Someone speaks in your meeting
        ↓
AI listens (Whisper speech-to-text)
        ↓
AI thinks (Gemma 4 on your Mac, or Claude in the cloud)
        ↓
AI speaks back (Kokoro text-to-speech, 10 voice options)
        ↓
Everyone in the meeting hears the response
```

That's it. Your AI avatar attends the call so you don't have to.

---

## 30-second version

```bash
git clone https://github.com/mrdulasolutions/ai-meeting-avatar.git
cd ai-meeting-avatar
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
ai-avatar onboard
```

The setup wizard asks you two questions (which AI brain, which voice) and handles everything else.

When it's done:

```bash
ai-avatar join my-meeting
```

---

## How it connects to Google Meet / Zoom

The avatar doesn't dial into Meet directly. It joins a LiveKit room on your machine, and you route the audio into Meet using a free virtual mic.

```
Avatar → LiveKit room → BlackHole virtual mic → Google Meet microphone input
```

**Setup (one time, 2 minutes):**

1. `brew install blackhole-2ch`
2. Open Audio MIDI Setup → Create Multi-Output Device (BlackHole + your speakers)
3. System Settings → Sound → Output → pick the Multi-Output Device
4. In Google Meet → Settings → Microphone → pick **BlackHole 2ch**

Done. The avatar talks through your mic.

---

## Pick your brain

| Brain | What it is | Needs |
|-------|-----------|-------|
| **Gemma 4** (default) | Runs 100% on your Mac. Private. No internet needed after setup. | Free HuggingFace account, ~2.6 GB download |
| **Claude** | Smarter. Better at complex questions. | Anthropic API key (~$0.01/meeting) |

Switch anytime:

```bash
ai-avatar brain --set claude
ai-avatar brain --set gemma
```

---

## Pick your voice

| Voice | Style |
|-------|-------|
| af_heart | warm American female (default) |
| af_sky | bright American female |
| af_nova | expressive American female |
| am_adam | natural American male |
| am_michael | deep American male |
| bf_emma | British female |
| bm_george | British male |

Change in `config.yaml` under `tts.voice`, or use the Claude Code skill command.

---

## Commands

```bash
ai-avatar onboard           # First-time setup wizard
ai-avatar join <room>       # Join a meeting room
ai-avatar brain             # Switch between Gemma and Claude
ai-avatar test-pipeline     # Test the full pipeline locally
ai-avatar check-deps        # Verify everything is installed
ai-avatar generate-token    # Generate a token for phone/remote access
```

---

## Requirements

- **macOS or Linux** (no Windows yet)
- **Python 3.11+**
- **Docker** (for local LiveKit server)
- **ffmpeg** — `brew install ffmpeg`

---

## Join from your phone

Want to listen in from your phone while the avatar runs on your Mac?

**Option A — Local network:**
```bash
# Get your Mac's IP
ipconfig getifaddr en0

# Generate a token
ai-avatar generate-token --room my-meeting --identity phone

# Open on phone:
# https://meet.livekit.io/custom/?liveKitUrl=ws://YOUR_IP:7880&token=TOKEN
```

**Option B — From anywhere (LiveKit Cloud):**
1. Create a free project at [cloud.livekit.io](https://cloud.livekit.io)
2. Add credentials to `.env`:
   ```
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=APxxxx
   LIVEKIT_API_SECRET=xxxx
   ```
3. `ai-avatar join my-meeting` — now works from anywhere

---

## Phase 2 — talking avatar video (coming)

Give the AI a face. Drop in a photo, and it lip-syncs the responses using SadTalker or LivePortrait, streamed through OBS Virtual Camera into Zoom/Meet as your webcam.

Enable in `config.yaml`:
```yaml
avatar:
  enabled: true
  photo_path: ./assets/avatar.jpg
```

---

## Claude Code skill

This repo includes a Claude Code skill at `.claude/skills/ai-meeting-avatar/`. When you open this project in Claude Code, you can say:

- "join my meeting"
- "set up the avatar"
- `/ai-meeting-avatar my-room`
- `/ai-meeting-avatar status`
- `/ai-meeting-avatar voice`
- `/ai-meeting-avatar brain`

Claude handles everything — setup, joining, voice changes, status checks.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Setup wizard fails on Gemma download | You need approved HuggingFace access — apply at the model page |
| "No speech detected" | Lower `stt.vad_energy_threshold` in config.yaml (try 0.01) |
| Response is slow | Set `stt.model_size: tiny` and `stt.device: mps` on Apple Silicon |
| Can't hear avatar in Meet | Pick BlackHole 2ch as your mic in Meet settings |
| LiveKit connection refused | Start it: `docker run --rm -d -p 7880:7880 -p 7881:7881 -e LIVEKIT_KEYS="devkey: secret" livekit/livekit-server --dev` |
| Want to switch brain | `ai-avatar brain --set claude` or `--set gemma` |

---

## Project structure

```
ai-meeting-avatar/
├── src/ai_meeting_avatar/       # Python package
│   ├── main.py                  # CLI commands
│   ├── orchestrator.py          # LiveKit agent pipeline
│   ├── stt.py                   # Speech-to-text (Whisper)
│   ├── llm.py                   # Local LLM (Gemma 4)
│   ├── llm_claude.py            # Cloud LLM (Claude API)
│   ├── tts.py                   # Text-to-speech (Kokoro)
│   ├── config.py                # Configuration
│   └── avatar.py                # Lip-sync rendering (Phase 2)
├── .claude/skills/              # Claude Code skill
├── config.yaml                  # All settings
├── scripts/                     # Setup and launch helpers
└── tests/                       # Unit tests
```

---

## License

MIT — see [LICENSE.md](LICENSE.md)
