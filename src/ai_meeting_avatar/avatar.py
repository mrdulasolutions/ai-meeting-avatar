"""
Avatar rendering module (Phase 2).

Architecture:
  TTS audio → SadTalker/LivePortrait → video frames → VirtualCamera → Zoom/Meet webcam

Provides:
  - AvatarRenderer (abstract) with SadTalkerRenderer and LivePortraitRenderer
  - VirtualCamera — writes frames to a virtual webcam via pyvirtualcam or OBS
  - validate_photo() — checks a photo has a detectable face
  - create_renderer() — factory from config

When avatar.enabled = true in config.yaml:
  1. Audio plays immediately through LiveKit (no delay)
  2. SadTalker renders lip-synced video in background
  3. Video frames stream to virtual camera (appears as webcam in Zoom/Meet)
  4. While rendering, the static photo shows as the webcam feed
"""

from __future__ import annotations

import asyncio
import glob
import logging
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ── Photo validation ─────────────────────────────────────────────────────────


def validate_photo(photo_path: str) -> dict:
    """
    Validate a photo for use as an avatar source.

    Returns dict with keys: valid (bool), width, height, faces (int), error (str|None).
    """
    path = Path(photo_path)
    if not path.exists():
        return {"valid": False, "error": f"File not found: {photo_path}"}

    try:
        from PIL import Image  # noqa: PLC0415

        img = Image.open(path)
        width, height = img.size

        if width < 128 or height < 128:
            return {
                "valid": False,
                "width": width,
                "height": height,
                "faces": 0,
                "error": f"Image too small ({width}x{height}). Minimum 128x128.",
            }

        # Try face detection via OpenCV (optional — graceful fallback)
        faces = _detect_faces(path)

        if faces == 0:
            return {
                "valid": False,
                "width": width,
                "height": height,
                "faces": 0,
                "error": "No face detected. Use a clear, front-facing photo.",
            }

        return {
            "valid": True,
            "width": width,
            "height": height,
            "faces": faces,
            "error": None,
        }

    except Exception as exc:
        return {"valid": False, "error": f"Cannot read image: {exc}"}


def _detect_faces(photo_path: Path) -> int:
    """Detect faces using OpenCV's Haar cascade. Returns face count."""
    try:
        import cv2  # noqa: PLC0415

        img = cv2.imread(str(photo_path))
        if img is None:
            return 0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        return len(faces)
    except ImportError:
        # OpenCV not installed — skip face detection, assume valid
        logger.debug("OpenCV not installed — skipping face detection.")
        return 1
    except Exception as exc:
        logger.debug("Face detection failed: %s — assuming valid.", exc)
        return 1


# ── Abstract renderer ────────────────────────────────────────────────────────


class AvatarRenderer(ABC):
    """Drive a portrait photo with an audio clip and produce a video."""

    @abstractmethod
    async def load(self) -> None:
        """Load / verify model weights."""
        ...

    @abstractmethod
    async def render(self, audio_path: str, output_dir: str) -> str:
        """
        Render avatar video from *audio_path*.

        Args:
            audio_path: Path to the WAV file driving the avatar.
            output_dir: Directory to write the output video.

        Returns:
            Path to the rendered video file (renderer picks the filename).
        """
        ...

    def is_available(self) -> bool:
        """Return True if the renderer's models/checkpoints are present."""
        return True


# ── No-op (audio-only MVP) ──────────────────────────────────────────────────


class NullAvatarRenderer(AvatarRenderer):
    """Used in audio-only mode; render() is a no-op."""

    async def load(self) -> None:
        logger.info("Avatar rendering disabled — running audio-only mode.")

    async def render(self, audio_path: str, output_dir: str) -> str:
        return ""


# ── SadTalker ────────────────────────────────────────────────────────────────


class SadTalkerRenderer(AvatarRenderer):
    """
    Drive a portrait with SadTalker.

    SadTalker generates its own output filename in the result directory.
    This renderer finds the most recent .mp4 file after rendering.
    """

    REQUIRED_CHECKPOINTS = [
        "checkpoints/SadTalker_V0.0.2_256.safetensors",
        "checkpoints/mapping_00109-model.pth.tar",
        "checkpoints/mapping_00229-model.pth.tar",
    ]

    def __init__(
        self,
        photo_path: str,
        sadtalker_path: str = "./models/SadTalker",
        enhancer: Optional[str] = "gfpgan",
        fps: int = 25,
        device: str = "cpu",
    ) -> None:
        self._photo_path = photo_path
        self._sadtalker_path = Path(sadtalker_path)
        self._enhancer = enhancer
        self._fps = fps
        self._device = device

    def is_available(self) -> bool:
        if not self._sadtalker_path.exists():
            return False
        if not (self._sadtalker_path / "inference.py").exists():
            return False
        for ckpt in self.REQUIRED_CHECKPOINTS:
            if not (self._sadtalker_path / ckpt).exists():
                return False
        return True

    async def load(self) -> None:
        if not self._sadtalker_path.exists():
            logger.error(
                "SadTalker not found at '%s'. "
                "Run: ./scripts/setup_models.sh",
                self._sadtalker_path,
            )
            raise FileNotFoundError(f"SadTalker not found at {self._sadtalker_path}")

        if not (self._sadtalker_path / "inference.py").exists():
            raise FileNotFoundError(
                f"SadTalker inference.py not found at {self._sadtalker_path}. "
                "The clone may be incomplete — re-run ./scripts/setup_models.sh"
            )

        missing = [
            c for c in self.REQUIRED_CHECKPOINTS
            if not (self._sadtalker_path / c).exists()
        ]
        if missing:
            logger.error("Missing SadTalker checkpoints: %s", missing)
            raise FileNotFoundError(
                f"Missing SadTalker checkpoints: {missing}. "
                "Run: ./scripts/setup_models.sh"
            )

        logger.info(
            "SadTalker renderer ready (path=%s, device=%s).",
            self._sadtalker_path,
            self._device,
        )

    async def render(self, audio_path: str, output_dir: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._render_sync, audio_path, output_dir
        )

    def _render_sync(self, audio_path: str, output_dir: str) -> str:
        # Record existing mp4 files before render so we can find the new one
        existing_mp4s = set(glob.glob(f"{output_dir}/**/*.mp4", recursive=True))

        cmd = [
            "python",
            str(self._sadtalker_path / "inference.py"),
            "--driven_audio", audio_path,
            "--source_image", self._photo_path,
            "--result_dir", output_dir,
            "--still",
            "--preprocess", "full",
            "--expression_scale", "1.0",
        ]
        if self._enhancer:
            cmd += ["--enhancer", self._enhancer]

        # Use CUDA/MPS if available
        if self._device == "cuda":
            pass  # SadTalker uses CUDA by default when available
        elif self._device == "mps":
            # SadTalker doesn't natively support MPS — run on CPU
            cmd += ["--device", "cpu"]
        else:
            cmd += ["--device", "cpu"]

        logger.info("Rendering avatar (SadTalker) — this may take 30-120 seconds …")
        t0 = time.monotonic()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(self._sadtalker_path),
            timeout=300,
        )

        elapsed = time.monotonic() - t0
        logger.info("SadTalker render took %.1f seconds.", elapsed)

        if result.returncode != 0:
            logger.error("SadTalker stderr:\n%s", result.stderr[-1000:])
            raise RuntimeError(
                f"SadTalker failed (code {result.returncode}). "
                f"Last error: {result.stderr[-200:]}"
            )

        # Find the newly created mp4 file
        new_mp4s = set(glob.glob(f"{output_dir}/**/*.mp4", recursive=True))
        added = new_mp4s - existing_mp4s

        if not added:
            # Fallback: find most recent mp4
            all_mp4s = sorted(
                glob.glob(f"{output_dir}/**/*.mp4", recursive=True),
                key=lambda f: Path(f).stat().st_mtime,
                reverse=True,
            )
            if all_mp4s:
                video_path = all_mp4s[0]
            else:
                raise RuntimeError("SadTalker produced no output video.")
        else:
            video_path = max(added, key=lambda f: Path(f).stat().st_mtime)

        logger.info("SadTalker output: %s", video_path)
        return video_path


# ── LivePortrait ─────────────────────────────────────────────────────────────


class LivePortraitRenderer(AvatarRenderer):
    """
    Drive a portrait with LivePortrait.

    LivePortrait supports audio-driven portrait animation via its inference
    script. Higher quality than SadTalker but may require more compute.
    """

    def __init__(
        self,
        photo_path: str,
        liveportrait_path: str = "./models/LivePortrait",
        fps: int = 25,
        device: str = "cpu",
    ) -> None:
        self._photo_path = photo_path
        self._liveportrait_path = Path(liveportrait_path)
        self._fps = fps
        self._device = device

    def is_available(self) -> bool:
        return (
            self._liveportrait_path.exists()
            and (self._liveportrait_path / "inference.py").exists()
        )

    async def load(self) -> None:
        if not self._liveportrait_path.exists():
            raise FileNotFoundError(
                f"LivePortrait not found at {self._liveportrait_path}. "
                "Run: git clone https://github.com/KwaiVGI/LivePortrait "
                f"{self._liveportrait_path}"
            )
        logger.info("LivePortrait renderer ready (path=%s).", self._liveportrait_path)

    async def render(self, audio_path: str, output_dir: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._render_sync, audio_path, output_dir
        )

    def _render_sync(self, audio_path: str, output_dir: str) -> str:
        output_path = str(Path(output_dir) / "avatar_lp.mp4")
        cmd = [
            "python",
            str(self._liveportrait_path / "inference.py"),
            "--source", self._photo_path,
            "--driving_audio", audio_path,
            "--output", output_path,
            "--fps", str(self._fps),
        ]

        logger.info("Rendering avatar (LivePortrait) …")
        t0 = time.monotonic()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(self._liveportrait_path),
            timeout=300,
        )

        elapsed = time.monotonic() - t0
        logger.info("LivePortrait render took %.1f seconds.", elapsed)

        if result.returncode != 0:
            logger.error("LivePortrait stderr:\n%s", result.stderr[-1000:])
            raise RuntimeError(
                f"LivePortrait failed (code {result.returncode}). "
                f"Last error: {result.stderr[-200:]}"
            )

        if not Path(output_path).exists():
            raise RuntimeError("LivePortrait produced no output video.")

        return output_path


# ── Virtual camera output ────────────────────────────────────────────────────


class VirtualCamera:
    """
    Write frames to a virtual webcam device.

    Uses pyvirtualcam (backed by OBS Virtual Camera on macOS).
    Requires OBS Studio to be installed (but NOT running) on macOS.
    On Linux, requires v4l2loopback.

    When idle, shows the static avatar photo.
    When speaking, streams rendered video frames.
    """

    def __init__(
        self,
        width: int = 256,
        height: int = 256,
        fps: int = 25,
        photo_path: Optional[str] = None,
    ) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._photo_path = photo_path
        self._cam = None
        self._idle_frame: Optional[np.ndarray] = None
        self._running = False

    async def start(self) -> None:
        """Open the virtual camera device."""
        try:
            import pyvirtualcam  # noqa: PLC0415
        except ImportError:
            raise ImportError(
                "pyvirtualcam not installed. Run: pip install -e '.[avatar]'"
            )

        loop = asyncio.get_event_loop()
        self._cam = await loop.run_in_executor(
            None, self._open_camera
        )
        self._running = True

        # Prepare idle frame from photo
        if self._photo_path and Path(self._photo_path).exists():
            self._idle_frame = await loop.run_in_executor(
                None, self._load_idle_frame
            )
            logger.info("Virtual camera started (%dx%d). Showing idle photo.", self._width, self._height)
        else:
            # Black frame as fallback
            self._idle_frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)
            logger.info("Virtual camera started (%dx%d). No photo — showing black.", self._width, self._height)

    def _open_camera(self):
        import pyvirtualcam  # noqa: PLC0415

        cam = pyvirtualcam.Camera(
            width=self._width,
            height=self._height,
            fps=self._fps,
            fmt=pyvirtualcam.PixelFormat.RGB,
        )
        logger.info("Virtual camera device: %s", cam.device)
        return cam

    def _load_idle_frame(self) -> np.ndarray:
        """Load and resize the avatar photo as an RGB numpy array."""
        from PIL import Image  # noqa: PLC0415

        img = Image.open(self._photo_path).convert("RGB")
        img = img.resize((self._width, self._height), Image.LANCZOS)
        return np.array(img)

    async def send_idle_frame(self) -> None:
        """Send one idle (static photo) frame to the virtual camera."""
        if self._cam is None or self._idle_frame is None:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._cam.send, self._idle_frame)

    async def stream_video(self, video_path: str) -> None:
        """
        Read a video file and stream its frames to the virtual camera.

        Plays at the video's native FPS for lip-sync timing.
        """
        if self._cam is None:
            logger.warning("Virtual camera not started — skipping video stream.")
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._stream_video_sync, video_path)

    def _stream_video_sync(self, video_path: str) -> None:
        """Read video frames with OpenCV and push to virtual camera."""
        try:
            import cv2  # noqa: PLC0415
        except ImportError:
            logger.error("OpenCV not installed — cannot stream video. pip install opencv-python")
            return

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error("Cannot open video: %s", video_path)
            return

        try:
            video_fps = cap.get(cv2.CAP_PROP_FPS) or self._fps
            frame_delay = 1.0 / video_fps
            frame_count = 0

            logger.info("Streaming avatar video (%s) to virtual camera at %.0f FPS …", video_path, video_fps)

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # OpenCV reads as BGR — convert to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # Resize to virtual camera dimensions
                if frame_rgb.shape[:2] != (self._height, self._width):
                    frame_rgb = cv2.resize(frame_rgb, (self._width, self._height))

                self._cam.send(frame_rgb)
                frame_count += 1
                time.sleep(frame_delay)

            logger.info("Streamed %d video frames to virtual camera.", frame_count)
        finally:
            cap.release()

        # Return to idle frame
        if self._idle_frame is not None:
            self._cam.send(self._idle_frame)

    async def stop(self) -> None:
        """Close the virtual camera."""
        if self._cam is not None:
            self._cam.close()
            self._cam = None
            self._running = False
            logger.info("Virtual camera stopped.")

    @property
    def is_running(self) -> bool:
        return self._running


# ── OBS virtual camera output (legacy) ───────────────────────────────────────


class OBSVirtualCamera:
    """
    Push rendered video into an OBS media source via WebSocket.

    Requires OBS >= 28 with WebSocket server enabled and obs-websocket-py.
    This is the legacy approach — pyvirtualcam (VirtualCamera) is simpler.
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
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._connect_sync)

    def _connect_sync(self) -> None:
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

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._update_sync, video_path)

    async def disconnect(self) -> None:
        """Close the OBS WebSocket connection."""
        if self._ws is not None:
            try:
                self._ws.disconnect()
            except Exception:
                pass
            self._ws = None
            logger.info("Disconnected from OBS WebSocket.")

    def _update_sync(self, video_path: str) -> None:
        self._ws.set_input_settings(
            name=self._source_name,
            settings={"local_file": str(Path(video_path).resolve()), "looping": False},
            overlay=True,
        )
        logger.debug("OBS source '%s' updated to: %s", self._source_name, video_path)


# ── Factory ──────────────────────────────────────────────────────────────────


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
            device=config.device,
        )
    if config.model == "liveportrait":
        return LivePortraitRenderer(
            photo_path=config.photo_path,
            liveportrait_path=config.liveportrait_path,
            fps=config.output_fps,
            device=config.device,
        )

    raise ValueError(f"Unknown avatar model: '{config.model}'. Use 'sadtalker' or 'liveportrait'.")


def create_virtual_camera(config) -> Optional[VirtualCamera]:
    """Create a VirtualCamera if avatar is enabled and camera output is configured."""
    if not config.enabled:
        return None

    if config.camera_output == "none":
        return None

    if config.camera_output in ("auto", "pyvirtualcam"):
        try:
            return VirtualCamera(
                width=config.render_width,
                height=config.render_height,
                fps=config.output_fps,
                photo_path=config.photo_path,
            )
        except Exception as exc:
            logger.warning("Cannot create virtual camera: %s", exc)
            if config.camera_output == "auto":
                logger.info("pyvirtualcam unavailable — continuing without virtual camera.")
                return None
            raise

    return None
