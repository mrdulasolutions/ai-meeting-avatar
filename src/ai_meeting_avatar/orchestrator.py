"""
Orchestrator — ties STT → LLM → TTS into a LiveKit agent.

Architecture
────────────
1.  The agent joins a LiveKit room as a participant.
2.  It subscribes to every remote audio track.
3.  Each audio frame is fed into an EnergyVAD.
4.  When VAD detects end-of-speech it fires the pipeline:
      audio → WhisperSTT → OllamaLLM → CoquiXTTS → AudioSource → room
5.  (Phase 2) The synthesised audio also drives the AvatarRenderer and the
    result is pushed to OBS via OBSVirtualCamera.

One pipeline task runs at a time per agent instance; overlapping utterances
are queued rather than processed concurrently so the LLM context is coherent.

LiveKit docs: https://docs.livekit.io/agents/
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from livekit import rtc

from .avatar import AvatarRenderer, OBSVirtualCamera, create_renderer
from .config import AppConfig, load_config
from .llm import ChatHistory, OllamaLLM
from .stt import EnergyVAD, WhisperSTT
from .tts import CoquiXTTS

logger = logging.getLogger(__name__)


# ── Agent ──────────────────────────────────────────────────────────────────────


class MeetingAvatarAgent:
    """
    Core agent logic — model loading, track subscription, pipeline execution.

    This class is framework-agnostic; it only depends on livekit-agents' rtc
    primitives so it can be tested independently of the worker harness.
    """

    def __init__(self, config: AppConfig) -> None:
        self._cfg = config

        self._stt = WhisperSTT(
            model_size=config.stt.model_size,
            device=config.stt.device,
            compute_type=config.stt.compute_type,
            language=config.stt.language,
        )
        self._llm = OllamaLLM(
            model=config.llm.model,
            host=config.llm.host,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            system_prompt=config.agent.system_prompt,
        )
        self._tts = CoquiXTTS(
            model_name=config.tts.model,
            speaker_wav=config.tts.speaker_wav,
            language=config.tts.language,
            speed=config.tts.speed,
            gpu=config.tts.gpu,
        )
        self._avatar: AvatarRenderer = create_renderer(config.avatar)
        self._obs: Optional[OBSVirtualCamera] = None

        self._history = ChatHistory(
            system_prompt=config.agent.system_prompt,
            max_turns=config.llm.history_turns,
        )

        # Serialise pipeline calls so LLM context stays coherent
        self._pipeline_lock = asyncio.Lock()

    # ── Model loading ──────────────────────────────────────────────────────────

    async def load_models(self) -> None:
        logger.info("Loading models …")
        # STT and TTS are CPU-bound; load them sequentially to avoid OOM spikes
        self._stt.load()
        self._tts.load()
        self._llm.load()
        await self._avatar.load()

        if self._cfg.obs.enabled:
            self._obs = OBSVirtualCamera(
                host=self._cfg.obs.host,
                port=self._cfg.obs.port,
                password=self._cfg.obs.password,
                source_name=self._cfg.obs.source_name,
            )
            await self._obs.connect()

        logger.info("All models loaded — agent ready.")

    # ── Room entry ─────────────────────────────────────────────────────────────

    async def run(self, room: rtc.Room, audio_source: rtc.AudioSource) -> None:
        """
        Attach to *room* and start processing audio.

        This coroutine returns only when the room disconnects.
        """
        self._audio_source = audio_source

        # Subscribe to audio tracks that already exist
        for participant in room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    asyncio.ensure_future(
                        self._handle_audio_track(pub.track)  # type: ignore[arg-type]
                    )

        # Subscribe to future tracks
        @room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            pub: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(self._handle_audio_track(track))  # type: ignore[arg-type]

        logger.info("Agent running in room '%s'. Listening …", room.name)

        disconnected = asyncio.Event()
        room.on("disconnected", lambda: disconnected.set())
        await disconnected.wait()
        logger.info("Room disconnected — agent shutting down.")

    # ── Audio track handler ────────────────────────────────────────────────────

    async def _handle_audio_track(self, track: rtc.AudioTrack) -> None:
        """Consume frames from one remote participant's audio track."""
        cfg = self._cfg.stt
        vad = EnergyVAD(
            sample_rate=48_000,          # LiveKit default; resampled by WhisperSTT
            energy_threshold=cfg.vad_energy_threshold,
            silence_frames_threshold=cfg.silence_frames_threshold,
        )

        audio_stream = rtc.AudioStream(track)
        logger.info("Subscribed to audio track: %s", track.sid)

        try:
            async for event in audio_stream:
                if not isinstance(event, rtc.AudioFrameEvent):
                    continue

                frame: rtc.AudioFrame = event.frame
                pcm = np.frombuffer(frame.data, dtype=np.int16)

                segment = vad.push_frame(pcm)
                if segment is not None and segment.size > 0:
                    asyncio.ensure_future(
                        self._run_pipeline(segment, frame.sample_rate)
                    )
        except asyncio.CancelledError:
            # Flush any remaining audio
            remaining = vad.flush()
            if remaining is not None and remaining.size > 0:
                await self._run_pipeline(remaining, 48_000)
        except Exception:
            logger.exception("Error in audio track handler")

    # ── STT → LLM → TTS pipeline ──────────────────────────────────────────────

    async def _run_pipeline(self, audio: np.ndarray, sample_rate: int) -> None:
        """Full pipeline: speech → text → LLM → synthesised speech → room."""
        async with self._pipeline_lock:
            await self._pipeline_inner(audio, sample_rate)

    async def _pipeline_inner(self, audio: np.ndarray, sample_rate: int) -> None:
        # ── STT ────────────────────────────────────────────────────────────────
        try:
            result = await self._stt.transcribe(audio, sample_rate)
        except Exception:
            logger.exception("STT failed")
            return

        text = result.text.strip()
        if not text:
            logger.debug("STT returned empty transcript — skipping.")
            return

        logger.info("[STT] %s", text)

        # ── LLM ────────────────────────────────────────────────────────────────
        try:
            reply = await self._llm.chat(self._history, text)
        except Exception:
            logger.exception("LLM failed")
            return

        reply = reply.strip()
        logger.info("[LLM] %s", reply)

        # Update history after a successful round-trip
        self._history.add_user(text)
        self._history.add_assistant(reply)

        # ── TTS ────────────────────────────────────────────────────────────────
        try:
            await self._speak(reply)
        except Exception:
            logger.exception("TTS / audio publish failed")

    async def _speak(self, text: str) -> None:
        """Synthesise *text* and publish audio to the LiveKit room."""
        # Phase 2: also render avatar video and push to OBS
        if self._cfg.avatar.enabled:
            await self._speak_with_avatar(text)
        else:
            await self._speak_audio_only(text)

    async def _speak_audio_only(self, text: str) -> None:
        """Synthesise and stream audio chunks directly into the room."""
        chunk_samples = self._cfg.tts.sample_rate // 10  # 100ms chunks

        async for chunk_int16, sr in self._tts.iter_audio_chunks(
            text, chunk_samples=chunk_samples
        ):
            frame = rtc.AudioFrame(
                data=chunk_int16.tobytes(),
                sample_rate=sr,
                num_channels=1,
                samples_per_channel=len(chunk_int16),
            )
            await self._audio_source.capture_frame(frame)
            # Real-time pacing: sleep for the duration of the chunk
            await asyncio.sleep(len(chunk_int16) / sr)

    async def _speak_with_avatar(self, text: str) -> None:
        """Synthesise audio, render avatar video, push both to room + OBS."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = str(Path(tmp) / "reply.wav")
            video_path = str(Path(tmp) / "avatar.mp4")

            # Write synthesised audio to disk for SadTalker/LivePortrait
            wav_bytes = await self._tts.synthesize_to_wav_bytes(text)
            Path(audio_path).write_bytes(wav_bytes)

            # Render avatar video (can be slow on CPU)
            rendered = await self._avatar.render(audio_path, video_path)
            logger.info("Avatar rendered: %s", rendered)

            # Push to OBS
            if self._obs is not None:
                await self._obs.update_source(rendered)

            # Also publish audio to LiveKit room
            audio, sr = await self._tts.synthesize(text)
            audio_int16 = (np.clip(audio, -1.0, 1.0) * 32_767).astype(np.int16)
            chunk_size = sr // 10  # 100ms

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


# ── LiveKit worker entrypoint ──────────────────────────────────────────────────


async def entrypoint(ctx) -> None:
    """
    Called by the LiveKit worker harness for each room job.

    The function signature matches livekit-agents JobContext.
    """
    config = load_config(os.getenv("AI_AVATAR_CONFIG", "config.yaml"))

    agent = MeetingAvatarAgent(config)

    await ctx.connect()

    # Publish our audio track before loading models so we appear in the room
    audio_source = rtc.AudioSource(
        sample_rate=config.tts.sample_rate,
        num_channels=1,
    )
    local_track = rtc.LocalAudioTrack.create_audio_track("avatar-voice", audio_source)
    pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await ctx.room.local_participant.publish_track(local_track, pub_options)

    logger.info("Loading models — this may take 30-120 s on first run …")
    await agent.load_models()

    await agent.run(ctx.room, audio_source)
