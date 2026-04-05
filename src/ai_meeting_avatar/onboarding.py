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

from .config import load_config
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


def _venv_bin(name: str) -> str:
    venv = Path(sys.prefix) / "bin" / name
    return str(venv) if venv.exists() else name


def _step(title: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]"))


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


def _choose_brain(config_path: str) -> str:
    _step("Step 1/5 - Choose Brain")
    console.print(
        Panel(
            "[bold]1) Gemma[/bold] - local, private, larger setup\n"
            "[bold]2) Claude[/bold] - cloud, faster to get working\n\n"
            "You can switch later with [cyan]ai-avatar brain --set ...[/cyan].",
            title="AI Brain",
            border_style="yellow",
        )
    )
    choice = Prompt.ask("Pick brain", choices=["1", "2"], default="2")
    backend = "claude" if choice == "2" else "gemma"
    _set_config_value(config_path, "llm", "backend", f'"{backend}"')
    return backend


def _choose_voice(config_path: str) -> str:
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


def _setup_brain(config_path: str, backend: str) -> bool:
    _step("Step 3/5 - Configure Brain")

    if backend == "claude":
        api_key = ""
        env_path = Path(config_path).resolve().parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
        if not api_key:
            api_key = Prompt.ask("Paste your Anthropic API key", password=True)
            lines = env_path.read_text().splitlines() if env_path.exists() else []
            lines = [line for line in lines if not line.startswith("ANTHROPIC_API_KEY=")]
            lines.append(f"ANTHROPIC_API_KEY={api_key}")
            env_path.write_text("\n".join(lines) + "\n")

        if importlib.util.find_spec("anthropic") is None:
            console.print("[cyan]Installing Claude dependencies...[/cyan]")
            if not _install_package("[claude]"):
                console.print("[red]Could not install Claude dependencies.[/red]")
                return False
        console.print("[green]Claude is configured.[/green]")
        return True

    model_path = Path(load_config(config_path).llm.model_path)
    if model_path.exists():
        console.print(f"[green]Gemma model already present:[/green] {model_path}")
        return True

    console.print("[cyan]Gemma is selected. Download the model with scripts/setup_models.sh if needed.[/cyan]")
    return False


def _setup_avatar(config_path: str, photo: str | None = None) -> bool:
    _step("Step 4/5 - Avatar Setup")
    from .avatar import validate_photo  # noqa: PLC0415

    cfg = load_config(config_path)
    photo_path = photo or Prompt.ask("Path to avatar photo", default=cfg.avatar.photo_path)
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
    if missing and Confirm.ask("Install avatar dependencies now?", default=True):
        if not _install_package("[avatar]"):
            console.print("[red]Could not install avatar dependencies.[/red]")
            return False

    console.print("[green]Avatar photo and settings saved.[/green]")
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
    if state.ready_to_join:
        console.print("[bold green]Ready.[/bold green] Run [cyan]ai-avatar join <room>[/cyan].")
    else:
        console.print("[bold yellow]Almost there.[/bold yellow] Run [cyan]ai-avatar doctor[/cyan] for full details.")
    return state.ready_to_join


async def run_avatar_setup_only(config_path: str, photo: str | None) -> None:
    _setup_avatar(config_path, photo)
    _run_doctor_summary(config_path)


async def run_onboarding(config_path: str) -> None:
    console.print(
        Panel.fit(
            "[bold white]AI Meeting Avatar[/bold white]\n"
            "Set up a synced meeting avatar that other agents can install and run.\n\n"
            "This setup is opinionated: one config, one voice, one avatar path, one readiness report.",
            title="Welcome",
            border_style="cyan",
        )
    )
    if not Confirm.ask("Start setup?", default=True):
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
    if missing and Confirm.ask("Install missing base dependencies now?", default=True):
        if not _install_package():
            console.print("[red]Base dependency install failed.[/red]")
            raise SystemExit(1)

    backend = _choose_brain(config_path)
    _choose_voice(config_path)
    _setup_brain(config_path, backend)
    _setup_avatar(config_path)
    ready = _run_doctor_summary(config_path)
    set_pref("setup_complete", ready)
