"""
Unit tests for avatar rendering module (Phase 2).

All tests use mocks so they run without SadTalker, LivePortrait, or pyvirtualcam.
Run: pytest tests/test_avatar.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ai_meeting_avatar.avatar import (
    NullAvatarRenderer,
    SadTalkerRenderer,
    LivePortraitRenderer,
    VirtualCamera,
    create_renderer,
    create_virtual_camera,
    validate_photo,
)
from ai_meeting_avatar.config import AvatarConfig

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

needs_pil = pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed (avatar optional dep)")


# ── Photo validation ─────────────────────────────────────────────────────────


class TestValidatePhoto:
    def test_missing_file(self):
        result = validate_photo("/nonexistent/photo.jpg")
        assert not result["valid"]
        assert "not found" in result["error"].lower()

    @needs_pil
    def test_valid_photo(self, tmp_path):
        img = Image.new("RGB", (256, 256), color="white")
        photo = tmp_path / "test.jpg"
        img.save(photo)

        with patch("ai_meeting_avatar.avatar._detect_faces", return_value=1):
            result = validate_photo(str(photo))

        assert result["valid"]
        assert result["width"] == 256
        assert result["height"] == 256
        assert result["faces"] == 1

    @needs_pil
    def test_too_small(self, tmp_path):
        img = Image.new("RGB", (64, 64), color="white")
        photo = tmp_path / "tiny.jpg"
        img.save(photo)

        result = validate_photo(str(photo))
        assert not result["valid"]
        assert "too small" in result["error"].lower()

    @needs_pil
    def test_no_face_detected(self, tmp_path):
        img = Image.new("RGB", (256, 256), color="white")
        photo = tmp_path / "noface.jpg"
        img.save(photo)

        with patch("ai_meeting_avatar.avatar._detect_faces", return_value=0):
            result = validate_photo(str(photo))

        assert not result["valid"]
        assert "no face" in result["error"].lower()


# ── NullAvatarRenderer ───────────────────────────────────────────────────────


class TestNullAvatarRenderer:
    @pytest.mark.asyncio
    async def test_load_is_noop(self):
        renderer = NullAvatarRenderer()
        await renderer.load()  # should not raise

    @pytest.mark.asyncio
    async def test_render_returns_empty(self):
        renderer = NullAvatarRenderer()
        result = await renderer.render("/fake/audio.wav", "/fake/output")
        assert result == ""

    def test_is_available(self):
        renderer = NullAvatarRenderer()
        assert renderer.is_available()


# ── SadTalkerRenderer ────────────────────────────────────────────────────────


class TestSadTalkerRenderer:
    def test_not_available_when_path_missing(self):
        renderer = SadTalkerRenderer(
            photo_path="/fake/photo.jpg",
            sadtalker_path="/nonexistent/SadTalker",
        )
        assert not renderer.is_available()

    def test_not_available_when_inference_missing(self, tmp_path):
        renderer = SadTalkerRenderer(
            photo_path="/fake/photo.jpg",
            sadtalker_path=str(tmp_path),
        )
        assert not renderer.is_available()

    def test_available_when_all_present(self, tmp_path):
        # Create inference.py and checkpoints
        (tmp_path / "inference.py").touch()
        (tmp_path / "checkpoints").mkdir()
        for ckpt in SadTalkerRenderer.REQUIRED_CHECKPOINTS:
            (tmp_path / ckpt).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / ckpt).touch()

        renderer = SadTalkerRenderer(
            photo_path="/fake/photo.jpg",
            sadtalker_path=str(tmp_path),
        )
        assert renderer.is_available()

    @pytest.mark.asyncio
    async def test_load_raises_when_missing(self):
        renderer = SadTalkerRenderer(
            photo_path="/fake/photo.jpg",
            sadtalker_path="/nonexistent/SadTalker",
        )
        with pytest.raises(FileNotFoundError):
            await renderer.load()

    @pytest.mark.asyncio
    async def test_render_calls_subprocess(self, tmp_path):
        # Create minimal SadTalker structure
        (tmp_path / "inference.py").touch()
        (tmp_path / "checkpoints").mkdir()
        for ckpt in SadTalkerRenderer.REQUIRED_CHECKPOINTS:
            (tmp_path / ckpt).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / ckpt).touch()

        renderer = SadTalkerRenderer(
            photo_path="/fake/photo.jpg",
            sadtalker_path=str(tmp_path),
        )
        await renderer.load()

        output_dir = str(tmp_path / "output")
        Path(output_dir).mkdir()
        # Create a fake output video
        fake_video = Path(output_dir) / "result.mp4"
        fake_video.write_bytes(b"fake video")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = await renderer.render("/fake/audio.wav", output_dir)

        assert result == str(fake_video)


# ── LivePortraitRenderer ─────────────────────────────────────────────────────


class TestLivePortraitRenderer:
    def test_not_available_when_missing(self):
        renderer = LivePortraitRenderer(
            photo_path="/fake/photo.jpg",
            liveportrait_path="/nonexistent/LivePortrait",
        )
        assert not renderer.is_available()

    @pytest.mark.asyncio
    async def test_load_raises_when_missing(self):
        renderer = LivePortraitRenderer(
            photo_path="/fake/photo.jpg",
            liveportrait_path="/nonexistent/LivePortrait",
        )
        with pytest.raises(FileNotFoundError):
            await renderer.load()


# ── Factory functions ────────────────────────────────────────────────────────


class TestCreateRenderer:
    def test_disabled_returns_null(self):
        config = AvatarConfig(enabled=False)
        renderer = create_renderer(config)
        assert isinstance(renderer, NullAvatarRenderer)

    def test_sadtalker_selected(self):
        config = AvatarConfig(enabled=True, model="sadtalker")
        renderer = create_renderer(config)
        assert isinstance(renderer, SadTalkerRenderer)

    def test_liveportrait_selected(self):
        config = AvatarConfig(enabled=True, model="liveportrait")
        renderer = create_renderer(config)
        assert isinstance(renderer, LivePortraitRenderer)

    def test_unknown_model_raises(self):
        with pytest.raises(Exception, match="sadtalker.*liveportrait"):
            AvatarConfig(enabled=True, model="unknown")

    def test_create_virtual_camera_disabled(self):
        config = AvatarConfig(enabled=False)
        vcam = create_virtual_camera(config)
        assert vcam is None

    def test_create_virtual_camera_none_output(self):
        config = AvatarConfig(enabled=True, camera_output="none")
        vcam = create_virtual_camera(config)
        assert vcam is None


# ── VirtualCamera ────────────────────────────────────────────────────────────


class TestVirtualCamera:
    def test_init_defaults(self):
        vcam = VirtualCamera()
        assert vcam._width == 256
        assert vcam._height == 256
        assert vcam._fps == 25
        assert not vcam.is_running

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        vcam = VirtualCamera()
        await vcam.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_send_idle_frame_without_start(self):
        vcam = VirtualCamera()
        await vcam.send_idle_frame()  # should not raise

    @pytest.mark.asyncio
    async def test_stream_video_without_start(self):
        vcam = VirtualCamera()
        await vcam.stream_video("/fake/video.mp4")  # should log warning, not raise


# ── AvatarConfig ─────────────────────────────────────────────────────────────


class TestAvatarConfig:
    def test_defaults(self):
        config = AvatarConfig()
        assert not config.enabled
        assert config.model == "sadtalker"
        assert config.render_width == 256
        assert config.render_height == 256
        assert config.camera_output == "auto"
        assert config.idle_photo
        assert config.device == "cpu"
        assert config.enhancer == "gfpgan"

    def test_custom_values(self):
        config = AvatarConfig(
            enabled=True,
            model="liveportrait",
            render_width=512,
            render_height=512,
            camera_output="none",
            device="mps",
            enhancer=None,
        )
        assert config.enabled
        assert config.model == "liveportrait"
        assert config.render_width == 512
        assert config.enhancer is None
