"""
CLI entry point for ai-meeting-avatar.

Commands
────────
  ai-avatar onboard              Interactive setup wizard (start here)
  ai-avatar join <room>          Join a LiveKit room as the avatar agent
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
    from .llm import ChatHistory, OllamaLLM  # noqa: PLC0415
    from .stt import WhisperSTT  # noqa: PLC0415
    from .tts import CoquiXTTS  # noqa: PLC0415

    console = Console()
    cfg = load_config(config_path)

    # Initialise components
    stt = WhisperSTT(
        model_size=cfg.stt.model_size,
        device=cfg.stt.device,
        compute_type=cfg.stt.compute_type,
        language=cfg.stt.language,
    )
    llm = OllamaLLM(
        model=cfg.llm.model,
        host=cfg.llm.host,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
        system_prompt=cfg.agent.system_prompt,
    )
    tts = CoquiXTTS(
        model_name=cfg.tts.model,
        speaker_wav=cfg.tts.speaker_wav,
        language=cfg.tts.language,
        gpu=cfg.tts.gpu,
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
        ("ollama", "ollama"),
        ("TTS", "Coqui TTS"),
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
