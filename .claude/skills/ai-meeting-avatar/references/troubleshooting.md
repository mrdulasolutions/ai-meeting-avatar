# Troubleshooting Reference

## Common errors and fixes

| Symptom | Fix |
|---------|-----|
| "No speech detected" | Speak louder; lower `stt.vad_energy_threshold` in config.yaml (try 0.01) |
| Avatar responds to background noise | Raise `stt.vad_energy_threshold` (try 0.05) |
| Response is very slow | Use `stt.model_size: tiny`; set `stt.device: mps` on Apple Silicon |
| `FileNotFoundError: Gemma model` | Run `ai-avatar onboard` or `./scripts/setup_models.sh` |
| HF `401` or `GatedRepoError` | Apply for access at huggingface.co/litert-community/gemma-4-E2B-it-litert-lm |
| `litert_lm` not found | `pip install litert-lm-nightly` |
| `kokoro_onnx` not found | `pip install kokoro-onnx onnxruntime` |
| `sounddevice` not found | `pip install sounddevice` |
| `anthropic` not found | `pip install -e '.[claude]'` |
| "LiveKit connection refused" | Start LiveKit: `docker run --rm -d --name livekit-dev -p 7880:7880 -p 7881:7881 -e LIVEKIT_KEYS="devkey: secret" livekit/livekit-server --dev` |
| Can't hear avatar in Meet | BlackHole not selected as mic — Meet Settings → Microphone → BlackHole 2ch |
| Avatar exits immediately | Check log: `tail -50 /tmp/ai-avatar.log` — often an import error or missing model |
| `ANTHROPIC_API_KEY` missing | Add to `.env`: `ANTHROPIC_API_KEY=sk-ant-...` |
| Config changes not taking effect | `/leave` then `/join` — config is loaded at startup |
| Pipeline test times out | Models slow on first load — re-run: `ai-avatar test-pipeline --text 'hello'` |
| Docker not found | Install Docker Desktop from docker.com |
| Python 3.14+ "too new" / kokoro-onnx fails | kokoro-onnx only supports Python 3.11–3.13. Install: `brew install python@3.13` then `python3.13 -m venv .venv` |
| Python 3.10 or older | Need Python 3.11+. Install: `brew install python@3.13` |
| Port already in use | Do not kill the owning process automatically. Check whether the dependency is already running on that port; if not, choose a different port or ask the user to stop the conflicting app themselves. |

## Wizard step failures

| Wizard step | Common error | Fix |
|-------------|-------------|-----|
| Step 1 — Dependencies | pip install fails | Run `pip install -e .` manually, check Python version >=3.11 |
| Step 3 — Gemma download | 401 Unauthorized | Need approved HF access to gated model |
| Step 3 — Claude setup | Invalid API key | Key must start with `sk-ant-`, get from console.anthropic.com |
| Step 4 — Kokoro TTS | Download fails | Check internet; manually download from github.com/thewh1teagle/kokoro-onnx |
| Step 5 — LiveKit | Docker not running | Start Docker Desktop first |
| Step 6 — Pipeline test | Timeout | Normal on first run — models initializing. Re-run later. |
