#!/usr/bin/env python3
"""
Standalone pipeline smoke-test.

Runs without LiveKit — useful for verifying models work before joining a call.

Usage:
    python scripts/test_pipeline.py
    python scripts/test_pipeline.py --text "What is the capital of France?"
    python scripts/test_pipeline.py --wav path/to/audio.wav
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project src is on sys.path when run as a standalone script
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_meeting_avatar.config import load_config
from ai_meeting_avatar.llm import ChatHistory, OllamaLLM
from ai_meeting_avatar.stt import WhisperSTT
from ai_meeting_avatar.tts import CoquiXTTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Test STT → LLM → TTS pipeline")
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    p.add_argument("--text", help="Skip STT and feed this text directly to the LLM")
    p.add_argument("--wav", help="Path to a WAV file to transcribe instead of recording")
    p.add_argument("--duration", type=float, default=5.0, help="Mic recording duration (s)")
    p.add_argument("--no-play", action="store_true", help="Skip audio playback")
    p.add_argument("--output", help="Save synthesised audio to this WAV file")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)

    # ── Load models ────────────────────────────────────────────────────────────
    logger.info("Loading STT (Whisper %s) …", cfg.stt.model_size)
    stt = WhisperSTT(
        model_size=cfg.stt.model_size,
        device=cfg.stt.device,
        compute_type=cfg.stt.compute_type,
        language=cfg.stt.language,
    )
    stt.load()

    logger.info("Initialising LLM (Ollama %s) …", cfg.llm.model)
    llm = OllamaLLM(
        model=cfg.llm.model,
        host=cfg.llm.host,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
        system_prompt=cfg.agent.system_prompt,
    )
    llm.load()

    logger.info("Loading TTS (XTTS-v2) …")
    tts = CoquiXTTS(
        model_name=cfg.tts.model,
        speaker_wav=cfg.tts.speaker_wav,
        language=cfg.tts.language,
        speed=cfg.tts.speed,
        gpu=cfg.tts.gpu,
    )
    tts.load()

    # ── STT ────────────────────────────────────────────────────────────────────
    if args.text:
        transcript = args.text
        print(f"\n[STT skipped] Text: {transcript}")
    elif args.wav:
        import numpy as np
        import scipy.io.wavfile as wav_io

        logger.info("Reading WAV: %s", args.wav)
        sr, data = wav_io.read(args.wav)
        if data.ndim == 2:
            data = data.mean(axis=1)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32_768.0

        logger.info("Transcribing …")
        result = await stt.transcribe(data, sr)
        transcript = result.text.strip()
        print(f"\n[STT] Language: {result.language} | Text: {transcript}")
    else:
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError:
            print("sounddevice not installed. Use --text or --wav instead.")
            sys.exit(1)

        print(f"\nRecording {args.duration}s from microphone — speak now …")
        audio = sd.rec(int(args.duration * 16_000), samplerate=16_000, channels=1, dtype="float32")
        sd.wait()
        audio = audio.flatten()

        logger.info("Transcribing …")
        result = await stt.transcribe(audio, 16_000)
        transcript = result.text.strip()

        if not transcript:
            print("No speech detected. Try --text or increase --duration.")
            sys.exit(1)

        print(f"\n[STT] Language: {result.language} | Text: {transcript}")

    # ── LLM ────────────────────────────────────────────────────────────────────
    print("\nGenerating LLM response …")
    history = ChatHistory(system_prompt=cfg.agent.system_prompt)
    reply = await llm.chat(history, transcript)
    print(f"\n[LLM] {reply}")

    # ── TTS ────────────────────────────────────────────────────────────────────
    print("\nSynthesising speech …")

    if not Path(cfg.tts.speaker_wav).exists():
        print(
            f"\n[WARNING] Speaker WAV not found at {cfg.tts.speaker_wav}.\n"
            "Add a 6-30 s voice sample and update config.yaml (tts.speaker_wav).\n"
            "Skipping TTS."
        )
        return

    audio_out, sr = await tts.synthesize(reply)
    print(f"[TTS] Synthesised {len(audio_out)/sr:.2f}s of audio at {sr} Hz")

    if args.output:
        import scipy.io.wavfile as wav_io
        import numpy as np
        wav_io.write(args.output, sr, (np.clip(audio_out, -1, 1) * 32_767).astype("int16"))
        print(f"[TTS] Saved to {args.output}")

    if not args.no_play:
        try:
            import sounddevice as sd
            print("Playing …")
            sd.play(audio_out, sr)
            sd.wait()
            print("Done.")
        except ImportError:
            print("sounddevice not installed — use --output to save the WAV instead.")


if __name__ == "__main__":
    asyncio.run(main())
