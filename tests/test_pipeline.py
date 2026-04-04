"""
Unit tests for the STT → LLM → TTS pipeline.

All tests use mocks so they run without real models, GPU, or a running Ollama.
Run: pytest tests/
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from ai_meeting_avatar.config import AppConfig, LLMConfig, STTConfig, TTSConfig
from ai_meeting_avatar.llm import ChatHistory, OllamaLLM
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
    def test_messages_include_system_prompt(self):
        h = ChatHistory(system_prompt="Be concise.")
        h.add_user("hello")
        h.add_assistant("hi")
        msgs = h.to_ollama_messages()
        assert msgs[0] == {"role": "system", "content": "Be concise."}
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"

    def test_max_turns_rolling(self):
        h = ChatHistory(max_turns=2)
        for i in range(5):
            h.add_user(f"msg {i}")
            h.add_assistant(f"reply {i}")
        msgs = h.to_ollama_messages()
        # Should only contain the last 2 turns (4 messages)
        assert len(msgs) == 4


class TestOllamaLLM:
    @pytest.mark.asyncio
    async def test_chat_returns_string(self):
        llm = OllamaLLM(model="llama3.2")
        mock_client = AsyncMock()
        mock_client.chat.return_value = {"message": {"content": "Paris"}}
        llm._client = mock_client

        history = ChatHistory()
        reply = await llm.chat(history, "Capital of France?")
        assert reply == "Paris"

    @pytest.mark.asyncio
    async def test_chat_raises_without_load(self):
        llm = OllamaLLM()
        with pytest.raises(RuntimeError, match="load()"):
            await llm.chat(ChatHistory(), "hello")

    @pytest.mark.asyncio
    async def test_system_prompt_in_messages(self):
        llm = OllamaLLM(system_prompt="You are terse.")
        mock_client = AsyncMock()
        mock_client.chat.return_value = {"message": {"content": "ok"}}
        llm._client = mock_client

        history = ChatHistory(system_prompt="You are terse.")
        await llm.chat(history, "hello")

        call_args = mock_client.chat.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"


# ── TTS tests ─────────────────────────────────────────────────────────────────


class TestCoquiXTTS:
    @pytest.mark.asyncio
    async def test_synthesize_raises_without_load(self):
        tts = CoquiXTTS(speaker_wav="speaker.wav")
        with pytest.raises(RuntimeError, match="load()"):
            await tts.synthesize("hello")

    @pytest.mark.asyncio
    async def test_synthesize_raises_without_speaker_wav(self):
        tts = CoquiXTTS()
        tts._tts = MagicMock()  # bypass load
        with pytest.raises(ValueError, match="speaker_wav"):
            await tts.synthesize("hello")

    @pytest.mark.asyncio
    async def test_synthesize_raises_wav_not_found(self):
        tts = CoquiXTTS(speaker_wav="/nonexistent/speaker.wav")
        tts._tts = MagicMock()
        with pytest.raises(FileNotFoundError):
            await tts.synthesize("hello")

    @pytest.mark.asyncio
    async def test_iter_audio_chunks_yields_correct_shape(self):
        tts = CoquiXTTS(speaker_wav="speaker.wav")

        # Patch synthesize to return a known array
        audio = np.zeros(4800, dtype=np.float32)  # 200ms at 24 kHz
        tts.synthesize = AsyncMock(return_value=(audio, 24_000))

        chunks = []
        async for chunk, sr in tts.iter_audio_chunks("hello", chunk_samples=2400):
            chunks.append(chunk)

        assert len(chunks) == 2  # 4800 / 2400 = 2
        assert all(len(c) == 2400 for c in chunks)
        assert sr == 24_000


# ── Config tests ───────────────────────────────────────────────────────────────


class TestConfig:
    def test_default_config_loads(self):
        cfg = AppConfig()
        assert cfg.agent.name == "AI Meeting Avatar"
        assert cfg.stt.model_size == "base"
        assert cfg.llm.model == "llama3.2"

    def test_load_config_from_yaml(self, tmp_path):
        from ai_meeting_avatar.config import load_config

        yaml_content = """
llm:
  model: mistral
  temperature: 0.5
stt:
  model_size: small
"""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content)

        cfg = load_config(cfg_file)
        assert cfg.llm.model == "mistral"
        assert cfg.llm.temperature == 0.5
        assert cfg.stt.model_size == "small"
        # Defaults preserved for unset keys
        assert cfg.agent.name == "AI Meeting Avatar"

    def test_load_config_missing_file_returns_defaults(self):
        from ai_meeting_avatar.config import load_config

        cfg = load_config("/nonexistent/config.yaml")
        assert cfg.llm.model == "llama3.2"
