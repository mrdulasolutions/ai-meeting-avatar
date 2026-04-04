"""
LLM via Google AI Edge LiteRT-LM — local Gemma 4 inference.

LiteRT-LM runs Gemma 4 (E2B / E4B) entirely on-device using memory-mapped
.litertlm weights, with no server process required.

Key LiteRT-LM concepts used here:
  - litert_lm.Engine   — loads weights, owns the inference backend
  - engine.create_conversation() — stateful context that tracks message history
  - conversation.send_message()       — blocking single response
  - conversation.send_message_async() — streaming token iterator
  - tools=[fn, …]     — Python functions auto-exposed as callable tools
                         (docstring + type hints → JSON schema)

Model files (.litertlm) are downloaded via Hugging Face Hub:
  litert-community/gemma-4-E2B-it-litert-lm  (~2.6 GB, gated)
  litert-community/gemma-4-E4B-it-litert-lm  (~4+ GB, gated)

Install:  pip install litert-lm-nightly huggingface-hub
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

logger = logging.getLogger(__name__)


# ── Built-in meeting-control tools ────────────────────────────────────────────
# These are passed to the Engine so Gemma can call them when appropriate.
# Add more tools here; they're auto-described from docstring + type hints.


def mute_microphone() -> str:
    """Mute the local microphone in the meeting."""
    logger.info("[Tool] mute_microphone called")
    return "Microphone muted."


def unmute_microphone() -> str:
    """Unmute the local microphone in the meeting."""
    logger.info("[Tool] unmute_microphone called")
    return "Microphone unmuted."


def get_meeting_info() -> str:
    """Get the current meeting name, participant count, and elapsed time."""
    logger.info("[Tool] get_meeting_info called")
    return "Meeting info: room active, participants connected."


def end_meeting_for_me() -> str:
    """Leave the current meeting call."""
    logger.info("[Tool] end_meeting_for_me called")
    return "Leaving the meeting now."


DEFAULT_TOOLS: list[Callable] = [
    mute_microphone,
    unmute_microphone,
    get_meeting_info,
    end_meeting_for_me,
]


# ── Conversation history (kept for API compatibility) ─────────────────────────
# LiteRT-LM manages history internally in the Conversation object.
# This class is retained so the orchestrator doesn't need to change.


class ChatHistory:
    """
    Thin wrapper kept for API compatibility with the orchestrator.

    LiteRT-LM's Conversation object manages its own rolling history, so
    this class only tracks the system prompt and provides a compatibility
    shim for external code that calls add_user / add_assistant.
    """

    def __init__(self, system_prompt: str = "", max_turns: int = 10) -> None:
        self.system_prompt = system_prompt
        self._max_turns = max_turns

    def add_user(self, text: str) -> None:
        pass  # history tracked by litert_lm.Conversation

    def add_assistant(self, text: str) -> None:
        pass  # history tracked by litert_lm.Conversation

    def clear(self) -> None:
        pass


# ── LLM wrapper ────────────────────────────────────────────────────────────────


class GemmaLLM:
    """
    Local LLM using Google AI Edge LiteRT-LM + Gemma 4.

    Usage::

        llm = GemmaLLM(model_path="./models/gemma-4-e2b/gemma-4-E2B-it.litertlm")
        llm.load()
        history = ChatHistory(system_prompt="You are a meeting assistant.")

        # Single response
        reply = await llm.chat(history, "Summarise the last 5 minutes.")

        # Streaming
        async for chunk in llm.stream_chat(history, "What was discussed?"):
            print(chunk, end="", flush=True)

        llm.close()
    """

    def __init__(
        self,
        model_path: str = "./models/gemma-4-e2b/gemma-4-E2B-it.litertlm",
        system_prompt: str = "",
        enable_tools: bool = True,
        extra_tools: Optional[list[Callable]] = None,
        max_tokens: int = 256,
        temperature: float = 0.7,
    ) -> None:
        self._model_path = model_path
        self.system_prompt = system_prompt
        self._enable_tools = enable_tools
        self._extra_tools = extra_tools or []
        self._max_tokens = max_tokens
        self._temperature = temperature

        self._engine = None
        self._conversation = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load the Gemma 4 model from disk and open a conversation session."""
        import litert_lm  # noqa: PLC0415

        model_path = Path(self._model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Gemma model not found: {model_path}\n"
                "Run: ./scripts/setup_models.sh  to download it."
            )

        logger.info("Loading Gemma 4 from %s …", model_path)

        # Enter the Engine context manually so it persists across chat() calls
        self._engine = litert_lm.Engine(
            str(model_path),
            backend=litert_lm.Backend.CPU,
        )
        self._engine.__enter__()

        # Build tool list
        tools: Optional[list[Callable]] = None
        if self._enable_tools:
            tools = DEFAULT_TOOLS + self._extra_tools

        # System prompt as the first message in the conversation
        initial_messages = None
        if self.system_prompt:
            initial_messages = [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": self.system_prompt}],
                }
            ]

        conv_kwargs: dict = {}
        if initial_messages:
            conv_kwargs["messages"] = initial_messages
        if tools:
            conv_kwargs["tools"] = tools

        self._conversation = self._engine.create_conversation(**conv_kwargs)
        self._conversation.__enter__()

        logger.info("Gemma 4 ready (tools=%s).", "enabled" if tools else "disabled")

    def close(self) -> None:
        """Release engine resources."""
        if self._conversation is not None:
            try:
                self._conversation.__exit__(None, None, None)
            except Exception:
                pass
            self._conversation = None
        if self._engine is not None:
            try:
                self._engine.__exit__(None, None, None)
            except Exception:
                pass
            self._engine = None

    def __del__(self) -> None:
        self.close()

    # ── Chat ───────────────────────────────────────────────────────────────────

    async def chat(self, history: ChatHistory, user_message: str) -> str:
        """
        Send *user_message* and return the complete assistant reply.

        History is maintained internally by the LiteRT-LM Conversation object.
        The *history* parameter is accepted for API compatibility only.
        """
        if self._conversation is None:
            raise RuntimeError("Call GemmaLLM.load() before chatting.")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, self._conversation.send_message, user_message
        )
        return self._extract_text(response)

    async def stream_chat(
        self, history: ChatHistory, user_message: str
    ) -> AsyncGenerator[str, None]:
        """
        Stream the assistant reply token by token.

        Usage::

            async for chunk in llm.stream_chat(history, "Hello"):
                print(chunk, end="", flush=True)
        """
        if self._conversation is None:
            raise RuntimeError("Call GemmaLLM.load() before chatting.")

        loop = asyncio.get_event_loop()

        # Run the streaming generator in a thread and yield chunks back
        # to the async caller via an asyncio.Queue.
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        def _stream_thread():
            try:
                for chunk in self._conversation.send_message_async(user_message):
                    for item in chunk.get("content", []):
                        if item.get("type") == "text":
                            asyncio.run_coroutine_threadsafe(
                                queue.put(item["text"]), loop
                            ).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        loop.run_in_executor(None, _stream_thread)

        while True:
            token = await queue.get()
            if token is None:
                break
            yield token

    # ── Health check ───────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Return True if the model is loaded and responsive."""
        if self._conversation is None:
            return False
        try:
            result = await self.chat(ChatHistory(), "ping")
            return bool(result)
        except Exception as exc:
            logger.warning("Gemma health check failed: %s", exc)
            return False

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(response: dict) -> str:
        """Pull the text string out of a LiteRT-LM response dict."""
        try:
            content = response.get("content", [])
            texts = [item["text"] for item in content if item.get("type") == "text"]
            return " ".join(texts).strip()
        except (KeyError, TypeError, AttributeError):
            # Fallback: stringify whatever came back
            return str(response)


# ── Backwards-compat alias ────────────────────────────────────────────────────
# The orchestrator imports OllamaLLM by name; alias it so no other file changes.
OllamaLLM = GemmaLLM
