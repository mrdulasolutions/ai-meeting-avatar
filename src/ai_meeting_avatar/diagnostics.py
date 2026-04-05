"""
Shared runtime diagnostics for the CLI and installable skill.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .avatar import create_renderer
from .config import load_config
from .prefs import load as load_prefs

PID_FILE = Path("/tmp/ai-avatar.pid")
LOG_FILE = Path("/tmp/ai-avatar.log")
RUNTIME_ROOM_FILE = Path("/tmp/ai-avatar-room")


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _env_file_has_key(path: Path, key: str) -> bool:
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}=") and line.split("=", 1)[1].strip():
            return True
    return False


def _livekit_running(url: str) -> bool:
    http_url = url.replace("ws://", "http://").replace("wss://", "https://")
    try:
        with urllib.request.urlopen(http_url, timeout=2):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, ValueError):
        return False


def _read_pid_status() -> tuple[bool, int | None]:
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return False, None

    try:
        os.kill(pid, 0)
    except OSError:
        return False, pid
    return True, pid


@dataclass
class ReadinessCheck:
    key: str
    ok: bool
    summary: str
    detail: str = ""


@dataclass
class RuntimeState:
    config_path: str
    backend: str
    voice: str
    avatar_enabled: bool
    room: str
    livekit_url: str
    livekit_running: bool
    avatar_running: bool
    avatar_pid: int | None
    renderer: str
    renderer_ready: bool
    camera_output: str
    kokoro_ready: bool
    gemma_ready: bool
    anthropic_key_ready: bool
    docker_ready: bool
    ffmpeg_ready: bool
    obs_ready: bool
    pyvirtualcam_ready: bool
    avatar_deps_ready: bool
    setup_complete: bool
    photo_path: str
    photo_ready: bool
    checks: list[ReadinessCheck] = field(default_factory=list)
    prefs: dict[str, Any] = field(default_factory=dict)

    @property
    def ready_to_join(self) -> bool:
        return all(check.ok for check in self.checks if check.key in {
            "avatar_enabled",
            "livekit_credentials",
            "brain",
            "tts",
            "avatar_photo",
            "renderer",
            "camera_output",
        })

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = [asdict(check) for check in self.checks]
        data["ready_to_join"] = self.ready_to_join
        return data


def gather_state(config_path: str | Path = "config.yaml") -> RuntimeState:
    cfg = load_config(config_path)
    prefs = load_prefs()
    env_path = Path(config_path).resolve().parent / ".env"
    avatar_running, avatar_pid = _read_pid_status()
    runtime_room = RUNTIME_ROOM_FILE.read_text().strip() if RUNTIME_ROOM_FILE.exists() else ""

    renderer = create_renderer(cfg.avatar)
    renderer_ready = renderer.is_available()

    anthropic_key_ready = bool(cfg.llm.anthropic_api_key) or _env_file_has_key(env_path, "ANTHROPIC_API_KEY")
    gemma_ready = Path(cfg.llm.model_path).exists() or any(Path(cfg.llm.fallback_model_path).glob("*.litertlm"))
    kokoro_ready = (
        Path(cfg.tts.model_dir, "kokoro-v1.0.onnx").exists()
        and Path(cfg.tts.model_dir, "voices-v1.0.bin").exists()
    )
    photo_ready = Path(cfg.avatar.photo_path).exists()
    pyvirtualcam_ready = _module_available("pyvirtualcam")
    obs_ready = _module_available("obsws_python")
    avatar_deps_ready = all(_module_available(name) for name in ("torch", "cv2", "PIL"))

    camera_ok = cfg.avatar.camera_output == "none"
    if cfg.avatar.enabled:
        if cfg.avatar.camera_output in {"auto", "pyvirtualcam"}:
            camera_ok = pyvirtualcam_ready or (cfg.avatar.camera_output == "auto" and obs_ready)
        elif cfg.avatar.camera_output == "obs":
            camera_ok = obs_ready

    checks = [
        ReadinessCheck(
            key="avatar_enabled",
            ok=cfg.avatar.enabled,
            summary="Avatar mode enabled",
        ),
        ReadinessCheck(
            key="livekit_credentials",
            ok=bool(cfg.livekit.url and cfg.livekit.api_key and cfg.livekit.api_secret),
            summary="LiveKit credentials configured",
            detail=cfg.livekit.url,
        ),
        ReadinessCheck(
            key="brain",
            ok=anthropic_key_ready if cfg.llm.backend == "claude" else gemma_ready,
            summary="Claude API key ready" if cfg.llm.backend == "claude" else "Gemma model present",
        ),
        ReadinessCheck(
            key="tts",
            ok=kokoro_ready,
            summary="Kokoro voice models present",
        ),
        ReadinessCheck(
            key="avatar_photo",
            ok=(not cfg.avatar.enabled) or photo_ready,
            summary="Avatar photo present",
            detail=cfg.avatar.photo_path,
        ),
        ReadinessCheck(
            key="renderer",
            ok=(not cfg.avatar.enabled) or renderer_ready,
            summary=f"{cfg.avatar.model} renderer ready",
        ),
        ReadinessCheck(
            key="camera_output",
            ok=(not cfg.avatar.enabled) or camera_ok,
            summary=f"Camera output '{cfg.avatar.camera_output}' ready",
        ),
    ]

    return RuntimeState(
        config_path=str(Path(config_path).resolve()),
        backend=cfg.llm.backend,
        voice=cfg.tts.voice,
        avatar_enabled=cfg.avatar.enabled,
        room=runtime_room or cfg.livekit.room or (prefs.get("last_room") or ""),
        livekit_url=cfg.livekit.url,
        livekit_running=_livekit_running(cfg.livekit.url),
        avatar_running=avatar_running,
        avatar_pid=avatar_pid,
        renderer=cfg.avatar.model,
        renderer_ready=renderer_ready,
        camera_output=cfg.avatar.camera_output,
        kokoro_ready=kokoro_ready,
        gemma_ready=gemma_ready,
        anthropic_key_ready=anthropic_key_ready,
        docker_ready=shutil.which("docker") is not None,
        ffmpeg_ready=shutil.which("ffmpeg") is not None,
        obs_ready=obs_ready,
        pyvirtualcam_ready=pyvirtualcam_ready,
        avatar_deps_ready=avatar_deps_ready,
        setup_complete=bool(prefs.get("setup_complete")),
        photo_path=cfg.avatar.photo_path,
        photo_ready=photo_ready,
        checks=checks,
        prefs=prefs,
    )


def format_state_json(config_path: str | Path = "config.yaml") -> str:
    return json.dumps(gather_state(config_path).as_dict(), indent=2)
