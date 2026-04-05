"""
Text-to-speech using Kokoro-ONNX (kokoro-onnx).

Kokoro is an 82M-parameter TTS model that runs entirely on CPU via ONNX
Runtime. No GPU, no PyTorch, no espeak, no voice samples required.

Install:  pip install kokoro-onnx onnxruntime soundfile
Models:   ~80 MB total, downloaded automatically on first load.

Available voices (American English, default lang "en-us"):
  af_heart   — warm female        ← project default
  af_sky     — bright female
  af_sarah   — clear female
  af_nova    — expressive female
  am_adam    — natural male
  am_michael — deep male

British English voices (lang "en-gb"):
  bf_emma, bf_isabella, bm_george, bm_lewis

Docs: https://github.com/thewh1teagle/kokoro-onnx
"""

from __future__ import annotations

import asyncio
import io
import logging
import urllib.request
from pathlib import Path
from typing import AsyncGenerator, Tuple

import numpy as np
import scipy.io.wavfile as wav_io

logger = logging.getLogger(__name__)

# ── Model file locations ───────────────────────────────────────────────────────

_MODEL_BASE_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
_MODEL_FILES = {
    "kokoro-v1.0.onnx": f"{_MODEL_BASE_URL}/kokoro-v1.0.onnx",
    "voices-v1.0.bin": f"{_MODEL_BASE_URL}/voices-v1.0.bin",
}

DEFAULT_MODEL_DIR = Path("./models/kokoro")


# ── Model download helper ──────────────────────────────────────────────────────


def download_models(model_dir: Path = DEFAULT_MODEL_DIR) -> Tuple[Path, Path]:
    """
    Download Kokoro model files if not already present.

    Returns (onnx_path, voices_path).
    Safe to call multiple times — skips files that already exist.
    """
    model_dir.mkdir(parents=True, exist_ok=True)

    paths = {}
    for filename, url in _MODEL_FILES.items():
        dest = model_dir / filename
        if dest.exists():
            logger.info("Kokoro model file already present: %s", dest)
        else:
            logger.info("Downloading %s → %s …", filename, dest)
            _download_with_progress(url, dest)
            logger.info("Downloaded %s (%.1f MB)", filename, dest.stat().st_size / 1e6)
        paths[filename] = dest

    return paths["kokoro-v1.0.onnx"], paths["voices-v1.0.bin"]


def _download_with_progress(url: str, dest: Path) -> None:
    """Download *url* to *dest* with a basic progress log every 10 MB."""
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(url) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1 << 20  # 1 MB

            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and downloaded % (10 * chunk_size) < chunk_size:
                        pct = downloaded / total * 100
                        logger.info("  %.0f%%  (%.0f / %.0f MB)", pct, downloaded / 1e6, total / 1e6)

        tmp.rename(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


# ── TTS class ─────────────────────────────────────────────────────────────────


class KokoroTTS:
    """
    Local TTS using Kokoro-ONNX — no GPU, no voice samples, no extra setup.

    Usage::

        tts = KokoroTTS(voice="af_heart")
        tts.load()                                   # downloads models if needed
        audio, sr = await tts.synthesize("Hello!")
    """

    SAMPLE_RATE = 24_000  # Kokoro native output sample rate

    def __init__(
        self,
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str = "en-us",
        model_dir: str = str(DEFAULT_MODEL_DIR),
    ) -> None:
        self._voice = voice
        self._speed = speed
        self._lang = lang
        self._model_dir = Path(model_dir)
        self._kokoro = None  # kokoro_onnx.Kokoro instance

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load Kokoro ONNX model (downloads ~80 MB on first run)."""
        from kokoro_onnx import Kokoro  # noqa: PLC0415

        onnx_path, voices_path = download_models(self._model_dir)
        logger.info("Loading Kokoro TTS (voice=%s, lang=%s) …", self._voice, self._lang)
        self._kokoro = Kokoro(str(onnx_path), str(voices_path))
        logger.info("Kokoro TTS ready.")

    # ── Public synthesis API ───────────────────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        lang: str | None = None,
    ) -> Tuple[np.ndarray, int]:
        """
        Synthesise *text* and return (audio_float32, sample_rate).

        Args:
            text:  Text to speak.
            voice: Override the default voice for this call.
            lang:  Override the default language code.

        Returns:
            Tuple of (float32 numpy array in [-1, 1], sample rate in Hz).
        """
        if self._kokoro is None:
            raise RuntimeError("Call KokoroTTS.load() before synthesising.")

        v = voice or self._voice
        language = lang or self._lang

        loop = asyncio.get_event_loop()
        audio, sr = await loop.run_in_executor(
            None, self._synthesize_sync, text, v, language
        )
        return audio, sr

    async def synthesize_to_wav_bytes(
        self,
        text: str,
        voice: str | None = None,
        lang: str | None = None,
    ) -> bytes:
        """Synthesise and return raw WAV bytes."""
        audio, sr = await self.synthesize(text, voice, lang)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32_767).astype(np.int16)
        buf = io.BytesIO()
        try:
            wav_io.write(buf, sr, audio_int16)
            return buf.getvalue()
        finally:
            buf.close()

    async def synthesize_to_pcm_int16(
        self,
        text: str,
        voice: str | None = None,
        lang: str | None = None,
    ) -> Tuple[bytes, int]:
        """Synthesise and return (raw PCM int16 bytes, sample_rate)."""
        audio, sr = await self.synthesize(text, voice, lang)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32_767).astype(np.int16)
        return audio_int16.tobytes(), sr

    async def iter_audio_chunks(
        self,
        text: str,
        chunk_samples: int = 2400,  # 100 ms at 24 kHz
        voice: str | None = None,
        lang: str | None = None,
    ) -> AsyncGenerator[Tuple[np.ndarray, int], None]:
        """
        Async generator that yields (chunk_int16, sample_rate) tuples.

        Streams synthesised audio into a LiveKit AudioSource chunk by chunk
        so playback begins before the full sentence is synthesised.
        """
        audio, sr = await self.synthesize(text, voice, lang)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32_767).astype(np.int16)

        for start in range(0, len(audio_int16), chunk_samples):
            chunk = audio_int16[start : start + chunk_samples]
            if len(chunk) < chunk_samples:
                chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))
            yield chunk, sr
            await asyncio.sleep(0)  # yield control between chunks

    # ── Sync helper (runs in thread pool) ─────────────────────────────────────

    def _synthesize_sync(self, text: str, voice: str, lang: str) -> Tuple[np.ndarray, int]:
        samples, sample_rate = self._kokoro.create(
            text,
            voice=voice,
            speed=self._speed,
            lang=lang,
        )
        # Ensure float32
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)
        return samples, int(sample_rate)


# ── Backwards-compat alias ────────────────────────────────────────────────────
# Orchestrator previously imported CoquiXTTS; alias avoids touching that file.
CoquiXTTS = KokoroTTS
