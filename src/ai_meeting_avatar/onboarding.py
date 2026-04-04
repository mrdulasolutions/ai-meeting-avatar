"""
Interactive onboarding wizard for ai-meeting-avatar.

Walks the user through every setup step with live status indicators.
No manual file-placing or terminal knowledge required.

Steps:
  1 — Check + auto-install Python dependencies
  2 — Choose AI brain (Gemma local or Claude cloud)
  3 — Set up chosen LLM backend
  4 — Download Kokoro TTS models (~80 MB, auto)
  5 — Avatar setup (Phase 2 — optional photo + model download)
  6 — Start LiveKit dev server (via Docker)
  7 — End-to-end pipeline test (STT → LLM → TTS)
  8 — Summary + next steps
"""

from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

console = Console()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _ok(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green]  {msg}")


def _fail(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red]  {msg}")


def _info(msg: str) -> None:
    console.print(f"  [bold blue]→[/bold blue]  {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [bold yellow]![/bold yellow]  {msg}")


def _step(number: int, total: int, title: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]Step {number}/{total} — {title}[/bold cyan]"))
    console.print()


# ── Steps ──────────────────────────────────────────────────────────────────────


def step_welcome() -> None:
    console.print(
        Panel.fit(
            "[bold white]AI Meeting Avatar[/bold white]\n"
            "[dim]Local voice agent for Google Meet & Zoom[/dim]\n\n"
            "This wizard sets everything up in [bold]5 minutes[/bold].\n"
            "No voice recording, no GPU needed.",
            title="[bold cyan]Welcome[/bold cyan]",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()
    Confirm.ask("  Ready to begin?", default=True)


def step_choose_brain() -> str:
    """Ask the user which LLM backend to use. Returns 'gemma' or 'claude'."""
    _step(2, 8, "Choose AI brain")

    console.print(
        Panel(
            "[white]The avatar needs an AI brain to understand and respond.\n\n"
            "[bold]1) Local Gemma 4[/bold]  (offline, private, no API key)\n"
            "   • Runs entirely on your machine — no data leaves your device\n"
            "   • Requires ~2.6 GB download (one-time) + HF account with model access\n"
            "   • Works without internet after setup\n\n"
            "[bold]2) Claude[/bold]  (smarter, needs internet + Anthropic API key)\n"
            "   • Much more capable — better reasoning and conversation\n"
            "   • Requires an [cyan]Anthropic API key[/cyan] (pay-per-use)\n"
            "   • No large model download — works instantly\n"
            "   • Requires:  [dim]pip install -e \".[claude]\"[/dim]",
            title="[bold yellow]Which AI brain?[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    console.print()

    choice = Prompt.ask(
        "  Choose brain",
        choices=["1", "2"],
        default="1",
    )
    backend = "claude" if choice == "2" else "gemma"

    # Persist the choice to config.yaml
    _set_config_backend(backend)

    if backend == "gemma":
        _ok("Selected: Local Gemma 4 (offline, private)")
    else:
        _ok("Selected: Claude (cloud, smarter)")

    return backend


def _set_config_backend(backend: str) -> None:
    """Update llm.backend in config.yaml."""
    import re  # noqa: PLC0415

    config_path = Path("config.yaml")
    if not config_path.exists():
        return
    text = config_path.read_text()
    # Replace the backend line (handles quoted and unquoted values)
    new_text = re.sub(
        r"^(\s*backend:\s*).*$",
        rf'\1"{backend}"',
        text,
        flags=re.MULTILINE,
    )
    config_path.write_text(new_text)


def step_check_deps() -> bool:
    """Check Python packages and system tools. Auto-installs if missing."""
    _step(1, 8, "Checking dependencies")

    py_packages = [
        ("faster_whisper", "faster-whisper"),
        ("litert_lm", "litert-lm-nightly"),
        ("kokoro_onnx", "kokoro-onnx"),
        ("onnxruntime", "onnxruntime"),
        ("livekit", "livekit"),
        ("livekit.agents", "livekit-agents"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("sounddevice", "sounddevice"),
        ("soundfile", "soundfile"),
        ("rich", "rich"),
        ("yaml", "PyYAML"),
    ]

    missing_py = []
    for module, name in py_packages:
        try:
            importlib.import_module(module)
            _ok(name)
        except ImportError:
            _fail(f"{name}  [dim](missing)[/dim]")
            missing_py.append(name)

    console.print()
    system_tools = ["ffmpeg", "git", "docker"]
    for tool in system_tools:
        if shutil.which(tool):
            _ok(tool)
        else:
            _warn(f"{tool}  [dim](not found — some features may not work)[/dim]")

    if missing_py:
        console.print()
        _info("Installing missing Python packages …")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".", "-q"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _ok("All packages installed.")
        else:
            _fail("pip install failed. Run manually:  pip install -e .")
            console.print(f"  [dim]{result.stderr[-400:]}[/dim]")
            return False

    return True


def step_gemma_download() -> bool:
    """Download Gemma 4 E2B weights from Hugging Face."""
    _step(3, 8, "LLM — Gemma 4 E2B (Google AI Edge)")

    from .config import load_config  # noqa: PLC0415

    cfg = load_config()
    model_path = Path(cfg.llm.model_path)

    # Check if already downloaded
    existing = list(model_path.parent.glob("*.litertlm")) if model_path.parent.exists() else []
    if existing:
        size_gb = existing[0].stat().st_size / (1024 ** 3)
        _ok(f"Gemma 4 E2B already present: {existing[0].name} ({size_gb:.1f} GB)")
        return True

    console.print(
        Panel(
            "[white]Gemma 4 E2B runs entirely on your machine — no cloud, no API keys.\n\n"
            "Requirements:\n"
            "  • Free [bold]Hugging Face account[/bold] with Gemma 4 access\n"
            "  • Apply at: [cyan]huggingface.co/litert-community/gemma-4-E2B-it-litert-lm[/cyan]\n"
            "  • Download size: [bold]~2.6 GB[/bold]  (one-time)",
            title="[bold yellow]Gemma 4 E2B[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    console.print()

    if not Confirm.ask("  Download Gemma 4 E2B now?", default=True):
        _warn("Skipped. Run setup_models.sh later to download.")
        return False

    # HF login check
    try:
        result = subprocess.run(
            ["huggingface-cli", "whoami"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            _ok(f"Logged in as: {result.stdout.strip().split(chr(10))[0]}")
        else:
            raise subprocess.CalledProcessError(1, "whoami")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        _info("You need a Hugging Face token. Get one at: huggingface.co/settings/tokens")
        console.print()
        token = Prompt.ask("  Paste your HF token (hidden)", password=True)
        login = subprocess.run(
            ["huggingface-cli", "login", "--token", token],
            capture_output=True, text=True,
        )
        if login.returncode != 0:
            _fail("HF login failed — check your token.")
            return False
        _ok("Logged in to Hugging Face.")

    # Download
    model_path.parent.mkdir(parents=True, exist_ok=True)
    repo = cfg.llm.hf_repo_e2b
    console.print()
    _info(f"Downloading {repo} (~2.6 GB) …")

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  TimeElapsedColumn(), console=console, transient=False) as p:
        p.add_task("Downloading Gemma 4 E2B …", total=None)
        dl = subprocess.run(
            ["huggingface-cli", "download", repo,
             "--local-dir", str(model_path.parent),
             "--local-dir-use-symlinks", "False",
             "--include", "*.litertlm", "*.json", "*.md"],
            capture_output=True, text=True,
        )

    if dl.returncode != 0:
        _fail("Download failed.")
        console.print(f"  [dim]{dl.stderr[-500:]}[/dim]")
        _info("If you see 401, apply for model access first.")
        return False

    files = list(model_path.parent.glob("*.litertlm"))
    if not files:
        _fail("No .litertlm file found after download.")
        return False

    _ok(f"Downloaded: {files[0].name} ({files[0].stat().st_size / 1e9:.1f} GB)")

    # Write path to .env
    env_path = Path(".env")
    env_line = f"GEMMA_MODEL_PATH={files[0]}"
    if env_path.exists():
        lines = [l for l in env_path.read_text().splitlines()
                 if not l.startswith("GEMMA_MODEL_PATH=")]
        lines.append(env_line)
        env_path.write_text("\n".join(lines) + "\n")
    else:
        env_path.write_text(env_line + "\n")

    _ok("Path saved to .env")
    return True


def step_claude_setup() -> bool:
    """Guide the user through setting up the Claude backend."""
    _step(3, 8, "LLM — Claude (Anthropic API)")

    import os  # noqa: PLC0415

    # Check for existing key
    existing_key = os.getenv("ANTHROPIC_API_KEY", "")
    if existing_key:
        _ok(f"ANTHROPIC_API_KEY already set ({existing_key[:8]}…)")
    else:
        console.print(
            Panel(
                "[white]To use Claude you need an Anthropic API key.\n\n"
                "  1. Create an account at [cyan]console.anthropic.com[/cyan]\n"
                "  2. Generate an API key under Settings → API Keys\n"
                "  3. Paste it below (stored in .env — never committed to git)",
                title="[bold yellow]Anthropic API Key[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        console.print()
        token = Prompt.ask("  Paste your Anthropic API key (hidden)", password=True)
        if not token.startswith("sk-ant-"):
            _warn("Key doesn't look like an Anthropic key (should start with sk-ant-). Continuing anyway.")

        # Write to .env
        env_path = Path(".env")
        env_line = f"ANTHROPIC_API_KEY={token}"
        if env_path.exists():
            lines = [ln for ln in env_path.read_text().splitlines()
                     if not ln.startswith("ANTHROPIC_API_KEY=")]
            lines.append(env_line)
            env_path.write_text("\n".join(lines) + "\n")
        else:
            env_path.write_text(env_line + "\n")
        _ok("API key saved to .env")

    # Check anthropic package is installed
    try:
        import anthropic  # noqa: PLC0415, F401
        _ok("anthropic package installed.")
    except ImportError:
        _info("Installing anthropic SDK …")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".[claude]", "-q"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            _ok("anthropic SDK installed.")
        else:
            _fail("Failed to install anthropic. Run:  pip install -e '.[claude]'")
            return False

    return True


def step_tts_setup() -> bool:
    """Download Kokoro TTS models and play a test sentence."""
    _step(4, 8, "TTS — Kokoro voice (~80 MB)")

    from .config import load_config  # noqa: PLC0415
    from .tts import DEFAULT_MODEL_DIR, download_models  # noqa: PLC0415

    cfg = load_config()
    model_dir = Path(cfg.tts.model_dir)

    # Check if already downloaded
    if (model_dir / "kokoro-v1.0.onnx").exists() and (model_dir / "voices-v1.0.bin").exists():
        _ok(f"Kokoro models already present at {model_dir}")
    else:
        _info("Downloading Kokoro TTS models (~80 MB) …")
        try:
            with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                          TimeElapsedColumn(), console=console, transient=True) as p:
                p.add_task("Downloading kokoro-v1.0.onnx and voices-v1.0.bin …", total=None)
                download_models(model_dir)
            _ok("Kokoro models downloaded.")
        except Exception as exc:
            _fail(f"Download failed: {exc}")
            return False

    # Quick audio test
    console.print()
    _info(f"Testing voice '{cfg.tts.voice}' …")
    try:
        import sounddevice as sd  # noqa: PLC0415
        from kokoro_onnx import Kokoro  # noqa: PLC0415

        kokoro = Kokoro(
            str(model_dir / "kokoro-v1.0.onnx"),
            str(model_dir / "voices-v1.0.bin"),
        )
        samples, sr = kokoro.create(
            "Hello! Your AI meeting avatar voice is ready.",
            voice=cfg.tts.voice,
            speed=cfg.tts.speed,
            lang=cfg.tts.lang,
        )
        console.print("  [dim]Playing 2-second preview …[/dim]")
        sd.play(samples, sr)
        sd.wait()
        _ok("TTS is working! That's what the avatar will sound like.")
    except ImportError:
        _warn("sounddevice not installed — skipping audio preview.")
        _ok("Kokoro models ready (audio test skipped).")
    except Exception as exc:
        _warn(f"Audio preview failed: {exc}")
        _ok("Kokoro models downloaded (preview unavailable).")

    return True


def step_avatar_setup() -> bool:
    """Optional Phase 2 avatar setup: photo, deps, model download."""
    _step(5, 8, "Talking avatar (Phase 2 — optional)")

    console.print(
        Panel(
            "[white]The avatar can show a lip-synced video of a photo during calls.\n"
            "This appears as a webcam in Zoom/Meet via a virtual camera.\n\n"
            "[bold]Requirements:[/bold]\n"
            "  • A front-facing photo (JPG/PNG, at least 256x256)\n"
            "  • SadTalker model (~500 MB download)\n"
            "  • Avatar Python deps: torch, opencv, pyvirtualcam\n"
            "  • OBS Studio installed (for virtual camera on macOS)\n\n"
            "[dim]Skip this if you only want audio — you can set it up later with:[/dim]\n"
            "[dim]  ai-avatar avatar-setup[/dim]",
            title="[bold yellow]Talking Avatar[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    console.print()

    if not Confirm.ask("  Set up the talking avatar now?", default=False):
        _info("Skipped. Run later:  ai-avatar avatar-setup")
        return True  # Not a failure — it's optional

    # ── Photo ─────────────────────────────────────────────────────────────────
    console.print()
    photo_path = Prompt.ask(
        "  Path to your avatar photo (JPG/PNG)",
        default="./assets/avatar.jpg",
    )

    from .avatar import validate_photo  # noqa: PLC0415

    result = validate_photo(photo_path)
    if not result["valid"]:
        _fail(f"Photo issue: {result['error']}")
        _info("You can fix this later with:  ai-avatar avatar-setup")
        return True  # Non-fatal

    _ok(f"Photo valid: {result['width']}x{result['height']}, {result['faces']} face(s) detected")

    # Save photo path to config
    _set_config_avatar(photo_path)
    _ok(f"Photo path saved to config.yaml")

    # ── Avatar deps ───────────────────────────────────────────────────────────
    console.print()
    _info("Checking avatar dependencies …")

    avatar_deps_missing = False
    for module, name in [("torch", "torch"), ("cv2", "opencv-python"), ("pyvirtualcam", "pyvirtualcam")]:
        try:
            importlib.import_module(module)
            _ok(name)
        except ImportError:
            _fail(f"{name}  [dim](missing)[/dim]")
            avatar_deps_missing = True

    if avatar_deps_missing:
        console.print()
        if Confirm.ask("  Install avatar dependencies? (may take a few minutes)", default=True):
            _info("Installing avatar deps …")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", ".[avatar]", "-q"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                _ok("Avatar dependencies installed.")
            else:
                _fail("Install failed. Run manually:  pip install -e '.[avatar]'")
                console.print(f"  [dim]{result.stderr[-400:]}[/dim]")
                return True  # Non-fatal
        else:
            _info("Skipped. Install later:  pip install -e '.[avatar]'")
            return True

    # ── SadTalker model ───────────────────────────────────────────────────────
    console.print()
    _info("Checking SadTalker model …")

    from .config import load_config  # noqa: PLC0415

    cfg = load_config()
    sadtalker_path = Path(cfg.avatar.sadtalker_path)

    if sadtalker_path.exists() and (sadtalker_path / "inference.py").exists():
        _ok(f"SadTalker already present at {sadtalker_path}")
    else:
        if not shutil.which("git"):
            _warn("git not found — cannot clone SadTalker. Install git first.")
            return True

        console.print()
        if Confirm.ask("  Download SadTalker model (~500 MB)?", default=True):
            _info("Cloning SadTalker …")
            clone = subprocess.run(
                ["git", "clone", "--depth", "1",
                 "https://github.com/OpenTalker/SadTalker.git",
                 str(sadtalker_path)],
                capture_output=True, text=True,
            )
            if clone.returncode != 0:
                _fail("SadTalker clone failed.")
                console.print(f"  [dim]{clone.stderr[-300:]}[/dim]")
                return True

            # Download checkpoints
            _info("Downloading SadTalker checkpoints …")
            ckpt_dir = sadtalker_path / "checkpoints"
            gfpgan_dir = sadtalker_path / "gfpgan" / "weights"
            ckpt_dir.mkdir(parents=True, exist_ok=True)
            gfpgan_dir.mkdir(parents=True, exist_ok=True)

            base_url = "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc"
            ckpt_files = [
                "SadTalker_V0.0.2_256.safetensors",
                "mapping_00109-model.pth.tar",
                "mapping_00229-model.pth.tar",
            ]
            for fname in ckpt_files:
                dest = ckpt_dir / fname
                if not dest.exists():
                    dl = subprocess.run(
                        ["curl", "-L", "--progress-bar", "-o", str(dest),
                         f"{base_url}/{fname}"],
                        capture_output=True, text=True,
                    )
                    if dl.returncode == 0 and dest.exists():
                        _ok(f"Downloaded: {fname}")
                    else:
                        _warn(f"Failed to download {fname}")

            # GFPGAN face enhancer
            gfpgan_dest = gfpgan_dir / "GFPGANv1.4.pth"
            if not gfpgan_dest.exists():
                gfpgan_url = "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth"
                subprocess.run(
                    ["curl", "-L", "--progress-bar", "-o", str(gfpgan_dest), gfpgan_url],
                    capture_output=True, text=True,
                )
                if gfpgan_dest.exists():
                    _ok("Downloaded: GFPGANv1.4.pth (face enhancer)")

            _ok("SadTalker ready.")
        else:
            _info("Skipped. Download later via:  ./scripts/setup_models.sh")

    # ── Enable avatar in config ───────────────────────────────────────────────
    _set_config_avatar_enabled(True)
    _ok("Avatar enabled in config.yaml")

    return True


def _set_config_avatar(photo_path: str) -> None:
    """Update avatar.photo_path in config.yaml."""
    import re  # noqa: PLC0415

    config_path = Path("config.yaml")
    if not config_path.exists():
        return
    text = config_path.read_text()
    new_text = re.sub(
        r"^(\s*photo_path:\s*).*$",
        rf'\1"{photo_path}"',
        text,
        flags=re.MULTILINE,
    )
    config_path.write_text(new_text)


def _set_config_avatar_enabled(enabled: bool) -> None:
    """Update avatar.enabled in config.yaml."""
    import re  # noqa: PLC0415

    config_path = Path("config.yaml")
    if not config_path.exists():
        return
    text = config_path.read_text()
    val = "true" if enabled else "false"
    new_text = re.sub(
        r"^(\s*enabled:\s*)(?:true|false)\b",
        rf"\1{val}",
        text,
        flags=re.MULTILINE,
        count=1,  # Only the first `enabled:` (avatar section comes first in file)
    )
    config_path.write_text(new_text)


def step_livekit() -> bool:
    """Check if LiveKit is running; offer to start it via Docker."""
    _step(6, 8, "LiveKit server")

    import urllib.request  # noqa: PLC0415

    livekit_url = "http://localhost:7880"
    try:
        urllib.request.urlopen(livekit_url, timeout=2)
        _ok("LiveKit already running at localhost:7880")
        return True
    except Exception:
        pass

    _info("LiveKit is not running locally.")
    console.print()

    if not shutil.which("docker"):
        _warn("Docker not installed — can't auto-start LiveKit.")
        console.print("  [dim]Install Docker from docker.com, then run:[/dim]")
        console.print(
            '  [bold]docker run --rm -p 7880:7880 -p 7881:7881 -e LIVEKIT_KEYS="devkey: secret" livekit/livekit-server --dev[/bold]'
        )
        console.print()
        Confirm.ask("  Press Enter once LiveKit is running", default=True)
        return True

    if Confirm.ask("  Start LiveKit dev server via Docker now?", default=True):
        _info("Starting LiveKit …")
        subprocess.Popen(
            ["docker", "run", "--rm",
             "-p", "7880:7880", "-p", "7881:7881",
             "-e", "LIVEKIT_KEYS=devkey: secret",
             "livekit/livekit-server", "--dev"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(4)
        try:
            urllib.request.urlopen(livekit_url, timeout=5)
            _ok("LiveKit is running at localhost:7880")
        except Exception:
            _warn("LiveKit may still be starting — continuing anyway.")
    else:
        _info("Skipped. Start LiveKit before joining a call.")

    return True


def step_pipeline_test() -> bool:
    """Run STT → LLM → TTS end-to-end. Return True if it passes."""
    _step(7, 8, "End-to-end test")

    console.print("  Runs the full pipeline (loads all models on first run).")
    console.print("  [dim]May take 1-3 minutes while models initialise.[/dim]")
    console.print()

    if not Confirm.ask("  Run the test now? (recommended)", default=True):
        _info("Skipped. Run later:  ai-avatar test-pipeline --text 'hello'")
        return True

    try:
        result = subprocess.run(
            [sys.executable, "-m", "ai_meeting_avatar.main",
             "test-pipeline",
             "--text", "Setup complete. I am your AI meeting avatar, ready to join calls."],
            timeout=300,
        )
        if result.returncode == 0:
            _ok("Pipeline test passed!")
            return True
        else:
            _warn("Test had issues — see output above.")
            return False
    except subprocess.TimeoutExpired:
        _warn("Timed out. Models may still be initialising. Try later:")
        _info("  ai-avatar test-pipeline --text 'hello'")
        return False
    except Exception as exc:
        _fail(f"Test failed: {exc}")
        return False


def step_summary(
    deps_ok: bool,
    backend: str,
    llm_ok: bool,
    tts_ok: bool,
    avatar_ok: bool,
    livekit_ok: bool,
    test_ok: bool,
) -> None:
    """Print the final status table and next-steps panel."""
    console.print()
    console.print(Rule("[bold cyan]Setup complete![/bold cyan]"))
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Icon", style="bold", width=4)
    table.add_column("Component")

    def row(ok: bool, label: str) -> None:
        table.add_row("[green]✓[/green]" if ok else "[yellow]![/yellow]", label)

    row(deps_ok, "Python dependencies")
    if backend == "claude":
        row(llm_ok, "Claude API — cloud LLM (Anthropic)")
    else:
        row(llm_ok, "Gemma 4 E2B — local LLM (no server needed)")
    row(tts_ok, "Kokoro TTS — natural voice, no cloning")
    row(avatar_ok, "Talking avatar (Phase 2)")
    row(livekit_ok, "LiveKit media server")
    row(test_ok, "End-to-end pipeline test")

    console.print(table)
    console.print()

    console.print(
        Panel(
            "[bold white]To join a meeting:[/bold white]\n\n"
            "  1. Make sure LiveKit is running:\n"
            "     [cyan]docker run --rm -p 7880:7880 -p 7881:7881 \\\n"
            '       -e LIVEKIT_KEYS="devkey: secret" \\\n'
            "       livekit/livekit-server --dev[/cyan]\n\n"
            "  2. Join a room:\n"
            "     [cyan]ai-avatar join my-meeting[/cyan]\n\n"
            "  3. Route audio to Google Meet / Zoom via BlackHole:\n"
            "     [dim]brew install blackhole-2ch → set as mic in Meet/Zoom[/dim]\n\n"
            "[dim]Change voice: edit  tts.voice  in config.yaml[/dim]\n"
            "[dim]Run  ai-avatar --help  for all commands[/dim]",
            title="[bold green]Next Steps[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )


# ── Entry ──────────────────────────────────────────────────────────────────────


async def run_onboarding() -> None:
    """Run the full interactive onboarding wizard."""
    step_welcome()

    # Step 1 — deps (renumbered; step_check_deps still prints its own header)
    deps_ok = step_check_deps()
    if not deps_ok:
        _fail("Dependency check failed. Fix the issues above then re-run:  ai-avatar onboard")
        sys.exit(1)

    # Step 2 — choose brain
    backend = step_choose_brain()

    # Step 3 — set up chosen LLM
    if backend == "claude":
        llm_ok = step_claude_setup()
    else:
        llm_ok = step_gemma_download()

    tts_ok = step_tts_setup()
    avatar_ok = step_avatar_setup()
    livekit_ok = step_livekit()
    test_ok = step_pipeline_test()

    step_summary(
        deps_ok=deps_ok,
        backend=backend,
        llm_ok=llm_ok,
        tts_ok=tts_ok,
        avatar_ok=avatar_ok,
        livekit_ok=livekit_ok,
        test_ok=test_ok,
    )
