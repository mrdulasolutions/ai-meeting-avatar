"""
CLI entry point for ai-meeting-avatar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .diagnostics import LOG_FILE, PID_FILE, RUNTIME_ROOM_FILE, format_state_json, gather_state
from .prefs import set as set_pref

load_dotenv()

logger = logging.getLogger(__name__)


@click.group()
@click.option(
    "--config",
    "-c",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml",
    envvar="AI_AVATAR_CONFIG",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging")
@click.pass_context
def cli(ctx: click.Context, config: str, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


def _save_runtime_pid() -> None:
    PID_FILE.write_text(str(os.getpid()))


def _cleanup_runtime_files() -> None:
    for path in (PID_FILE, RUNTIME_ROOM_FILE):
        if path.exists():
            path.unlink()


def _set_config_value(config_path: str, section: str, key: str, value: str) -> None:
    import re  # noqa: PLC0415

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise click.ClickException(f"config file not found: {config_path}")

    text = cfg_file.read_text()
    pattern = rf"(^{section}:\n(?:^(?:  .*)\n)*)^(\s*{re.escape(key)}:\s*).*$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        raise click.ClickException(f"Could not find {section}.{key} in {config_path}")

    block = match.group(1)
    updated = re.sub(
        rf"^(\s*{re.escape(key)}:\s*).*$",
        rf"\g<1>{value}",
        block,
        flags=re.MULTILINE,
        count=1,
    )
    cfg_file.write_text(text.replace(block, updated, 1))


@cli.command()
@click.option(
    "--brain",
    type=click.Choice(["gemma", "claude"], case_sensitive=False),
    default=None,
    help="Pre-select LLM backend",
)
@click.option("--voice", default=None, help="Pre-select TTS voice ID")
@click.option("--skip-test", is_flag=True, default=False, help="Skip the pipeline test reminder")
@click.option("--skip-avatar", is_flag=True, default=False, help="Skip avatar setup")
@click.option("--yes", "-y", is_flag=True, default=False, help="Auto-confirm prompts")
@click.pass_context
def onboard(
    ctx: click.Context,
    brain: str | None,
    voice: str | None,
    skip_test: bool,
    skip_avatar: bool,
    yes: bool,
) -> None:
    """Run the avatar-first setup flow."""
    from .onboarding import run_onboarding  # noqa: PLC0415

    asyncio.run(
        run_onboarding(
            ctx.obj["config_path"],
            preset_brain=brain,
            preset_voice=voice,
            skip_test=skip_test,
            skip_avatar=skip_avatar,
            auto_confirm=yes,
        )
    )


@cli.command()
@click.argument("room", required=False, default="")
@click.option("--url", envvar="LIVEKIT_URL", help="LiveKit server WebSocket URL")
@click.option("--api-key", envvar="LIVEKIT_API_KEY", help="LiveKit API key")
@click.option("--api-secret", envvar="LIVEKIT_API_SECRET", help="LiveKit API secret")
@click.option("--identity", default="ai-meeting-avatar", show_default=True, help="Participant identity")
@click.pass_context
def join(
    ctx: click.Context,
    room: str,
    url: str | None,
    api_key: str | None,
    api_secret: str | None,
    identity: str,
) -> None:
    """Join a LiveKit room directly as the synced avatar."""
    from .orchestrator import join_room  # noqa: PLC0415

    cfg = load_config(ctx.obj["config_path"])
    console = Console()

    if url:
        cfg.livekit.url = url
    if api_key:
        cfg.livekit.api_key = api_key
    if api_secret:
        cfg.livekit.api_secret = api_secret

    room_name = room or cfg.livekit.room or ""
    if not room_name:
        raise click.ClickException("Room name required. Pass `ai-avatar join <room>` or set livekit.room.")

    state = gather_state(ctx.obj["config_path"])
    if not state.ready_to_join:
        failures = ", ".join(check.key for check in state.checks if not check.ok)
        raise click.ClickException(
            f"Avatar is not ready to join. Run `ai-avatar doctor` first. Missing: {failures}"
        )

    set_pref("last_room", room_name)
    _save_runtime_pid()

    def _handle_signal(signum, frame) -> None:  # type: ignore[unused-argument]
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    console.print(
        Panel.fit(
            f"[bold green]Joining room[/bold green] [cyan]{room_name}[/cyan]\n"
            f"Brain: [bold]{cfg.llm.backend}[/bold]   Voice: [bold]{cfg.tts.voice}[/bold]\n"
            f"Avatar: [bold]{'enabled' if cfg.avatar.enabled else 'disabled'}[/bold]   Renderer: [bold]{cfg.avatar.model}[/bold]",
            title="AI Meeting Avatar",
            border_style="green",
        )
    )

    try:
        asyncio.run(join_room(cfg, room_name, identity=identity))
    except KeyboardInterrupt:
        click.echo("\nAvatar stopped.")
    finally:
        _cleanup_runtime_files()


@cli.command("test-pipeline")
@click.option("--duration", "-d", default=5, show_default=True, help="Recording seconds")
@click.option("--text", "-t", default=None, help="Skip recording and use this text as the STT output")
@click.pass_context
def test_pipeline(ctx: click.Context, duration: int, text: str | None) -> None:
    """Test STT -> LLM -> TTS locally."""
    asyncio.run(_test_pipeline(ctx.obj["config_path"], duration, text))


async def _test_pipeline(config_path: str, duration: int, forced_text: str | None) -> None:
    try:
        import sounddevice as sd  # noqa: PLC0415
    except ImportError:
        click.echo("sounddevice is required for microphone input. pip install sounddevice")
        raise SystemExit(1)

    from .llm import ChatHistory  # noqa: PLC0415
    from .orchestrator import _build_llm  # noqa: PLC0415
    from .stt import WhisperSTT  # noqa: PLC0415
    from .tts import KokoroTTS  # noqa: PLC0415

    console = Console()
    cfg = load_config(config_path)

    stt = WhisperSTT(
        model_size=cfg.stt.model_size,
        device=cfg.stt.device,
        compute_type=cfg.stt.compute_type,
        language=cfg.stt.language,
    )
    llm = _build_llm(cfg)
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

    if forced_text:
        transcript = forced_text
        console.print(f"[bold]Using provided text:[/bold] {transcript}")
    else:
        console.print(f"\n[bold yellow]Recording {duration}s … speak now![/bold yellow]")
        audio = sd.rec(int(duration * 16_000), samplerate=16_000, channels=1, dtype="float32")
        sd.wait()
        transcript = (await stt.transcribe(audio.flatten(), 16_000)).text.strip()
        if not transcript:
            console.print("[red]No speech detected.[/red]")
            return
        console.print(f"\n[bold green]You said:[/bold green] {transcript}")

    console.print("[bold cyan]Generating response …[/bold cyan]")
    history = ChatHistory(system_prompt=cfg.agent.system_prompt)
    reply = await llm.chat(history, transcript)
    console.print(f"\n[bold blue]Response:[/bold blue] {reply}")

    console.print("[bold cyan]Synthesising speech …[/bold cyan]")
    audio_out, sr = await tts.synthesize(reply)
    sd.play(audio_out, sr)
    sd.wait()
    console.print("[bold green]Done![/bold green]")


@cli.command("check-deps")
@click.pass_context
def check_deps(ctx: click.Context) -> None:
    """Quick dependency report."""
    _render_doctor(ctx.obj["config_path"], verbose=False)


@cli.command()
@click.option("--json-output", is_flag=True, help="Emit JSON instead of a table")
@click.pass_context
def doctor(ctx: click.Context, json_output: bool) -> None:
    """Full avatar-first readiness check."""
    if json_output:
        click.echo(format_state_json(ctx.obj["config_path"]))
        return
    ok = _render_doctor(ctx.obj["config_path"], verbose=True)
    if not ok:
        raise SystemExit(1)


def _render_doctor(config_path: str, verbose: bool) -> bool:
    state = gather_state(config_path)
    console = Console()

    table = Table(title="AI Meeting Avatar Doctor")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail")

    status_rows = [
        ("Setup", "[green]complete[/green]" if state.setup_complete else "[yellow]incomplete[/yellow]", ""),
        ("LiveKit", "[green]running[/green]" if state.livekit_running else "[yellow]not detected[/yellow]", state.livekit_url),
        ("Brain", state.backend, "Claude key ready" if state.backend == "claude" and state.anthropic_key_ready else ("Gemma model ready" if state.gemma_ready else "missing dependency")),
        ("Kokoro", "[green]ready[/green]" if state.kokoro_ready else "[red]missing[/red]", state.voice),
        ("Avatar photo", "[green]ready[/green]" if state.photo_ready else "[red]missing[/red]", state.photo_path),
        ("Renderer", "[green]ready[/green]" if state.renderer_ready else "[red]missing[/red]", state.renderer),
        ("Camera output", "[green]ready[/green]" if any(c.key == "camera_output" and c.ok for c in state.checks) else "[red]not ready[/red]", state.camera_output),
        ("Avatar runtime", "[green]running[/green]" if state.avatar_running else "[dim]stopped[/dim]", str(state.avatar_pid or "—")),
    ]

    for name, status, detail in status_rows:
        table.add_row(name, status, detail)

    console.print(table)

    if verbose:
        checks = Table(title="Preflight")
        checks.add_column("Key", style="bold")
        checks.add_column("Result")
        checks.add_column("Summary")
        for check in state.checks:
            checks.add_row(check.key, "[green]OK[/green]" if check.ok else "[red]FAIL[/red]", check.summary)
        console.print(checks)

        if LOG_FILE.exists():
            console.print(f"[dim]Latest log:[/dim] {LOG_FILE}")

    if state.ready_to_join:
        console.print("\n[bold green]Ready to join.[/bold green]")
    else:
        console.print("\n[bold yellow]Not ready to join.[/bold yellow] Fix the failed checks above.")
    return state.ready_to_join


@cli.command("state")
@click.option("--json-output", is_flag=True, help="Emit JSON state")
@click.pass_context
def state_cmd(ctx: click.Context, json_output: bool) -> None:
    """Machine-readable state for the installable skill."""
    state = gather_state(ctx.obj["config_path"])
    if json_output:
        click.echo(json.dumps(state.as_dict(), indent=2))
        return

    click.echo(f"BACKEND={state.backend}")
    click.echo(f"VOICE={state.voice}")
    click.echo(f"READY_TO_JOIN={state.ready_to_join}")
    click.echo(f"AVATAR_RUNNING={state.avatar_running}")
    click.echo(f"ROOM={state.room}")
    click.echo(f"SETUP_COMPLETE={state.setup_complete}")


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
    """Switch the LLM brain."""
    cfg = load_config(ctx.obj["config_path"])
    console = Console()

    if backend is None:
        choice = click.prompt("Choose brain (gemma/claude)", default=cfg.llm.backend)
        backend = str(choice).strip().lower()

    if backend not in {"gemma", "claude"}:
        raise click.ClickException("Brain must be 'gemma' or 'claude'.")

    _set_config_value(ctx.obj["config_path"], "llm", "backend", f'"{backend}"')
    console.print(f"[green]Brain set to[/green] [bold]{backend}[/bold]")


@cli.command("voice")
@click.option("--set", "voice_id", default=None, help="Set voice directly.")
@click.pass_context
def voice(ctx: click.Context, voice_id: str | None) -> None:
    """Show or change the active voice."""
    cfg = load_config(ctx.obj["config_path"])
    if voice_id is None:
        click.echo(f"{cfg.tts.voice} ({cfg.tts.lang})")
        return

    lang = "en-gb" if voice_id.startswith(("bf_", "bm_")) else "en-us"
    _set_config_value(ctx.obj["config_path"], "tts", "voice", f'"{voice_id}"')
    _set_config_value(ctx.obj["config_path"], "tts", "lang", f'"{lang}"')
    set_pref("voice", voice_id)
    click.echo(f"Voice set to {voice_id} ({lang})")


@cli.group()
def avatar() -> None:
    """Manage the video avatar."""


@avatar.command("enable")
@click.pass_context
def avatar_enable(ctx: click.Context) -> None:
    _set_config_value(ctx.obj["config_path"], "avatar", "enabled", "true")
    click.echo("Avatar enabled.")


@avatar.command("disable")
@click.pass_context
def avatar_disable(ctx: click.Context) -> None:
    _set_config_value(ctx.obj["config_path"], "avatar", "enabled", "false")
    click.echo("Avatar disabled.")


@avatar.command("set-photo")
@click.argument("photo_path", type=click.Path(exists=True))
@click.pass_context
def avatar_set_photo(ctx: click.Context, photo_path: str) -> None:
    from .avatar import validate_photo  # noqa: PLC0415

    result = validate_photo(photo_path)
    if not result["valid"]:
        raise click.ClickException(result["error"])

    dest = Path("assets/avatar.jpg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(photo_path, dest)
    _set_config_value(ctx.obj["config_path"], "avatar", "photo_path", f'"{dest}"')
    set_pref("avatar_photo", str(dest))
    click.echo(f"Avatar photo set to {dest}")


@avatar.command("status")
@click.pass_context
def avatar_status(ctx: click.Context) -> None:
    state = gather_state(ctx.obj["config_path"])
    table = Table(title="Avatar Status")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_column("Status")
    table.add_row("Enabled", str(state.avatar_enabled), "[green]ON[/green]" if state.avatar_enabled else "[dim]OFF[/dim]")
    table.add_row("Photo", state.photo_path, "[green]OK[/green]" if state.photo_ready else "[red]missing[/red]")
    table.add_row("Renderer", state.renderer, "[green]ready[/green]" if state.renderer_ready else "[red]missing[/red]")
    table.add_row("Camera", state.camera_output, "[green]ready[/green]" if any(c.key == "camera_output" and c.ok for c in state.checks) else "[red]not ready[/red]")
    table.add_row("Runtime", state.room or "—", "[green]running[/green]" if state.avatar_running else "[dim]stopped[/dim]")
    Console().print(table)


@avatar.command("test")
@click.option("--text", "-t", default="Hello, this is a test of the avatar rendering pipeline.", help="Text to synthesize and render")
@click.pass_context
def avatar_test(ctx: click.Context, text: str) -> None:
    asyncio.run(_avatar_test(ctx.obj["config_path"], text))


async def _avatar_test(config_path: str, text: str) -> None:
    import tempfile  # noqa: PLC0415

    from .avatar import create_renderer, create_virtual_camera, validate_photo  # noqa: PLC0415
    from .tts import KokoroTTS  # noqa: PLC0415

    console = Console()
    cfg = load_config(config_path)

    if not cfg.avatar.enabled:
        console.print("[red]Avatar is disabled.[/red]")
        return

    photo_result = validate_photo(cfg.avatar.photo_path)
    if not photo_result["valid"]:
        console.print(f"[red]Photo invalid:[/red] {photo_result['error']}")
        return

    tts = KokoroTTS(
        voice=cfg.tts.voice,
        speed=cfg.tts.speed,
        lang=cfg.tts.lang,
        model_dir=cfg.tts.model_dir,
    )
    tts.load()
    renderer = create_renderer(cfg.avatar)
    if not renderer.is_available():
        console.print(f"[red]Renderer '{cfg.avatar.model}' is not available.[/red]")
        return
    await renderer.load()

    with tempfile.TemporaryDirectory() as tmp:
        wav_bytes = await tts.synthesize_to_wav_bytes(text)
        audio_path = str(Path(tmp) / "test.wav")
        Path(audio_path).write_bytes(wav_bytes)
        video_path = await renderer.render(audio_path, tmp)
        console.print(f"[green]Rendered:[/green] {video_path}")

        vcam = create_virtual_camera(cfg.avatar)
        if vcam is not None:
            await vcam.start()
            await vcam.stream_video(video_path)
            await vcam.stop()
            console.print("[green]Virtual camera test passed.[/green]")


@cli.command("avatar-setup")
@click.option("--photo", "-p", default=None, help="Path to avatar photo (JPG/PNG)")
@click.pass_context
def avatar_setup(ctx: click.Context, photo: str | None) -> None:
    """Run avatar-specific setup only."""
    from .onboarding import run_avatar_setup_only  # noqa: PLC0415

    asyncio.run(run_avatar_setup_only(ctx.obj["config_path"], photo))


@cli.command("generate-token")
@click.option("--room", required=True, help="Room name")
@click.option("--identity", default="ai-meeting-avatar", show_default=True, help="Participant identity")
@click.pass_context
def generate_token(ctx: click.Context, room: str, identity: str) -> None:
    """Generate a LiveKit participant token."""
    from .orchestrator import create_room_token  # noqa: PLC0415

    cfg = load_config(ctx.obj["config_path"])
    click.echo(
        create_room_token(
            cfg,
            room,
            identity,
            cfg.agent.name,
            hidden=False,
            kind="standard",
        )
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
