"""
ai-meeting-avatar
=================
Local AI meeting avatar agent.

Pipeline: LiveKit audio in → Whisper STT → Ollama LLM → Coqui XTTS → LiveKit audio out
Phase 2:  TTS audio → SadTalker/LivePortrait avatar video → OBS virtual camera
"""

__version__ = "0.1.0"
