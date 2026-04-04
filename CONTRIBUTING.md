# Contributing

Thanks for wanting to help. Here's how to get started.

## Setup

```bash
git clone https://github.com/mrdulasolutions/ai-meeting-avatar.git
cd ai-meeting-avatar
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run tests

```bash
pytest tests/ -v
```

Tests are mocked — no models, no GPU, no LiveKit needed.

## Lint and type-check

```bash
ruff check src/ tests/
mypy src/
```

## Making changes

1. Fork the repo
2. Create a branch: `git checkout -b my-fix`
3. Make your changes
4. Run tests and lint
5. Open a pull request

## What makes a good PR

- **One thing per PR.** A bug fix is one PR. A new feature is another. Don't mix them.
- **Explain the why.** The diff shows what changed. The PR description should say why.
- **Test it.** If you're fixing a bug, add a test that fails without the fix. If you're adding a feature, add a test that exercises it.
- **Keep it small.** Smaller PRs get reviewed faster and merged sooner.

## Architecture quick guide

```
User speaks → WhisperSTT (stt.py) → GemmaLLM or ClaudeLLM (llm.py / llm_claude.py)
           → KokoroTTS (tts.py) → LiveKit audio output (orchestrator.py)
```

- `config.py` — Pydantic models, YAML + env var loading
- `orchestrator.py` — The main pipeline. Wires STT → LLM → TTS inside a LiveKit room.
- `main.py` — CLI commands (Click)
- `onboarding.py` — Setup wizard
- `prefs.py` — User preferences stored at `~/.config/ai-meeting-avatar/prefs.json`

## Where to contribute

### Good first issues

- Add a new Kokoro voice preset
- Improve error messages in the setup wizard
- Add a `--quiet` flag to suppress progress output
- Write tests for `prefs.py` and `config.py`

### Medium effort

- Whisper language auto-detection
- Multiple voice support (different voice per meeting context)
- Conversation memory across sessions
- Better VAD (voice activity detection) — replace energy-based with Silero VAD

### Big projects

- Direct Google Meet join via headless Chrome
- Zoom SIP bridge integration
- Phase 2 avatar rendering (SadTalker / LivePortrait)
- Windows support (blocked on LiteRT-LM)
- Web dashboard for monitoring active rooms

## Claude Code skill

The skill lives at `.claude/skills/ai-meeting-avatar/`. If you're modifying the skill:

- `SKILL.md` — Main skill instructions (keep under 400 lines)
- `scripts/` — Helper scripts referenced via `${CLAUDE_SKILL_DIR}`
- `references/` — Lookup tables loaded on demand

Test the skill by opening the project in Claude Code and saying "set up the avatar" or "/ai-meeting-avatar status".

## Code style

- Type hints on all function signatures
- Docstrings on classes and public functions
- `noqa: PLC0415` for lazy imports (they're intentional — keeps startup fast)
- Use `rich` for user-facing CLI output, `logging` for debug output

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
