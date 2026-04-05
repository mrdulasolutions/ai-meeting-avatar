"""
Orchestrator — direct LiveKit room client for the synced meeting avatar.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import scipy.io.wavfile as wav_io
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

from .avatar import (
    AvatarRenderer,
    NullAvatarRenderer,
    OBSVirtualCamera,
    VirtualCamera,
    create_renderer,
    create_virtual_camera,
)
from .config import AppConfig
from .diagnostics import RUNTIME_ROOM_FILE
from .llm import ChatHistory, GemmaLLM
from .llm_claude import ClaudeLLM
from .stt import EnergyVAD, WhisperSTT
from .tts import CoquiXTTS

logger = logging.getLogger(__name__)


def _build_llm(config: AppConfig) -> GemmaLLM | ClaudeLLM:
    backend = config.llm.backend.lower()
    if backend == "claude":
        return ClaudeLLM(
            api_key=config.llm.anthropic_api_key,
            model=config.llm.claude_model,
            system_prompt=config.agent.system_prompt,
            enable_tools=config.llm.enable_tools,
            max_tokens=config.llm.max_tokens,
            temperature=config.llm.temperature,
            history_turns=config.llm.history_turns,
        )
    if backend == "gemma":
        return GemmaLLM(
            model_path=config.llm.model_path,
            system_prompt=config.agent.system_prompt,
            enable_tools=config.llm.enable_tools,
            max_tokens=config.llm.max_tokens,
            temperature=config.llm.temperature,
        )
    raise ValueError(f"Unknown llm.backend: '{backend}'. Choose 'gemma' or 'claude'.")


class MeetingAvatarAgent:
    def __init__(self, config: AppConfig) -> None:
        self._cfg = config
        self._stt = WhisperSTT(
            model_size=config.stt.model_size,
            device=config.stt.device,
            compute_type=config.stt.compute_type,
            language=config.stt.language,
        )
        self._llm = _build_llm(config)
        self._tts = CoquiXTTS(
            voice=config.tts.voice,
            speed=config.tts.speed,
            lang=config.tts.lang,
            model_dir=config.tts.model_dir,
        )
        self._avatar: AvatarRenderer = create_renderer(config.avatar)
        self._vcam: Optional[VirtualCamera] = create_virtual_camera(config.avatar)
        self._obs: Optional[OBSVirtualCamera] = None
        self._history = ChatHistory(
            system_prompt=config.agent.system_prompt,
            max_turns=config.llm.history_turns,
        )
        self._audio_source: Optional[rtc.AudioSource] = None
        self._pipeline_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def _spawn_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Background task failed: %s", exc, exc_info=exc)

    async def load_models(self) -> None:
        logger.info("Loading models …")
        self._stt.load()
        self._tts.load()
        self._llm.load()

        try:
            await self._avatar.load()
        except Exception:
            logger.exception("Avatar model failed to load — falling back to audio-only mode")
            self._avatar = NullAvatarRenderer()
            self._vcam = None
            self._cfg.avatar.enabled = False

        if self._vcam is not None:
            try:
                await self._vcam.start()
                await self._vcam.send_idle_frame()
            except Exception:
                logger.exception("Failed to start virtual camera — continuing without it")
                self._vcam = None

        use_obs = self._cfg.avatar.camera_output == "obs" or (
            self._cfg.avatar.camera_output == "auto" and self._vcam is None
        )
        if use_obs or self._cfg.obs.enabled:
            try:
                self._obs = OBSVirtualCamera(
                    host=self._cfg.obs.host,
                    port=self._cfg.obs.port,
                    password=self._cfg.obs.password,
                    source_name=self._cfg.obs.source_name,
                )
                await self._obs.connect()
            except Exception:
                logger.exception("OBS connection failed — continuing without OBS")
                self._obs = None

        logger.info("All models loaded — agent ready.")

    async def run(self, room: rtc.Room, audio_source: rtc.AudioSource) -> None:
        self._audio_source = audio_source

        for participant in room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    self._spawn_task(self._handle_audio_track(pub.track))  # type: ignore[arg-type]

        @room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            pub: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                self._spawn_task(self._handle_audio_track(track))  # type: ignore[arg-type]

        disconnected = asyncio.Event()
        room.on("disconnected", lambda: disconnected.set())
        await disconnected.wait()

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._vcam is not None:
            await self._vcam.stop()
        if self._obs is not None:
            try:
                await self._obs.disconnect()
            except Exception:
                logger.debug("OBS disconnect failed (may already be closed).")

    async def _handle_audio_track(self, track: rtc.AudioTrack) -> None:
        cfg = self._cfg.stt
        vad = EnergyVAD(
            sample_rate=48_000,
            energy_threshold=cfg.vad_energy_threshold,
            silence_frames_threshold=cfg.silence_frames_threshold,
        )
        audio_stream = rtc.AudioStream(track)

        try:
            async for event in audio_stream:
                if not isinstance(event, rtc.AudioFrameEvent):
                    continue
                frame: rtc.AudioFrame = event.frame
                pcm = np.frombuffer(frame.data, dtype=np.int16)
                segment = vad.push_frame(pcm)
                if segment is not None and segment.size > 0:
                    self._spawn_task(self._run_pipeline(segment, frame.sample_rate))
        except asyncio.CancelledError:
            remaining = vad.flush()
            if remaining is not None and remaining.size > 0:
                await self._run_pipeline(remaining, 48_000)
        except Exception:
            logger.exception("Error in audio track handler")

    async def _run_pipeline(self, audio: np.ndarray, sample_rate: int) -> None:
        async with self._pipeline_lock:
            await self._pipeline_inner(audio, sample_rate)

    async def _pipeline_inner(self, audio: np.ndarray, sample_rate: int) -> None:
        try:
            result = await self._stt.transcribe(audio, sample_rate)
        except Exception:
            logger.exception("STT failed")
            return

        text = result.text.strip()
        if not text:
            return

        self._history.add_user(text)
        try:
            reply = await self._llm.chat(self._history, text)
        except Exception:
            logger.exception("LLM failed")
            return

        reply = reply.strip()
        self._history.add_assistant(reply)

        try:
            await self._speak(reply)
        except Exception:
            logger.exception("TTS / audio publish failed")

    async def _speak(self, text: str) -> None:
        if self._audio_source is None:
            raise RuntimeError("Audio source is not ready.")
        if self._cfg.avatar.enabled:
            await self._speak_with_avatar(text)
        else:
            await self._speak_audio_only(text)

    async def _speak_audio_only(self, text: str) -> None:
        chunk_samples = self._cfg.tts.sample_rate // 10
        async for chunk_int16, sr in self._tts.iter_audio_chunks(text, chunk_samples=chunk_samples):
            frame = rtc.AudioFrame(
                data=chunk_int16.tobytes(),
                sample_rate=sr,
                num_channels=1,
                samples_per_channel=len(chunk_int16),
            )
            await self._audio_source.capture_frame(frame)
            await asyncio.sleep(len(chunk_int16) / sr)

    async def _speak_with_avatar(self, text: str) -> None:
        audio_float, sr = await self._tts.synthesize(text)
        audio_int16 = (np.clip(audio_float, -1.0, 1.0) * 32_767).astype(np.int16)

        with tempfile.TemporaryDirectory(prefix="avatar_") as tmp_dir:
            audio_path = str(Path(tmp_dir) / "reply.wav")
            wav_io.write(audio_path, sr, audio_int16)
            render_task = asyncio.create_task(self._render_and_stream_video(audio_path, tmp_dir))

            chunk_size = sr // 10
            for start in range(0, len(audio_int16), chunk_size):
                chunk = audio_int16[start : start + chunk_size]
                if len(chunk) < chunk_size:
                    chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
                frame = rtc.AudioFrame(
                    data=chunk.tobytes(),
                    sample_rate=sr,
                    num_channels=1,
                    samples_per_channel=len(chunk),
                )
                await self._audio_source.capture_frame(frame)
                await asyncio.sleep(len(chunk) / sr)

            try:
                await render_task
            except Exception:
                logger.exception("Avatar render/stream failed — audio was still delivered")

    async def _render_and_stream_video(self, audio_path: str, output_dir: str) -> None:
        rendered = await self._avatar.render(audio_path, output_dir)
        if not rendered:
            return
        if self._vcam is not None:
            await self._vcam.stream_video(rendered)
        if self._obs is not None:
            await self._obs.update_source(rendered)


def create_room_token(
    config: AppConfig,
    room_name: str,
    identity: str,
    display_name: str,
    *,
    hidden: bool = True,
    kind: str = "agent",
) -> str:
    return (
        AccessToken(config.livekit.api_key, config.livekit.api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_kind(kind)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                hidden=hidden,
            )
        )
        .to_jwt()
    )


async def join_room(config: AppConfig, room_name: str, identity: str | None = None) -> None:
    room = rtc.Room()
    agent = MeetingAvatarAgent(config)

    participant_identity = identity or "ai-meeting-avatar"
    token = create_room_token(config, room_name, participant_identity, config.agent.name)

    await room.connect(config.livekit.url, token)
    RUNTIME_ROOM_FILE.write_text(room_name)

    audio_source = rtc.AudioSource(sample_rate=config.tts.sample_rate, num_channels=1)
    local_track = rtc.LocalAudioTrack.create_audio_track("avatar-voice", audio_source)
    pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await room.local_participant.publish_track(local_track, pub_options)

    try:
        await agent.load_models()
        await agent.run(room, audio_source)
    finally:
        if room.isconnected():
            await room.disconnect()
        if RUNTIME_ROOM_FILE.exists():
            RUNTIME_ROOM_FILE.unlink()
