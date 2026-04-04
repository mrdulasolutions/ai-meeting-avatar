"""
Configuration loading and validation using Pydantic.

Priority (highest → lowest):
  1. Environment variables (LIVEKIT_URL, LIVEKIT_API_KEY, …)
  2. config.yaml values
  3. Field defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, model_validator


# ── Sub-configs ────────────────────────────────────────────────────────────────


class AgentConfig(BaseModel):
    name: str = "AI Meeting Avatar"
    system_prompt: str = (
        "You are a helpful AI meeting assistant. "
        "Keep responses concise and conversational — this is a live voice call."
    )
    language: str = "en"
    silence_flush_seconds: float = 1.0


class LiveKitConfig(BaseModel):
    url: str = "ws://localhost:7880"
    api_key: str = "devkey"
    api_secret: str = "secret"
    room: str = ""

    @model_validator(mode="after")
    def _override_from_env(self) -> "LiveKitConfig":
        if url := os.getenv("LIVEKIT_URL"):
            self.url = url
        if key := os.getenv("LIVEKIT_API_KEY"):
            self.api_key = key
        if secret := os.getenv("LIVEKIT_API_SECRET"):
            self.api_secret = secret
        return self


class STTConfig(BaseModel):
    model_size: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "en"
    vad_energy_threshold: float = 0.02
    silence_frames_threshold: int = 50  # × 20ms frames

    @model_validator(mode="after")
    def _override_from_env(self) -> "STTConfig":
        if device := os.getenv("STT_DEVICE"):
            self.device = device
        return self


class LLMConfig(BaseModel):
    host: str = "http://localhost:11434"
    model: str = "llama3.2"
    temperature: float = 0.7
    max_tokens: int = 256
    history_turns: int = 10

    @model_validator(mode="after")
    def _override_from_env(self) -> "LLMConfig":
        if host := os.getenv("OLLAMA_HOST"):
            self.host = host
        return self


class TTSConfig(BaseModel):
    model: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    speaker_wav: str = "./assets/voice_samples/speaker.wav"
    language: str = "en"
    speed: float = 1.0
    sample_rate: int = 24000
    gpu: bool = False

    @model_validator(mode="after")
    def _override_from_env(self) -> "TTSConfig":
        if gpu_env := os.getenv("TTS_GPU"):
            self.gpu = gpu_env.lower() in ("1", "true", "yes")
        return self


class AvatarConfig(BaseModel):
    enabled: bool = False
    photo_path: str = "./assets/avatar.jpg"
    model: str = "sadtalker"
    sadtalker_path: str = "./models/SadTalker"
    liveportrait_path: str = "./models/LivePortrait"
    output_fps: int = 25
    enhancer: Optional[str] = "gfpgan"


class OBSConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    source_name: str = "AI Avatar"


# ── Root config ────────────────────────────────────────────────────────────────


class AppConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    livekit: LiveKitConfig = Field(default_factory=LiveKitConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    avatar: AvatarConfig = Field(default_factory=AvatarConfig)
    obs: OBSConfig = Field(default_factory=OBSConfig)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load config from YAML file, falling back to defaults for missing keys."""
    path = Path(path)

    if not path.exists():
        return AppConfig()

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    return AppConfig.model_validate(data)
