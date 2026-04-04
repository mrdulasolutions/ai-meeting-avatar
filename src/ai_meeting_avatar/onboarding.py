"""
Interactive onboarding wizard for ai-meeting-avatar.

Walks the user through every setup step with live status indicators.
No manual file-placing or terminal knowledge required.

Steps:
  1 — Check + auto-install Python dependencies
  2 — Download Gemma 4 E2B (LLM) via Hugging Face
  3 — Download Kokoro TTS models (~80 MB, auto)
  4 — Start LiveKit dev server (via Docker)
  5 — End-to-end pipeline test (STT → LLM → TTS)
  6 — Summary + next steps
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
            "No voice recording, no API keys, no GPU needed.",
            title="[bold cyan]Welcome[/bold cyan]",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()
    Confirm.ask("  Ready to begin?", default=True)


def step_check_deps() -> bool:
    """Check Python packages and system tools. Auto-installs if missing."""
    _step(1, 5, "Checking dependencies")

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
    _step(2, 5, "LLM — Gemma 4 E2B (Google AI Edge)")

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


def step_tts_setup() -> bool:
    """Download Kokoro TTS models and play a test sentence."""
    _step(3, 5, "TTS — Kokoro voice (~80 MB)")

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


def step_livekit() -> bool:
    """Check if LiveKit is running; offer to start it via Docker."""
    _step(4, 5, "LiveKit server")

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
    _step(5, 5, "End-to-end test")

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


def step_summary(deps_ok: bool, llm_ok: bool, tts_ok: bool, livekit_ok: bool, test_ok: bool) -> None:
    """Print the final status table and next-steps panel."""
    console.print()
    console.print(Rule("[bold cyan]Step 6/5 — You're all set![/bold cyan]"))
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Icon", style="bold", width=4)
    table.add_column("Component")

    def row(ok: bool, label: str) -> None:
        table.add_row("[green]✓[/green]" if ok else "[yellow]![/yellow]", label)

    row(deps_ok, "Python dependencies")
    row(llm_ok, "Gemma 4 E2B — local LLM (no server needed)")
    row(tts_ok, "Kokoro TTS — natural voice, no cloning")
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

    deps_ok = step_check_deps()
    if not deps_ok:
        _fail("Dependency check failed. Fix the issues above then re-run:  ai-avatar onboard")
        sys.exit(1)

    llm_ok = step_gemma_download()
    tts_ok = step_tts_setup()
    livekit_ok = step_livekit()
    test_ok = step_pipeline_test()

    step_summary(
        deps_ok=deps_ok,
        llm_ok=llm_ok,
        tts_ok=tts_ok,
        livekit_ok=livekit_ok,
        test_ok=test_ok,
    )
