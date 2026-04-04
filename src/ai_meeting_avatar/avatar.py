"""
Avatar rendering module (Phase 2).

Provides a common interface for driving a 2-D portrait with synthesised audio.
Implementations:
  - SadTalkerRenderer  — uses SadTalker (subprocess call to inference.py)
  - LivePortraitRenderer — uses LivePortrait
  - NullAvatarRenderer — audio-only passthrough (MVP / default)

When avatar.enabled = true in config.yaml, the rendered video can be pushed
to OBS via the OBSVirtualCamera helper, which appears as a webcam in Zoom/Meet.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Abstract interface ─────────────────────────────────────────────────────────


class AvatarRenderer(ABC):
    """Drive a portrait photo with an audio clip and produce a video."""

    @abstractmethod
    async def load(self) -> None:
        """Load / verify model weights."""
        ...

    @abstractmethod
    async def render(self, audio_path: str, output_path: str) -> str:
        """
        Render avatar video from *audio_path*.

        Args:
            audio_path:  Path to the WAV/MP3 file driving the avatar.
            output_path: Desired output video path (MP4).

        Returns:
            Actual path to the rendered video (may differ from *output_path*).
        """
        ...


# ── No-op (audio-only MVP) ─────────────────────────────────────────────────────


class NullAvatarRenderer(AvatarRenderer):
    """Used in audio-only mode; render() is a no-op."""

    async def load(self) -> None:
        logger.info("Avatar rendering disabled — running audio-only mode.")

    async def render(self, audio_path: str, output_path: str) -> str:
        return audio_path


# ── SadTalker ─────────────────────────────────────────────────────────────────


class SadTalkerRenderer(AvatarRenderer):
    """
    Drive a portrait with SadTalker.

    Prerequisites:
      1. Clone SadTalker into ./models/SadTalker
      2. Run scripts/setup_models.sh to download checkpoints
      3. pip install -e ".[avatar]"

    SadTalker repo: https://github.com/OpenTalker/SadTalker
    """

    def __init__(
        self,
        photo_path: str,
        sadtalker_path: str = "./models/SadTalker",
        enhancer: Optional[str] = "gfpgan",
        fps: int = 25,
    ) -> None:
        self._photo_path = photo_path
        self._sadtalker_path = Path(sadtalker_path)
        self._enhancer = enhancer
        self._fps = fps

    async def load(self) -> None:
        if not self._sadtalker_path.exists():
            logger.warning(
                "SadTalker not found at '%s'. "
                "Run: git clone https://github.com/OpenTalker/SadTalker %s",
                self._sadtalker_path,
                self._sadtalker_path,
            )
        else:
            logger.info("SadTalker renderer ready (path=%s).", self._sadtalker_path)

    async def render(self, audio_path: str, output_path: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._render_sync, audio_path, output_path
        )

    def _render_sync(self, audio_path: str, output_path: str) -> str:
        out_dir = str(Path(output_path).parent)
        cmd = [
            "python",
            str(self._sadtalker_path / "inference.py"),
            "--driven_audio", audio_path,
            "--source_image", self._photo_path,
            "--result_dir", out_dir,
            "--still",
            "--preprocess", "full",
            "--expression_scale", "1.0",
        ]
        if self._enhancer:
            cmd += ["--enhancer", self._enhancer]

        logger.debug("Running SadTalker: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self._sadtalker_path))

        if result.returncode != 0:
            logger.error("SadTalker stderr:\n%s", result.stderr)
            raise RuntimeError(f"SadTalker failed with code {result.returncode}")

        logger.info("SadTalker render complete: %s", output_path)
        return output_path


# ── LivePortrait ───────────────────────────────────────────────────────────────


class LivePortraitRenderer(AvatarRenderer):
    """
    Drive a portrait with LivePortrait.

    Prerequisites:
      1. Clone LivePortrait into ./models/LivePortrait
      2. pip install -e ".[avatar]"

    LivePortrait repo: https://github.com/KwaiVGI/LivePortrait
    """

    def __init__(
        self,
        photo_path: str,
        liveportrait_path: str = "./models/LivePortrait",
        fps: int = 25,
    ) -> None:
        self._photo_path = photo_path
        self._liveportrait_path = Path(liveportrait_path)
        self._fps = fps

    async def load(self) -> None:
        if not self._liveportrait_path.exists():
            logger.warning(
                "LivePortrait not found at '%s'. "
                "Run: git clone https://github.com/KwaiVGI/LivePortrait %s",
                self._liveportrait_path,
                self._liveportrait_path,
            )
        else:
            logger.info("LivePortrait renderer ready (path=%s).", self._liveportrait_path)

    async def render(self, audio_path: str, output_path: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._render_sync, audio_path, output_path
        )

    def _render_sync(self, audio_path: str, output_path: str) -> str:
        cmd = [
            "python",
            str(self._liveportrait_path / "inference.py"),
            "--source", self._photo_path,
            "--driving_audio", audio_path,
            "--output", output_path,
            "--fps", str(self._fps),
        ]
        logger.debug("Running LivePortrait: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(self._liveportrait_path)
        )
        if result.returncode != 0:
            logger.error("LivePortrait stderr:\n%s", result.stderr)
            raise RuntimeError(f"LivePortrait failed with code {result.returncode}")

        logger.info("LivePortrait render complete: %s", output_path)
        return output_path


# ── OBS virtual camera output ──────────────────────────────────────────────────


class OBSVirtualCamera:
    """
    Push rendered video frames into an OBS media source via WebSocket,
    making the avatar appear as a virtual webcam in Zoom / Google Meet.

    Requires OBS >= 28 with WebSocket server enabled and obs-websocket-py:
        pip install obs-websocket-py
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4455,
        password: str = "",
        source_name: str = "AI Avatar",
    ) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._source_name = source_name
        self._ws = None

    async def connect(self) -> None:
        try:
            import obsws_python as obs  # noqa: PLC0415
        except ImportError:
            raise ImportError("Install obs-websocket-py: pip install -e '.[obs]'")

        self._ws = obs.ReqClient(
            host=self._host,
            port=self._port,
            password=self._password,
        )
        logger.info("Connected to OBS WebSocket at %s:%d", self._host, self._port)

    async def update_source(self, video_path: str) -> None:
        """Point the OBS media source at *video_path* and play it."""
        if self._ws is None:
            raise RuntimeError("Call OBSVirtualCamera.connect() first.")

        self._ws.set_input_settings(
            name=self._source_name,
            settings={"local_file": str(Path(video_path).resolve()), "looping": False},
            overlay=True,
        )
        logger.debug("OBS source '%s' updated to: %s", self._source_name, video_path)


# ── Factory ────────────────────────────────────────────────────────────────────


def create_renderer(config) -> AvatarRenderer:
    """Construct the right AvatarRenderer from AvatarConfig."""
    if not config.enabled:
        return NullAvatarRenderer()

    if config.model == "sadtalker":
        return SadTalkerRenderer(
            photo_path=config.photo_path,
            sadtalker_path=config.sadtalker_path,
            enhancer=config.enhancer,
            fps=config.output_fps,
        )
    if config.model == "liveportrait":
        return LivePortraitRenderer(
            photo_path=config.photo_path,
            liveportrait_path=config.liveportrait_path,
            fps=config.output_fps,
        )

    raise ValueError(f"Unknown avatar model: '{config.model}'. Use 'sadtalker' or 'liveportrait'.")
