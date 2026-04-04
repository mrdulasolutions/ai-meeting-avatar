"""
CLI entry point for ai-meeting-avatar.

Commands
────────
  ai-avatar onboard              Interactive setup wizard (start here)
  ai-avatar join <room>          Join a LiveKit room as the avatar agent
  ai-avatar avatar-setup         Set up Phase 2 talking avatar (photo + models)
  ai-avatar test-pipeline        Record 5 s from mic and run STT → LLM → TTS locally
  ai-avatar check-deps           Verify all Python deps and binaries are present
  ai-avatar generate-token       Generate a LiveKit participant token
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

# Load .env file if present so credentials are available before config parsing
load_dotenv()


# ── CLI root ───────────────────────────────────────────────────────────────────


@click.group()
@click.option(
    "--config", "-c",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml",
    envvar="AI_AVATAR_CONFIG",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging")
@click.pass_context
def cli(ctx: click.Context, config: str, verbose: bool) -> None:
    """AI Meeting Avatar — local voice + avatar agent for Google Meet / Zoom.

    New here? Run:  ai-avatar onboard
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ── onboard ────────────────────────────────────────────────────────────────────


@cli.command()
def onboard() -> None:
    """Interactive setup wizard — run this first."""
    from .onboarding import run_onboarding  # noqa: PLC0415

    asyncio.run(run_onboarding())


# ── join ───────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("room", required=False, default="")
@click.option("--url", envvar="LIVEKIT_URL", help="LiveKit server WebSocket URL")
@click.option("--api-key", envvar="LIVEKIT_API_KEY", help="LiveKit API key")
@click.option("--api-secret", envvar="LIVEKIT_API_SECRET", help="LiveKit API secret")
@click.pass_context
def join(
    ctx: click.Context,
    room: str,
    url: str | None,
    api_key: str | None,
    api_secret: str | None,
) -> None:
    """
    Join a LiveKit room as the avatar agent.

    ROOM is the room name to join. Overrides config.yaml livekit.room.

    Example:
        ai-avatar join my-meeting --url ws://localhost:7880
    """
    from livekit.agents import WorkerOptions, cli as agent_cli  # noqa: PLC0415

    from .config import load_config  # noqa: PLC0415
    from .orchestrator import entrypoint  # noqa: PLC0415

    config_path = ctx.obj["config_path"]
    cfg = load_config(config_path)

    # CLI flags override config / env
    if url:
        os.environ["LIVEKIT_URL"] = url
        cfg.livekit.url = url
    if api_key:
        os.environ["LIVEKIT_API_KEY"] = api_key
        cfg.livekit.api_key = api_key
    if api_secret:
        os.environ["LIVEKIT_API_SECRET"] = api_secret
        cfg.livekit.api_secret = api_secret
    if room:
        cfg.livekit.room = room

    # Expose config path for the entrypoint coroutine
    os.environ["AI_AVATAR_CONFIG"] = str(Path(config_path).resolve())

    click.echo(
        f"Joining room '{cfg.livekit.room or '(auto)'}' at {cfg.livekit.url} …"
    )

    # livekit-agents uses Typer and re-parses sys.argv directly.
    # Replace argv so it sees its own 'start' subcommand rather than ours.
    sys.argv = [
        "ai-avatar",
        "start",
        "--url", cfg.livekit.url,
        "--api-key", cfg.livekit.api_key,
        "--api-secret", cfg.livekit.api_secret,
    ]
    agent_cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


# ── test-pipeline ──────────────────────────────────────────────────────────────


@cli.command("test-pipeline")
@click.option("--duration", "-d", default=5, show_default=True, help="Recording seconds")
@click.option(
    "--text", "-t",
    default=None,
    help="Skip recording and use this text as the STT output",
)
@click.pass_context
def test_pipeline(ctx: click.Context, duration: int, text: str | None) -> None:
    """
    Test STT → LLM → TTS locally (no LiveKit needed).

    Records from the default microphone for DURATION seconds (unless --text
    is given), runs the pipeline, and plays back the synthesised response.
    """
    asyncio.run(_test_pipeline(ctx.obj["config_path"], duration, text))


async def _test_pipeline(config_path: str, duration: int, forced_text: str | None) -> None:
    try:
        import sounddevice as sd  # noqa: PLC0415
    except ImportError:
        click.echo("sounddevice is required for microphone input. pip install sounddevice")
        sys.exit(1)

    from rich.console import Console  # noqa: PLC0415

    from .config import load_config  # noqa: PLC0415
    from .llm import ChatHistory, GemmaLLM  # noqa: PLC0415
    from .stt import WhisperSTT  # noqa: PLC0415
    from .tts import KokoroTTS  # noqa: PLC0415

    console = Console()
    cfg = load_config(config_path)

    # Initialise components
    stt = WhisperSTT(
        model_size=cfg.stt.model_size,
        device=cfg.stt.device,
        compute_type=cfg.stt.compute_type,
        language=cfg.stt.language,
    )
    llm = GemmaLLM(
        model_path=cfg.llm.model_path,
        system_prompt=cfg.agent.system_prompt,
        enable_tools=cfg.llm.enable_tools,
        max_tokens=cfg.llm.max_tokens,
        temperature=cfg.llm.temperature,
    )
    tts = KokoroTTS(
        voice=cfg.tts.voice,
        speed=cfg.tts.speed,
        lang=cfg.tts.lang,
        model_dir=cfg.tts.model_dir,
    )

    console.print("[bold cyan]Loading models …[/bold cyan]")
    stt.load()
    llm.load()
    tts.load()

    # ── STT ────────────────────────────────────────────────────────────────────
    if forced_text:
        transcript = forced_text
        console.print(f"[bold]Using provided text:[/bold] {transcript}")
    else:
        import numpy as np  # noqa: PLC0415

        console.print(
            f"\n[bold yellow]Recording {duration}s … speak now![/bold yellow]"
        )
        audio = sd.rec(
            int(duration * 16_000),
            samplerate=16_000,
            channels=1,
            dtype="float32",
        )
        sd.wait()
        audio = audio.flatten()

        console.print("[bold cyan]Transcribing …[/bold cyan]")
        result = await stt.transcribe(audio, 16_000)
        transcript = result.text.strip()

        if not transcript:
            console.print("[red]No speech detected. Try again with --duration or --text.[/red]")
            return

        console.print(f"\n[bold green]You said:[/bold green] {transcript}")

    # ── LLM ────────────────────────────────────────────────────────────────────
    console.print("[bold cyan]Generating response …[/bold cyan]")
    history = ChatHistory(system_prompt=cfg.agent.system_prompt)
    reply = await llm.chat(history, transcript)
    console.print(f"\n[bold blue]Response:[/bold blue] {reply}")

    # ── TTS ────────────────────────────────────────────────────────────────────
    console.print("[bold cyan]Synthesising speech …[/bold cyan]")
    audio_out, sr = await tts.synthesize(reply)

    console.print("[bold green]Playing …[/bold green]")
    sd.play(audio_out, sr)
    sd.wait()
    console.print("[bold green]Done![/bold green]")


# ── check-deps ─────────────────────────────────────────────────────────────────


@cli.command("check-deps")
def check_deps() -> None:
    """Verify all Python dependencies and external tools are available."""
    import importlib  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from rich.console import Console  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    console = Console()

    py_deps = [
        ("faster_whisper", "faster-whisper"),
        ("litert_lm", "litert-lm-nightly"),
        ("kokoro_onnx", "kokoro-onnx"),
        ("onnxruntime", "onnxruntime"),
        ("livekit", "livekit"),
        ("livekit.agents", "livekit-agents"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("sounddevice", "sounddevice"),
        ("yaml", "PyYAML"),
        ("pydantic", "pydantic"),
        ("rich", "rich"),
    ]

    table = Table(title="Python Dependencies")
    table.add_column("Package")
    table.add_column("Status")

    all_ok = True
    for module, name in py_deps:
        try:
            importlib.import_module(module)
            table.add_row(name, "[green]OK[/green]")
        except ImportError:
            table.add_row(name, "[red]MISSING[/red]")
            all_ok = False

    console.print(table)

    # External tools
    tools = ["ffmpeg", "git"]
    tool_table = Table(title="External Tools")
    tool_table.add_column("Tool")
    tool_table.add_column("Status")

    for tool in tools:
        if shutil.which(tool):
            tool_table.add_row(tool, "[green]found[/green]")
        else:
            tool_table.add_row(tool, "[yellow]not found[/yellow]")

    console.print(tool_table)

    if not all_ok:
        console.print("\n[red]Install missing packages:[/red] pip install -e .")
        sys.exit(1)
    else:
        console.print("\n[bold green]All dependencies satisfied![/bold green]")


# ── avatar-setup ──────────────────────────────────────────────────────────────


@cli.command("avatar-setup")
@click.option("--photo", "-p", default=None, help="Path to avatar photo (JPG/PNG)")
@click.pass_context
def avatar_setup(ctx: click.Context, photo: str | None) -> None:
    """
    Set up Phase 2 talking avatar — photo validation, model download, deps.

    This is the same setup that runs during onboarding, but standalone
    so you can add the avatar after initial setup.

    \b
    Examples:
        ai-avatar avatar-setup                       # interactive
        ai-avatar avatar-setup --photo ./me.jpg      # specify photo directly
    """
    import importlib  # noqa: PLC0415
    import re  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    from rich.console import Console as RConsole  # noqa: PLC0415
    from rich.prompt import Confirm as RConfirm  # noqa: PLC0415
    from rich.prompt import Prompt as RPrompt  # noqa: PLC0415

    from .avatar import validate_photo  # noqa: PLC0415
    from .config import load_config  # noqa: PLC0415

    rcon = RConsole()
    config_path = ctx.obj["config_path"]
    cfg = load_config(config_path)

    # ── Photo ─────────────────────────────────────────────────────────────────
    if photo is None:
        photo = RPrompt.ask(
            "Path to your avatar photo (JPG/PNG)",
            default=cfg.avatar.photo_path,
        )

    result = validate_photo(photo)
    if not result["valid"]:
        rcon.print(f"[red]Photo issue: {result['error']}[/red]")
        sys.exit(1)

    rcon.print(
        f"[green]Photo valid:[/green] {result['width']}x{result['height']}, "
        f"{result['faces']} face(s) detected"
    )

    # Update config.yaml
    cfg_file = Path(config_path)
    if cfg_file.exists():
        text = cfg_file.read_text()
        text = re.sub(
            r"^(\s*photo_path:\s*).*$",
            rf'\1"{photo}"',
            text,
            flags=re.MULTILINE,
        )
        text = re.sub(
            r"^(\s*enabled:\s*)(?:true|false)\b",
            r"\1true",
            text,
            flags=re.MULTILINE,
            count=1,
        )
        cfg_file.write_text(text)
        rcon.print("[green]Avatar enabled in config.yaml[/green]")

    # ── Check deps ────────────────────────────────────────────────────────────
    missing = []
    for module, name in [("torch", "torch"), ("cv2", "opencv-python"), ("pyvirtualcam", "pyvirtualcam")]:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(name)

    if missing:
        rcon.print(f"\n[yellow]Missing avatar deps: {', '.join(missing)}[/yellow]")
        if RConfirm.ask("Install now?", default=True):
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", ".[avatar]", "-q"],
            )
        else:
            rcon.print("Install later:  [cyan]pip install -e '.[avatar]'[/cyan]")

    # ── Check SadTalker ──────────────────────────────────────────────────────
    sadtalker_path = Path(cfg.avatar.sadtalker_path)
    if not sadtalker_path.exists() or not (sadtalker_path / "inference.py").exists():
        rcon.print("\n[yellow]SadTalker not found.[/yellow]")
        rcon.print("Download it:  [cyan]./scripts/setup_models.sh[/cyan]")
    else:
        rcon.print("[green]SadTalker model found.[/green]")

    rcon.print(
        "\n[bold green]Avatar setup complete.[/bold green]\n"
        "Restart the avatar to see it:  [cyan]ai-avatar join <room>[/cyan]"
    )


# ── brain ──────────────────────────────────────────────────────────────────────


@cli.command("brain")
@click.option(
    "--set",
    "backend",
    type=click.Choice(["gemma", "claude"], case_sensitive=False),
    default=None,
    help="Set backend directly without prompting.",
)
@click.pass_context
def brain(ctx: click.Context, backend: str | None) -> None:
    """
    Switch the LLM brain between local Gemma 4 and cloud Claude.

    Updates llm.backend in config.yaml.  The change takes effect on the
    next  ai-avatar join  or  ai-avatar test-pipeline  invocation.

    \b
    Examples:
        ai-avatar brain               # interactive menu
        ai-avatar brain --set claude  # switch to Claude directly
        ai-avatar brain --set gemma   # switch back to Gemma
    """
    import re  # noqa: PLC0415

    from rich.console import Console as RConsole  # noqa: PLC0415
    from rich.panel import Panel as RPanel  # noqa: PLC0415
    from rich.prompt import Prompt as RPrompt  # noqa: PLC0415

    from .config import load_config  # noqa: PLC0415

    rcon = RConsole()
    config_path = ctx.obj["config_path"]
    cfg = load_config(config_path)
    current = cfg.llm.backend

    if backend is None:
        rcon.print(
            RPanel(
                f"[white]Current brain: [bold cyan]{current}[/bold cyan]\n\n"
                "  [bold]1) gemma[/bold]  — Local Gemma 4 (offline, private, no API key)\n"
                "  [bold]2) claude[/bold] — Anthropic Claude (smarter, needs internet + API key)",
                title="[bold yellow]Choose AI Brain[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        rcon.print()
        choice = RPrompt.ask(
            "  Select brain", choices=["1", "2", "gemma", "claude"], default="1"
        )
        backend = "claude" if choice in ("2", "claude") else "gemma"

    if backend == current:
        click.echo(f"Already using '{backend}' — no change.")
        return

    # Update config.yaml
    import pathlib  # noqa: PLC0415

    cfg_file = pathlib.Path(config_path)
    if cfg_file.exists():
        text = cfg_file.read_text()
        new_text = re.sub(
            r"^(\s*backend:\s*).*$",
            rf'\1"{backend}"',
            text,
            flags=re.MULTILINE,
        )
        cfg_file.write_text(new_text)
        click.echo(f"Switched brain: {current} → {backend}  (saved to {config_path})")
    else:
        click.echo(f"config.yaml not found at '{config_path}'.")
        return

    # Remind about Claude-specific requirements
    if backend == "claude":
        rcon.print(
            "\n[yellow]Remember:[/yellow]\n"
            "  • Set your API key:  [cyan]export ANTHROPIC_API_KEY=sk-ant-...[/cyan]\n"
            "  • Or add it to .env: [cyan]ANTHROPIC_API_KEY=sk-ant-...[/cyan]\n"
            "  • Install the SDK:   [cyan]pip install -e '.[claude]'[/cyan]\n"
        )
    else:
        rcon.print(
            "\n[yellow]Remember:[/yellow]\n"
            "  • Gemma model must be downloaded — run  [cyan]ai-avatar onboard[/cyan]  if not yet done.\n"
        )

    rcon.print("[bold green]Restart[/bold green] the avatar to apply the change.")


# ── avatar ────────────────────────────────────────────────────────────────────


@cli.group()
def avatar() -> None:
    """Manage the lip-sync avatar (Phase 2).

    \b
    Commands:
        ai-avatar avatar enable       Enable avatar video rendering
        ai-avatar avatar disable      Disable avatar (audio-only mode)
        ai-avatar avatar set-photo    Set the avatar source photo
        ai-avatar avatar test         Test avatar rendering pipeline
        ai-avatar avatar status       Show current avatar configuration
    """


@avatar.command("enable")
@click.pass_context
def avatar_enable(ctx: click.Context) -> None:
    """Enable avatar video rendering in config.yaml."""
    _set_avatar_config(ctx.obj["config_path"], "enabled", "true")
    click.echo("Avatar enabled. Next `ai-avatar join` will render lip-sync video.")
    click.echo("Make sure you have:")
    click.echo("  1. pip install -e '.[avatar]'")
    click.echo("  2. A photo at assets/avatar.jpg (or set avatar.photo_path)")
    click.echo("  3. SadTalker models (./scripts/setup_models.sh)")


@avatar.command("disable")
@click.pass_context
def avatar_disable(ctx: click.Context) -> None:
    """Disable avatar — return to audio-only mode."""
    _set_avatar_config(ctx.obj["config_path"], "enabled", "false")
    click.echo("Avatar disabled. Running in audio-only mode.")


@avatar.command("set-photo")
@click.argument("photo_path", type=click.Path(exists=True))
@click.pass_context
def avatar_set_photo(ctx: click.Context, photo_path: str) -> None:
    """Set the avatar source photo (JPG/PNG, front-facing).

    \b
    Example:
        ai-avatar avatar set-photo ~/Pictures/headshot.jpg
    """
    from .avatar import validate_photo  # noqa: PLC0415

    result = validate_photo(photo_path)
    if not result["valid"]:
        click.echo(f"Photo validation failed: {result['error']}")
        raise SystemExit(1)

    # Copy to assets/avatar.jpg
    import shutil  # noqa: PLC0415

    dest = Path("assets/avatar.jpg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(photo_path, dest)

    _set_avatar_config(ctx.obj["config_path"], "photo_path", f'"{dest}"')
    click.echo(
        f"Avatar photo set: {dest} "
        f"({result['width']}×{result['height']}, {result['faces']} face(s) detected)"
    )


@avatar.command("test")
@click.option("--text", "-t", default="Hello, this is a test of the avatar rendering pipeline.",
              help="Text to synthesize and render")
@click.pass_context
def avatar_test(ctx: click.Context, text: str) -> None:
    """Test the full avatar rendering pipeline (TTS → renderer → virtual camera)."""
    asyncio.run(_avatar_test(ctx.obj["config_path"], text))


async def _avatar_test(config_path: str, text: str) -> None:
    import tempfile  # noqa: PLC0415

    from rich.console import Console  # noqa: PLC0415

    from .avatar import create_renderer, create_virtual_camera, validate_photo  # noqa: PLC0415
    from .config import load_config  # noqa: PLC0415
    from .tts import KokoroTTS  # noqa: PLC0415

    console = Console()
    cfg = load_config(config_path)

    if not cfg.avatar.enabled:
        console.print("[red]Avatar is disabled.[/red] Run: ai-avatar avatar enable")
        return

    # Validate photo
    console.print("[bold cyan]Checking avatar photo …[/bold cyan]")
    photo_result = validate_photo(cfg.avatar.photo_path)
    if not photo_result["valid"]:
        console.print(f"[red]Photo invalid:[/red] {photo_result['error']}")
        return
    console.print(
        f"  Photo OK: {photo_result['width']}×{photo_result['height']}, "
        f"{photo_result['faces']} face(s)"
    )

    # TTS
    console.print("[bold cyan]Synthesizing speech …[/bold cyan]")
    tts = KokoroTTS(
        voice=cfg.tts.voice,
        speed=cfg.tts.speed,
        lang=cfg.tts.lang,
        model_dir=cfg.tts.model_dir,
    )
    tts.load()
    wav_bytes = await tts.synthesize_to_wav_bytes(text)
    console.print(f"  Audio: {len(wav_bytes)} bytes")

    # Renderer
    console.print("[bold cyan]Loading avatar renderer …[/bold cyan]")
    renderer = create_renderer(cfg.avatar)
    if not renderer.is_available():
        console.print(
            f"[red]Renderer '{cfg.avatar.model}' is not available.[/red]\n"
            "  Run: ./scripts/setup_models.sh"
        )
        return

    await renderer.load()
    console.print(f"  Renderer: {cfg.avatar.model}")

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = str(Path(tmp) / "test.wav")
        Path(audio_path).write_bytes(wav_bytes)

        console.print("[bold cyan]Rendering avatar video …[/bold cyan]")
        console.print("  [dim]This may take 30-120 seconds on CPU …[/dim]")
        video_path = await renderer.render(audio_path, tmp)
        console.print(f"  Video: {video_path}")

        # Try virtual camera
        vcam = create_virtual_camera(cfg.avatar)
        if vcam is not None:
            console.print("[bold cyan]Testing virtual camera …[/bold cyan]")
            try:
                await vcam.start()
                await vcam.stream_video(video_path)
                await vcam.stop()
                console.print("  [bold green]Virtual camera test passed![/bold green]")
            except Exception as exc:
                console.print(f"  [yellow]Virtual camera test failed: {exc}[/yellow]")
        else:
            console.print("  [dim]Virtual camera not configured (camera_output=none)[/dim]")

        # Play audio
        try:
            import sounddevice as sd  # noqa: PLC0415

            audio_float, sr = await tts.synthesize(text)
            console.print("[bold cyan]Playing audio …[/bold cyan]")
            sd.play(audio_float, sr)
            sd.wait()
        except ImportError:
            console.print("  [dim]sounddevice not installed — skipping playback[/dim]")

    console.print("[bold green]Avatar test complete![/bold green]")


@avatar.command("status")
@click.pass_context
def avatar_status(ctx: click.Context) -> None:
    """Show current avatar configuration and readiness."""
    from rich.console import Console as RConsole  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    from .avatar import validate_photo  # noqa: PLC0415
    from .config import load_config  # noqa: PLC0415

    cfg = load_config(ctx.obj["config_path"])
    rcon = RConsole()

    table = Table(title="Avatar Configuration (Phase 2)")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_column("Status")

    # Enabled
    table.add_row(
        "Enabled",
        str(cfg.avatar.enabled),
        "[green]ON[/green]" if cfg.avatar.enabled else "[dim]OFF[/dim]",
    )

    # Photo
    photo = validate_photo(cfg.avatar.photo_path) if cfg.avatar.enabled else {"valid": False}
    photo_status = "[green]OK[/green]" if photo.get("valid") else "[red]MISSING[/red]"
    table.add_row("Photo", cfg.avatar.photo_path, photo_status)

    # Renderer
    renderer_path = (
        cfg.avatar.sadtalker_path if cfg.avatar.model == "sadtalker"
        else cfg.avatar.liveportrait_path
    )
    renderer_exists = Path(renderer_path).exists()
    table.add_row(
        "Renderer",
        cfg.avatar.model,
        "[green]installed[/green]" if renderer_exists else "[red]not found[/red]",
    )

    # Camera output
    table.add_row("Camera output", cfg.avatar.camera_output, "")

    # Resolution
    table.add_row("Resolution", f"{cfg.avatar.render_width}×{cfg.avatar.render_height}", "")

    # Device
    table.add_row("Device", cfg.avatar.device, "")

    # Enhancer
    table.add_row("Enhancer", str(cfg.avatar.enhancer or "none"), "")

    # pyvirtualcam
    try:
        import pyvirtualcam  # noqa: PLC0415, F401
        vcam_status = "[green]installed[/green]"
    except ImportError:
        vcam_status = "[red]not installed[/red]"
    table.add_row("pyvirtualcam", "", vcam_status)

    # Avatar deps
    try:
        import cv2  # noqa: PLC0415, F401
        import torch  # noqa: PLC0415, F401
        deps_status = "[green]OK[/green]"
    except ImportError:
        deps_status = "[yellow]pip install -e '.[avatar]'[/yellow]"
    table.add_row("Avatar deps", "", deps_status)

    rcon.print(table)

    if not cfg.avatar.enabled:
        rcon.print("\n[dim]Enable with:[/dim]  ai-avatar avatar enable")


def _set_avatar_config(config_path: str, key: str, value: str) -> None:
    """Update a single avatar.KEY in config.yaml via regex."""
    import re  # noqa: PLC0415

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        click.echo(f"config.yaml not found at '{config_path}'.")
        raise SystemExit(1)

    text = cfg_file.read_text()
    pattern = rf"^(\s*{re.escape(key)}:\s*).*$"
    new_text = re.sub(pattern, rf"\g<1>{value}", text, flags=re.MULTILINE)

    if new_text == text:
        click.echo(f"Warning: key '{key}' not found in {config_path}")
    else:
        cfg_file.write_text(new_text)


# ── generate-token ─────────────────────────────────────────────────────────────


@cli.command("generate-token")
@click.option("--room", required=True, help="Room name")
@click.option("--identity", default="avatar-agent", show_default=True, help="Participant identity")
@click.pass_context
def generate_token(ctx: click.Context, room: str, identity: str) -> None:
    """Generate a LiveKit participant access token (useful for testing)."""
    from livekit.api import AccessToken, VideoGrants  # noqa: PLC0415

    from .config import load_config  # noqa: PLC0415

    cfg = load_config(ctx.obj["config_path"])
    token = (
        AccessToken(cfg.livekit.api_key, cfg.livekit.api_secret)
        .with_grants(VideoGrants(room_join=True, room=room))
        .with_identity(identity)
        .to_jwt()
    )
    click.echo(token)


# ── entry ──────────────────────────────────────────────────────────────────────


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
