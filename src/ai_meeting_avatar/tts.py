"""
Text-to-speech with voice cloning using Coqui XTTS-v2 (fully local).

XTTS-v2 can clone a voice from a 6-30 second reference WAV and synthesise
speech in multiple languages at 24 kHz.

Install: pip install TTS
Model downloads automatically on first load (~1.8 GB).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import scipy.io.wavfile as wav_io

logger = logging.getLogger(__name__)

OUTPUT_SAMPLE_RATE = 24_000


class CoquiXTTS:
    """
    Voice-cloning TTS using Coqui XTTS-v2.

    Usage::

        tts = CoquiXTTS(speaker_wav="./assets/voice_samples/speaker.wav")
        tts.load()
        audio, sr = await tts.synthesize("Hello, how are you?")
    """

    def __init__(
        self,
        model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
        speaker_wav: Optional[str] = None,
        language: str = "en",
        speed: float = 1.0,
        gpu: bool = False,
    ) -> None:
        self._model_name = model_name
        self._speaker_wav = speaker_wav
        self._language = language
        self._speed = speed
        self._gpu = gpu
        self._tts = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load XTTS-v2 model (downloads on first run, ~1.8 GB)."""
        from TTS.api import TTS  # noqa: PLC0415

        logger.info("Loading TTS model '%s' (gpu=%s) …", self._model_name, self._gpu)
        # Suppress Coqui's interactive license prompt in CI / non-TTY environments
        os.environ.setdefault("COQUI_TOS_AGREED", "1")
        self._tts = TTS(self._model_name, gpu=self._gpu)
        logger.info("TTS model ready.")

        if self._speaker_wav and not Path(self._speaker_wav).exists():
            logger.warning(
                "Speaker WAV not found: %s — voice cloning will fail. "
                "Add a 6-30 s WAV clip to that path.",
                self._speaker_wav,
            )

    # ── Synthesis ──────────────────────────────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        speaker_wav: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Tuple[np.ndarray, int]:
        """
        Synthesise *text* and return (audio_float32, sample_rate).

        Args:
            text:        Text to synthesise.
            speaker_wav: Override the default speaker WAV for this call.
            language:    Override the default language code.

        Returns:
            Tuple of (float32 numpy array in [-1, 1], sample rate in Hz).
        """
        if self._tts is None:
            raise RuntimeError("Call CoquiXTTS.load() before synthesising.")

        wav_path = speaker_wav or self._speaker_wav
        if not wav_path:
            raise ValueError("speaker_wav must be set — needed for voice cloning.")
        if not Path(wav_path).exists():
            raise FileNotFoundError(f"Speaker WAV not found: {wav_path}")

        lang = language or self._language

        loop = asyncio.get_event_loop()
        audio, sr = await loop.run_in_executor(
            None, self._synthesize_sync, text, wav_path, lang
        )
        return audio, sr

    async def synthesize_to_wav_bytes(
        self,
        text: str,
        speaker_wav: Optional[str] = None,
        language: Optional[str] = None,
    ) -> bytes:
        """Synthesise and return raw WAV bytes (for streaming over network)."""
        audio, sr = await self.synthesize(text, speaker_wav, language)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32_767).astype(np.int16)
        buf = io.BytesIO()
        wav_io.write(buf, sr, audio_int16)
        return buf.getvalue()

    async def synthesize_to_pcm_int16(
        self,
        text: str,
        speaker_wav: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Tuple[bytes, int]:
        """
        Synthesise and return raw PCM int16 bytes + sample rate.
        Suitable for feeding directly into LiveKit AudioFrame.
        """
        audio, sr = await self.synthesize(text, speaker_wav, language)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32_767).astype(np.int16)
        return audio_int16.tobytes(), sr

    # ── Sync helper (runs in thread pool) ─────────────────────────────────────

    def _synthesize_sync(
        self, text: str, speaker_wav: str, language: str
    ) -> Tuple[np.ndarray, int]:
        """Run XTTS inference synchronously (called from thread pool)."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            self._tts.tts_to_file(
                text=text,
                speaker_wav=speaker_wav,
                language=language,
                file_path=tmp_path,
                speed=self._speed,
            )
            sr, data = wav_io.read(tmp_path)
            if data.dtype == np.int16:
                audio = data.astype(np.float32) / 32_768.0
            elif data.dtype == np.float32:
                audio = data
            else:
                audio = data.astype(np.float32)

            return audio, sr
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── Chunked streaming helper ───────────────────────────────────────────────

    async def iter_audio_chunks(
        self,
        text: str,
        chunk_samples: int = 2400,  # 100ms at 24 kHz
        speaker_wav: Optional[str] = None,
        language: Optional[str] = None,
    ):
        """
        Async generator that yields (chunk_int16, sample_rate) tuples.

        Use this to stream audio into a LiveKit AudioSource frame by frame
        without buffering the entire synthesis.
        """
        audio, sr = await self.synthesize(text, speaker_wav, language)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32_767).astype(np.int16)

        for start in range(0, len(audio_int16), chunk_samples):
            chunk = audio_int16[start : start + chunk_samples]
            # Pad last chunk to full size
            if len(chunk) < chunk_samples:
                chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))
            yield chunk, sr
            # Yield control between chunks so the event loop stays responsive
            await asyncio.sleep(0)
