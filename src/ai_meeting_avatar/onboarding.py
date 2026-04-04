"""
Interactive onboarding wizard for ai-meeting-avatar.

Walks the user through every setup step with live status, voice recording,
and a final pipeline test — no terminal knowledge required.
"""

from __future__ import annotations

import asyncio
import importlib
import os
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
from rich import print as rprint

console = Console()

VOICE_SAMPLE_PATH = Path("assets/voice_samples/speaker.wav")
VOICE_SAMPLE_SECONDS = 15  # seconds to record


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


# ── Individual steps ───────────────────────────────────────────────────────────


def step_welcome() -> None:
    console.print(
        Panel.fit(
            "[bold white]AI Meeting Avatar[/bold white]\n"
            "[dim]Local voice agent for Google Meet & Zoom[/dim]\n\n"
            "This wizard will set everything up in about [bold]5 minutes[/bold].\n"
            "You'll need to speak into your mic once to clone your voice.",
            title="[bold cyan]Welcome[/bold cyan]",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()
    Confirm.ask("  Ready to begin?", default=True)


def step_check_deps() -> bool:
    """Check Python packages and system tools. Return True if all OK."""
    _step(1, 6, "Checking dependencies")

    py_packages = [
        ("faster_whisper", "faster-whisper"),
        ("ollama", "ollama"),
        ("TTS", "TTS (Coqui)"),
        ("livekit", "livekit"),
        ("livekit.agents", "livekit-agents"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("sounddevice", "sounddevice"),
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

    system_tools = ["ffmpeg", "git", "docker"]
    missing_tools = []
    console.print()
    for tool in system_tools:
        if shutil.which(tool):
            _ok(tool)
        else:
            _warn(f"{tool}  [dim](not found — some features may not work)[/dim]")
            missing_tools.append(tool)

    if missing_py:
        console.print()
        _info("Installing missing Python packages …")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _ok("All packages installed.")
        else:
            _fail("pip install failed. Run manually:  pip install -e .")
            console.print(f"  [dim]{result.stderr[-400:]}[/dim]")
            return False

    if "ffmpeg" in missing_tools:
        console.print()
        _warn("ffmpeg is missing. Install it:")
        _info("  macOS:   brew install ffmpeg")
        _info("  Ubuntu:  sudo apt install ffmpeg")

    return True


def step_ollama() -> bool:
    """Verify Ollama is running and pull the model. Return True if OK."""
    _step(2, 6, "LLM — Ollama")

    from .config import load_config  # noqa: PLC0415

    cfg = load_config()
    model = cfg.llm.model
    host = cfg.llm.host

    # Check if ollama CLI exists
    if not shutil.which("ollama"):
        _fail("Ollama is not installed.")
        _info("Download it from [link=https://ollama.com]https://ollama.com[/link]")
        _info("Then run:  ollama serve")
        console.print()
        if not Confirm.ask("  Skip this step for now?", default=True):
            return False
        return True

    # Check if ollama server is reachable
    import urllib.request  # noqa: PLC0415

    try:
        urllib.request.urlopen(f"{host}/api/tags", timeout=3)
        _ok(f"Ollama server reachable at {host}")
    except Exception:
        _fail(f"Ollama server not running at {host}")
        _info("Start it with:  ollama serve")
        console.print()
        _info("Starting Ollama in the background …")
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        try:
            urllib.request.urlopen(f"{host}/api/tags", timeout=5)
            _ok("Ollama server started.")
        except Exception:
            _fail("Could not start Ollama automatically.")
            _info("Open a new terminal and run:  ollama serve")
            Confirm.ask("  Press Enter when Ollama is running", default=True)

    # Pull the model
    console.print()
    _info(f"Pulling model '{model}' (may take a few minutes on first run) …")

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"Pulling {model} …", total=None)
        result = subprocess.run(
            ["ollama", "pull", model],
            capture_output=True,
            text=True,
        )
        progress.stop()

    if result.returncode == 0:
        _ok(f"Model '{model}' ready.")
        return True
    else:
        _fail(f"Could not pull '{model}'.")
        console.print(f"  [dim]{result.stderr[-300:]}[/dim]")
        return False


def step_record_voice() -> bool:
    """Record a voice sample from the microphone. Return True if saved."""
    _step(3, 6, "Voice cloning — record your voice")

    if VOICE_SAMPLE_PATH.exists():
        console.print(
            f"  Voice sample already exists at [cyan]{VOICE_SAMPLE_PATH}[/cyan]"
        )
        if not Confirm.ask("  Re-record it?", default=False):
            _ok("Using existing voice sample.")
            return True

    try:
        import sounddevice as sd  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        import scipy.io.wavfile as wav_io  # noqa: PLC0415
    except ImportError:
        _fail("sounddevice / numpy / scipy not installed.")
        _info("Run:  pip install sounddevice numpy scipy")
        return False

    console.print(
        Panel(
            f"[white]You'll record [bold]{VOICE_SAMPLE_SECONDS} seconds[/bold] of your voice.\n\n"
            "Tips for a good clone:\n"
            "  • Speak in a [bold]quiet room[/bold] — no background noise\n"
            "  • Use your [bold]natural speaking voice[/bold] and pace\n"
            "  • Read anything aloud — a news article, a book, anything\n"
            "  • Stay [bold]15-20 cm from the mic[/bold]",
            title="[bold yellow]Voice Recording Tips[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    console.print()

    sample_text = (
        "The quick brown fox jumps over the lazy dog. "
        "Artificial intelligence is transforming the way we communicate and collaborate. "
        "In today's meeting, I'd like to walk through our quarterly results and discuss "
        "the roadmap for the next six months. Please feel free to ask questions at any time."
    )
    console.print(f"  [dim]Suggested reading:[/dim]\n  [italic]{sample_text}[/italic]")
    console.print()

    Confirm.ask("  Ready? Recording starts immediately when you press Enter", default=True)

    SAMPLE_RATE = 16_000
    recorded_data = None

    with Progress(
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("[cyan]{task.description}"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Recording …", total=VOICE_SAMPLE_SECONDS * 10)

        frames = []

        def audio_callback(indata, frame_count, time_info, status):
            frames.append(indata.copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=audio_callback,
        ):
            for tick in range(VOICE_SAMPLE_SECONDS * 10):
                time.sleep(0.1)
                remaining = VOICE_SAMPLE_SECONDS - tick // 10
                progress.update(
                    task,
                    advance=1,
                    description=f"Recording … {remaining}s left",
                )

        recorded_data = np.concatenate(frames, axis=0).flatten()

    console.print()
    _ok(f"Recorded {len(recorded_data) / SAMPLE_RATE:.1f}s of audio.")

    # Save as WAV (XTTS needs 22050 Hz+; upsample if needed)
    VOICE_SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # XTTS-v2 works best with 22050 Hz
    from scipy.signal import resample as scipy_resample  # noqa: PLC0415

    target_sr = 22_050
    num_samples = int(len(recorded_data) * target_sr / SAMPLE_RATE)
    audio_22k = scipy_resample(recorded_data, num_samples).astype("float32")
    audio_int16 = (audio_22k * 32_767).clip(-32_768, 32_767).astype("int16")

    wav_io.write(str(VOICE_SAMPLE_PATH), target_sr, audio_int16)
    _ok(f"Voice sample saved → [cyan]{VOICE_SAMPLE_PATH}[/cyan]")
    return True


def step_livekit() -> bool:
    """Check if LiveKit is running, offer to start it. Return True if ready."""
    _step(4, 6, "LiveKit server")

    import urllib.request  # noqa: PLC0415

    livekit_url = "http://localhost:7880"
    try:
        urllib.request.urlopen(livekit_url, timeout=2)
        _ok("LiveKit server already running at localhost:7880")
        return True
    except Exception:
        pass

    _info("LiveKit is not running locally.")
    console.print()

    if not shutil.which("docker"):
        _warn("Docker not found — can't auto-start LiveKit.")
        console.print()
        console.print("  [dim]Install Docker, then run:[/dim]")
        console.print(
            "  [bold]docker run --rm -p 7880:7880 -p 7881:7881 "
            '-e LIVEKIT_KEYS="devkey: secret" '
            "livekit/livekit-server --dev[/bold]"
        )
        console.print()
        Confirm.ask("  Press Enter once LiveKit is running", default=True)
        return True

    if Confirm.ask("  Start LiveKit dev server via Docker now?", default=True):
        _info("Starting LiveKit …")
        subprocess.Popen(
            [
                "docker", "run", "--rm",
                "-p", "7880:7880",
                "-p", "7881:7881",
                "-e", "LIVEKIT_KEYS=devkey: secret",
                "livekit/livekit-server",
                "--dev",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(4)
        try:
            urllib.request.urlopen(livekit_url, timeout=5)
            _ok("LiveKit is running at localhost:7880")
            return True
        except Exception:
            _warn("LiveKit may still be starting. Continuing anyway …")
            return True
    else:
        _info("Skipped — start LiveKit before joining a call.")
        return True


def step_pipeline_test() -> bool:
    """Run a quick STT→LLM→TTS test. Return True if it worked."""
    _step(5, 6, "Pipeline test")

    console.print("  Running a quick end-to-end test (this loads all models) …")
    console.print("  [dim]First run may take 2-5 minutes while models cache.[/dim]")
    console.print()

    if not Confirm.ask("  Run the test now? (recommended)", default=True):
        _info("Skipped. Run later with:  ai-avatar test-pipeline --text 'hello'")
        return True

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "ai_meeting_avatar.main",
                "test-pipeline",
                "--text", "Hello! I am your AI meeting avatar. The setup is complete.",
            ],
            timeout=300,
        )
        if result.returncode == 0:
            _ok("Pipeline test passed!")
            return True
        else:
            _warn("Pipeline test had issues — check output above.")
            return False
    except subprocess.TimeoutExpired:
        _warn("Test timed out (models may still be downloading). Try again with:")
        _info("  ai-avatar test-pipeline --text 'hello'")
        return False
    except Exception as e:
        _fail(f"Test failed: {e}")
        return False


def step_summary(voice_ok: bool, ollama_ok: bool, livekit_ok: bool, test_ok: bool) -> None:
    """Print final summary and next steps."""
    _step(6, 6, "You're all set!")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Status", style="bold", width=4)
    table.add_column("Component")

    def row(ok: bool, label: str):
        icon = "[green]✓[/green]" if ok else "[yellow]![/yellow]"
        table.add_row(icon, label)

    row(True, "Python dependencies")
    row(ollama_ok, f"Ollama LLM")
    row(voice_ok, "Voice sample recorded")
    row(livekit_ok, "LiveKit server")
    row(test_ok, "Pipeline smoke test")

    console.print(table)
    console.print()

    console.print(
        Panel(
            "[bold white]To join a meeting:[/bold white]\n\n"
            "  1. Start LiveKit (if not already running):\n"
            "     [cyan]docker run --rm -p 7880:7880 -p 7881:7881 \\\n"
            '       -e LIVEKIT_KEYS="devkey: secret" \\\n'
            "       livekit/livekit-server --dev[/cyan]\n\n"
            "  2. Join a room:\n"
            "     [cyan]ai-avatar join my-meeting[/cyan]\n\n"
            "  3. Connect your Google Meet / Zoom audio via BlackHole or similar\n"
            "     (see README.md → Google Meet / Zoom integration)\n\n"
            "[dim]Run  ai-avatar --help  for all commands.[/dim]",
            title="[bold green]Next Steps[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )


# ── Main entry ─────────────────────────────────────────────────────────────────


async def run_onboarding() -> None:
    """Run the full interactive onboarding wizard."""
    step_welcome()

    deps_ok = step_check_deps()
    if not deps_ok:
        _fail("Dependency check failed. Fix the issues above and re-run:  ai-avatar onboard")
        sys.exit(1)

    ollama_ok = step_ollama()
    voice_ok = step_record_voice()
    livekit_ok = step_livekit()
    test_ok = step_pipeline_test()

    step_summary(
        voice_ok=voice_ok,
        ollama_ok=ollama_ok,
        livekit_ok=livekit_ok,
        test_ok=test_ok,
    )
