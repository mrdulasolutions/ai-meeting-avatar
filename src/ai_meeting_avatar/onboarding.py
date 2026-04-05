"""
Avatar-first onboarding flow.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

from .diagnostics import gather_state
from .prefs import set as set_pref

console = Console()


def _set_config_value(config_path: str, section: str, key: str, value: str) -> None:
    import re  # noqa: PLC0415

    cfg_file = Path(config_path)
    text = cfg_file.read_text()
    pattern = rf"(^{section}:\n(?:^(?:  .*)\n)*)^(\s*{key}:\s*).*$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find {section}.{key} in {config_path}")

    block = match.group(1)
    updated = re.sub(
        rf"^(\s*{key}:\s*).*$",
        rf"\g<1>{value}",
        block,
        flags=re.MULTILINE,
        count=1,
    )
    cfg_file.write_text(text.replace(block, updated, 1))


def _step(title: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]"))


def _check_python_version() -> None:
    v = sys.version_info
    if not (3, 11) <= (v.major, v.minor) <= (3, 13):
        raise SystemExit(
            f"Python {v.major}.{v.minor} is not supported. Use Python 3.11–3.13."
        )


def _check_python_packages(packages: list[tuple[str, str]]) -> list[str]:
    missing = []
    for module, package in packages:
        try:
            importlib.import_module(module)
            console.print(f"[green]✓[/green] {package}")
        except ImportError:
            console.print(f"[yellow]![/yellow] {package} missing")
            missing.append(package)
    return missing


def _install_package(extra: str = "") -> bool:
    target = f".{extra}"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", target, "-q"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _choose_brain(config_path: str, preset_brain: str | None = None, auto_confirm: bool = False) -> str:
    _step("Step 1/5 - Choose Brain")
    if preset_brain:
        backend = preset_brain.lower()
    elif auto_confirm:
        backend = "claude"
    else:
        console.print(
            Panel(
                "[bold]1) Gemma[/bold] - local, private, larger setup\n"
                "[bold]2) Claude[/bold] - cloud, faster to get working",
                title="AI Brain",
                border_style="yellow",
            )
        )
        choice = Prompt.ask("Pick brain", choices=["1", "2"], default="2")
        backend = "claude" if choice == "2" else "gemma"

    _set_config_value(config_path, "llm", "backend", f'"{backend}"')
    return backend


def _choose_voice(config_path: str, preset_voice: str | None = None, auto_confirm: bool = False) -> str:
    _step("Step 2/5 - Choose Voice")
    voices = [
        ("1", "af_heart", "warm female"),
        ("2", "af_sky", "bright female"),
        ("3", "af_nova", "expressive female"),
        ("4", "am_adam", "natural male"),
        ("5", "am_michael", "deep male"),
        ("6", "bf_emma", "British female"),
        ("7", "bm_george", "British male"),
    ]

    if preset_voice:
        voice = preset_voice
    elif auto_confirm:
        voice = "af_heart"
    else:
        table = Table(title="Voices")
        table.add_column("#")
        table.add_column("Voice")
        table.add_column("Style")
        for row in voices:
            table.add_row(*row)
        console.print(table)
        selected = Prompt.ask("Pick voice", choices=[row[0] for row in voices], default="1")
        voice = next(row[1] for row in voices if row[0] == selected)

    lang = "en-gb" if voice.startswith(("bf_", "bm_")) else "en-us"
    _set_config_value(config_path, "tts", "voice", f'"{voice}"')
    _set_config_value(config_path, "tts", "lang", f'"{lang}"')
    set_pref("voice", voice)
    return voice


def _setup_brain(config_path: str, backend: str, auto_confirm: bool = False) -> bool:
    _step("Step 3/5 - Configure Brain")
    env_path = Path(config_path).resolve().parent / ".env"

    if backend == "claude":
        api_key = ""
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

        if not api_key and not auto_confirm:
            api_key = Prompt.ask("Paste your Anthropic API key", password=True)

        if api_key:
            lines = env_path.read_text().splitlines() if env_path.exists() else []
            lines = [line for line in lines if not line.startswith("ANTHROPIC_API_KEY=")]
            lines.append(f"ANTHROPIC_API_KEY={api_key}")
            env_path.write_text("\n".join(lines) + "\n")

        if importlib.util.find_spec("anthropic") is None:
            console.print("[cyan]Installing Claude dependencies...[/cyan]")
            if not _install_package("[claude]"):
                console.print("[red]Could not install Claude dependencies.[/red]")
                return False
        return True

    model_path = Path("models/gemma-4-e2b")
    if any(model_path.glob("*.litertlm")):
        console.print("[green]Gemma model already present.[/green]")
        return True

    console.print(
        "[yellow]Gemma model not found.[/yellow] Run [cyan]./scripts/setup_models.sh[/cyan] after onboarding."
    )
    return False


def _setup_avatar(config_path: str, photo: str | None = None, auto_confirm: bool = False) -> bool:
    _step("Step 4/5 - Avatar Setup")
    from .avatar import validate_photo  # noqa: PLC0415

    if photo:
        photo_path = photo
    elif auto_confirm:
        photo_path = "./assets/avatar.jpg"
    else:
        photo_path = Prompt.ask("Path to avatar photo", default="./assets/avatar.jpg")

    result = validate_photo(photo_path)
    if not result["valid"]:
        console.print(f"[red]Photo invalid:[/red] {result['error']}")
        return False

    _set_config_value(config_path, "avatar", "photo_path", f'"{photo_path}"')
    _set_config_value(config_path, "avatar", "enabled", "true")
    set_pref("avatar_photo", photo_path)

    missing = _check_python_packages(
        [("torch", "torch"), ("cv2", "opencv-python"), ("pyvirtualcam", "pyvirtualcam")]
    )
    if missing:
        should_install = True if auto_confirm else Confirm.ask(
            "Install avatar dependencies now?", default=True
        )
        if should_install and not _install_package("[avatar]"):
            console.print("[red]Could not install avatar dependencies.[/red]")
            return False

    return True


def _run_doctor_summary(config_path: str) -> bool:
    _step("Step 5/5 - Readiness")
    state = gather_state(config_path)
    table = Table(title="Avatar Readiness")
    table.add_column("Check", style="bold")
    table.add_column("Result")
    for check in state.checks:
        table.add_row(check.summary, "[green]OK[/green]" if check.ok else "[yellow]Needs attention[/yellow]")
    console.print(table)
    return state.ready_to_join


async def run_avatar_setup_only(config_path: str, photo: str | None) -> None:
    _check_python_version()
    _setup_avatar(config_path, photo)
    _run_doctor_summary(config_path)


async def run_onboarding(
    config_path: str = "config.yaml",
    *,
    preset_brain: str | None = None,
    preset_voice: str | None = None,
    skip_test: bool = False,
    skip_avatar: bool = False,
    auto_confirm: bool = False,
) -> None:
    _check_python_version()
    console.print(
        Panel.fit(
            "[bold white]AI Meeting Avatar[/bold white]\n"
            "Set up a synced meeting avatar with one reliable room-join path.\n"
            "[dim]Requires Python 3.11–3.13[/dim]",
            title="Welcome",
            border_style="cyan",
        )
    )
    if not auto_confirm and not Confirm.ask("Start setup?", default=True):
        return

    _step("Step 0/5 - Base Dependencies")
    missing = _check_python_packages(
        [
            ("rich", "rich"),
            ("yaml", "PyYAML"),
            ("faster_whisper", "faster-whisper"),
            ("kokoro_onnx", "kokoro-onnx"),
            ("livekit", "livekit"),
        ]
    )
    if missing:
        should_install = True if auto_confirm else Confirm.ask(
            "Install missing base dependencies now?", default=True
        )
        if should_install and not _install_package():
            console.print("[red]Base dependency install failed.[/red]")
            raise SystemExit(1)

    backend = _choose_brain(config_path, preset_brain, auto_confirm)
    _choose_voice(config_path, preset_voice, auto_confirm)
    _setup_brain(config_path, backend, auto_confirm)
    if not skip_avatar:
        _setup_avatar(config_path, None, auto_confirm)
    ready = _run_doctor_summary(config_path)
    if not skip_test:
        console.print("[dim]Run `ai-avatar test-pipeline --text \"hello\"` after setup to confirm the speech path.[/dim]")
    set_pref("setup_complete", ready)
