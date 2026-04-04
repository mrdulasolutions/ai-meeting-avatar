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
    # ── Backend selection ──────────────────────────────────────────────────────
    # "gemma"  — local Gemma 4 via LiteRT-LM (offline, private, no API key)
    # "claude" — Anthropic Claude API (smarter, needs ANTHROPIC_API_KEY)
    backend: str = "gemma"

    # ── Gemma (local) settings ─────────────────────────────────────────────────
    # Model variant: "e2b" (lightweight ~2.6 GB) or "e4b" (higher quality ~4 GB)
    model_variant: str = "e2b"
    # Path to the .litertlm weights file on disk
    model_path: str = "./models/gemma-4-e2b/gemma-4-E2B-it.litertlm"
    fallback_model_path: str = "./models/gemma-4-e2b"
    # Hugging Face repos for setup_models.sh
    hf_repo_e2b: str = "litert-community/gemma-4-E2B-it-litert-lm"
    hf_repo_e4b: str = "litert-community/gemma-4-E4B-it-litert-lm"

    # ── Claude (cloud) settings ────────────────────────────────────────────────
    # API key — prefer ANTHROPIC_API_KEY env var over storing here
    anthropic_api_key: str = ""
    # Model to use; see https://docs.anthropic.com/en/docs/about-claude/models
    claude_model: str = "claude-sonnet-4-6"

    # ── Shared settings ────────────────────────────────────────────────────────
    temperature: float = 0.7
    max_tokens: int = 512
    enable_tools: bool = True
    history_turns: int = 10

    @model_validator(mode="after")
    def _override_from_env(self) -> "LLMConfig":
        if path := os.getenv("GEMMA_MODEL_PATH"):
            self.model_path = path
        if key := os.getenv("ANTHROPIC_API_KEY"):
            self.anthropic_api_key = key
        if backend := os.getenv("LLM_BACKEND"):
            self.backend = backend
        return self


class TTSConfig(BaseModel):
    # Voice preset — no voice sample needed
    # American English: af_heart, af_sky, af_sarah, af_nova, am_adam, am_michael
    # British English:  bf_emma, bf_isabella, bm_george, bm_lewis  (set lang to "en-gb")
    voice: str = "af_heart"
    speed: float = 1.0
    lang: str = "en-us"
    # Directory where kokoro-v1.0.onnx and voices-v1.0.bin are stored
    model_dir: str = "./models/kokoro"
    # Kokoro native output sample rate (do not change)
    sample_rate: int = 24_000


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
