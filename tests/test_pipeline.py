"""
Unit tests for the STT → LLM → TTS pipeline.

All tests use mocks so they run without real models or downloaded weights.
Run: pytest tests/
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from ai_meeting_avatar.config import AppConfig, LLMConfig, STTConfig, TTSConfig
from ai_meeting_avatar.llm import ChatHistory, GemmaLLM
from ai_meeting_avatar.stt import EnergyVAD, TranscriptionResult, WhisperSTT
from ai_meeting_avatar.tts import CoquiXTTS


# ── STT tests ─────────────────────────────────────────────────────────────────


class TestWhisperSTT:
    def test_prepare_audio_resamples(self):
        stt = WhisperSTT()
        # 1 second at 48 kHz
        audio_48k = np.zeros(48_000, dtype=np.float32)
        resampled = stt._prepare_audio(audio_48k, 48_000)
        assert len(resampled) == 16_000

    def test_prepare_audio_converts_int16(self):
        stt = WhisperSTT()
        audio_i16 = np.array([0, 16384, -16384, 32767], dtype=np.int16)
        out = stt._prepare_audio(audio_i16, 16_000)
        assert out.dtype == np.float32
        assert np.abs(out).max() <= 1.0

    def test_prepare_audio_flattens_stereo(self):
        stt = WhisperSTT()
        stereo = np.zeros((16_000, 2), dtype=np.float32)
        mono = stt._prepare_audio(stereo, 16_000)
        assert mono.ndim == 1

    @pytest.mark.asyncio
    async def test_transcribe_calls_model(self):
        stt = WhisperSTT()
        mock_model = MagicMock()
        mock_info = MagicMock(language="en", language_probability=0.99, duration=1.0)
        mock_segment = MagicMock(text=" Hello world")
        mock_model.transcribe.return_value = ([mock_segment], mock_info)
        stt._model = mock_model

        audio = np.zeros(16_000, dtype=np.float32)
        result = await stt.transcribe(audio, 16_000)

        assert result.text == "Hello world"
        assert result.language == "en"

    @pytest.mark.asyncio
    async def test_transcribe_raises_without_load(self):
        stt = WhisperSTT()
        with pytest.raises(RuntimeError, match="load()"):
            await stt.transcribe(np.zeros(100, dtype=np.float32))


class TestEnergyVAD:
    def _make_frame(self, energy: float, n: int = 320) -> np.ndarray:
        """Create a frame with the given RMS energy."""
        return np.full(n, energy * 32_767, dtype=np.int16)

    def test_silence_returns_none(self):
        vad = EnergyVAD(energy_threshold=0.02)
        for _ in range(200):
            result = vad.push_frame(self._make_frame(0.0))
        assert result is None

    def test_speech_then_silence_returns_segment(self):
        vad = EnergyVAD(energy_threshold=0.02, silence_frames_threshold=5)
        # Push 10 speech frames
        for _ in range(10):
            vad.push_frame(self._make_frame(0.1))
        # Push enough silence to flush
        segment = None
        for _ in range(10):
            segment = vad.push_frame(self._make_frame(0.0))
            if segment is not None:
                break
        assert segment is not None
        assert isinstance(segment, np.ndarray)

    def test_flush_returns_remaining(self):
        vad = EnergyVAD(energy_threshold=0.02)
        vad.push_frame(self._make_frame(0.1))
        seg = vad.flush()
        assert seg is not None


# ── LLM tests ─────────────────────────────────────────────────────────────────


class TestChatHistory:
    def test_add_user_and_assistant_noop(self):
        # History is managed internally by LiteRT-LM; these are no-ops
        h = ChatHistory(system_prompt="Be concise.")
        h.add_user("hello")
        h.add_assistant("hi")
        # No exception raised and system prompt preserved
        assert h.system_prompt == "Be concise."

    def test_clear_noop(self):
        h = ChatHistory()
        h.add_user("hello")
        h.clear()  # should not raise


class TestGemmaLLM:
    @pytest.mark.asyncio
    async def test_chat_returns_string(self):
        llm = GemmaLLM(model_path="/fake/model.litertlm")
        mock_conv = MagicMock()
        mock_conv.send_message.return_value = {
            "content": [{"type": "text", "text": "Paris"}]
        }
        llm._conversation = mock_conv

        history = ChatHistory()
        reply = await llm.chat(history, "Capital of France?")
        assert reply == "Paris"

    @pytest.mark.asyncio
    async def test_chat_raises_without_load(self):
        llm = GemmaLLM(model_path="/fake/model.litertlm")
        with pytest.raises(RuntimeError, match="load()"):
            await llm.chat(ChatHistory(), "hello")

    def test_extract_text_handles_multiple_content_items(self):
        response = {
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "World"},
            ]
        }
        result = GemmaLLM._extract_text(response)
        assert result == "Hello World"

    def test_extract_text_skips_non_text_items(self):
        response = {
            "content": [
                {"type": "tool_call", "name": "mute_microphone"},
                {"type": "text", "text": "Done."},
            ]
        }
        result = GemmaLLM._extract_text(response)
        assert result == "Done."

    def test_extract_text_handles_empty_response(self):
        result = GemmaLLM._extract_text({"content": []})
        assert result == ""

    def test_load_raises_file_not_found(self):
        llm = GemmaLLM(model_path="/nonexistent/model.litertlm")
        with pytest.raises(FileNotFoundError):
            llm.load()

    def test_close_is_idempotent(self):
        llm = GemmaLLM(model_path="/fake/model.litertlm")
        llm.close()  # should not raise even with no engine loaded
        llm.close()  # second call also safe


# ── TTS tests (KokoroTTS via CoquiXTTS alias) ─────────────────────────────────


class TestKokoroTTS:
    @pytest.mark.asyncio
    async def test_synthesize_raises_without_load(self):
        tts = CoquiXTTS(voice="af_heart")
        with pytest.raises(RuntimeError, match="load()"):
            await tts.synthesize("hello")

    @pytest.mark.asyncio
    async def test_synthesize_uses_default_voice(self):
        tts = CoquiXTTS(voice="af_heart")
        mock_kokoro = MagicMock()
        mock_kokoro.create.return_value = (np.zeros(2400, dtype=np.float32), 24_000)
        tts._kokoro = mock_kokoro

        audio, sr = await tts.synthesize("hello")
        mock_kokoro.create.assert_called_once_with(
            "hello", voice="af_heart", speed=tts._speed, lang=tts._lang
        )
        assert sr == 24_000

    @pytest.mark.asyncio
    async def test_synthesize_voice_override(self):
        tts = CoquiXTTS(voice="af_heart")
        mock_kokoro = MagicMock()
        mock_kokoro.create.return_value = (np.zeros(2400, dtype=np.float32), 24_000)
        tts._kokoro = mock_kokoro

        await tts.synthesize("hello", voice="am_adam")
        call_voice = mock_kokoro.create.call_args.kwargs["voice"]
        assert call_voice == "am_adam"

    @pytest.mark.asyncio
    async def test_iter_audio_chunks_yields_correct_shape(self):
        tts = CoquiXTTS(voice="af_heart")
        audio = np.zeros(4800, dtype=np.float32)  # 200ms at 24 kHz
        tts.synthesize = AsyncMock(return_value=(audio, 24_000))

        chunks = []
        async for chunk, sr in tts.iter_audio_chunks("hello", chunk_samples=2400):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert all(len(c) == 2400 for c in chunks)
        assert sr == 24_000

    def test_sample_rate_constant(self):
        assert CoquiXTTS.SAMPLE_RATE == 24_000

    @pytest.mark.asyncio
    async def test_synthesize_to_wav_bytes_returns_bytes(self):
        tts = CoquiXTTS(voice="af_heart")
        audio = np.zeros(2400, dtype=np.float32)
        tts.synthesize = AsyncMock(return_value=(audio, 24_000))

        result = await tts.synthesize_to_wav_bytes("hello")
        assert isinstance(result, bytes)
        assert len(result) > 44  # at least a WAV header


# ── Config tests ───────────────────────────────────────────────────────────────


class TestConfig:
    def test_default_config_loads(self):
        cfg = AppConfig()
        assert cfg.agent.name == "AI Meeting Avatar"
        assert cfg.stt.model_size == "base"
        assert cfg.llm.backend == "gemma"

    def test_load_config_from_yaml(self, tmp_path):
        from ai_meeting_avatar.config import load_config

        yaml_content = """
llm:
  model_variant: e4b
  model_path: ./models/gemma-4-e4b/gemma-4-E4B-it.litertlm
  temperature: 0.5
stt:
  model_size: small
"""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content)

        cfg = load_config(cfg_file)
        assert cfg.llm.model_variant == "e4b"
        assert cfg.llm.temperature == 0.5
        assert cfg.stt.model_size == "small"
        # Defaults preserved for unset keys
        assert cfg.agent.name == "AI Meeting Avatar"

    def test_load_config_missing_file_returns_defaults(self):
        from ai_meeting_avatar.config import load_config

        cfg = load_config("/nonexistent/config.yaml")
        assert cfg.llm.backend == "gemma"
