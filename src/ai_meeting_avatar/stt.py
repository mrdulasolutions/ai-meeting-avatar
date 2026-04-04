"""
Speech-to-text using faster-whisper (local Whisper inference).

Supports:
  - Batch transcription of a numpy audio array
  - Built-in VAD filtering (silero VAD via faster-whisper)
  - Automatic resampling to 16 kHz (Whisper's native rate)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.signal import resample as scipy_resample

logger = logging.getLogger(__name__)

WHISPER_SAMPLE_RATE = 16_000


@dataclass
class TranscriptionResult:
    text: str
    language: str
    language_probability: float = 1.0
    duration: float = 0.0


class WhisperSTT:
    """
    Local speech-to-text using faster-whisper.

    Usage::

        stt = WhisperSTT(model_size="base", device="cpu")
        stt.load()
        result = await stt.transcribe(audio_np, sample_rate=16000)
        print(result.text)
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: Optional[str] = "en",
        beam_size: int = 5,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language if language != "auto" else None
        self._beam_size = beam_size
        self._model = None
        self._executor = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Download (first run) and load the Whisper model into memory."""
        from faster_whisper import WhisperModel  # noqa: PLC0415

        logger.info(
            "Loading Whisper '%s' on %s (%s) …",
            self._model_size,
            self._device,
            self._compute_type,
        )
        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )
        logger.info("Whisper model ready.")

    # ── Transcription ──────────────────────────────────────────────────────────

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = WHISPER_SAMPLE_RATE,
    ) -> TranscriptionResult:
        """
        Transcribe audio asynchronously (runs Whisper in a thread-pool executor).

        Args:
            audio:       1-D float32 or int16 numpy array.
            sample_rate: Sample rate of *audio*. Resampled to 16 kHz if needed.

        Returns:
            TranscriptionResult with the transcribed text.
        """
        if self._model is None:
            raise RuntimeError("Call WhisperSTT.load() before transcribing.")

        audio = self._prepare_audio(audio, sample_rate)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._run_inference, audio)
        return result

    def transcribe_sync(
        self,
        audio: np.ndarray,
        sample_rate: int = WHISPER_SAMPLE_RATE,
    ) -> TranscriptionResult:
        """Synchronous transcription (for use outside asyncio)."""
        if self._model is None:
            raise RuntimeError("Call WhisperSTT.load() before transcribing.")
        audio = self._prepare_audio(audio, sample_rate)
        return self._run_inference(audio)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _prepare_audio(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Ensure audio is float32 mono at 16 kHz."""
        # Flatten stereo to mono
        if audio.ndim == 2:
            audio = audio.mean(axis=1)

        # Convert int16 → float32
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32_768.0
        elif audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Resample if needed
        if sample_rate != WHISPER_SAMPLE_RATE:
            num_samples = int(len(audio) * WHISPER_SAMPLE_RATE / sample_rate)
            audio = scipy_resample(audio, num_samples).astype(np.float32)

        return audio

    def _run_inference(self, audio: np.ndarray) -> TranscriptionResult:
        segments, info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 200},
            word_timestamps=False,
        )
        # Consume the generator (faster-whisper is lazy)
        text = " ".join(seg.text for seg in segments).strip()
        return TranscriptionResult(
            text=text,
            language=info.language,
            language_probability=info.language_probability,
            duration=info.duration,
        )


# ── Simple energy-based VAD ────────────────────────────────────────────────────


class EnergyVAD:
    """
    Lightweight energy-based voice-activity detector.

    Accumulates 20ms audio frames and flushes a speech segment when enough
    silence has elapsed after speech is detected.
    """

    def __init__(
        self,
        sample_rate: int = 16_000,
        frame_duration_ms: int = 20,
        energy_threshold: float = 0.02,
        silence_frames_threshold: int = 50,
    ) -> None:
        self._sample_rate = sample_rate
        self._frame_samples = int(sample_rate * frame_duration_ms / 1000)
        self._energy_threshold = energy_threshold
        self._silence_threshold = silence_frames_threshold
        self._buffer: list[np.ndarray] = []
        self._silence_count: int = 0
        self._speech_detected: bool = False

    def push_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """
        Push a 20ms audio frame.

        Returns a complete speech segment as a numpy array when speech ends,
        or None if still accumulating.
        """
        # Normalize to float32 if needed
        if frame.dtype == np.int16:
            frame = frame.astype(np.float32) / 32_768.0

        energy = float(np.sqrt(np.mean(frame**2)))
        is_speech = energy > self._energy_threshold

        if is_speech:
            self._speech_detected = True
            self._silence_count = 0
            self._buffer.append(frame)
            return None

        if self._speech_detected:
            self._buffer.append(frame)  # include trailing silence
            self._silence_count += 1

            if self._silence_count >= self._silence_threshold:
                segment = np.concatenate(self._buffer)
                self._buffer = []
                self._silence_count = 0
                self._speech_detected = False
                return segment

        return None

    def flush(self) -> Optional[np.ndarray]:
        """Flush any remaining buffered audio."""
        if self._buffer:
            segment = np.concatenate(self._buffer)
            self._buffer = []
            self._silence_count = 0
            self._speech_detected = False
            return segment
        return None
