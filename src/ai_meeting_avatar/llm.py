"""
LLM via Ollama (local inference).

Maintains per-session conversation history and provides both
blocking and streaming chat interfaces.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncGenerator, Deque

logger = logging.getLogger(__name__)


# ── Conversation history ───────────────────────────────────────────────────────


@dataclass
class Message:
    role: str   # "system" | "user" | "assistant"
    content: str


class ChatHistory:
    """
    Rolling conversation history.

    Keeps the system prompt pinned at position 0, then stores up to
    *max_turns* user/assistant pairs.
    """

    def __init__(self, system_prompt: str = "", max_turns: int = 10) -> None:
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        # Each turn is (user_msg, assistant_msg) — stored as flat Messages
        self._turns: Deque[Message] = deque(maxlen=max_turns * 2)

    def add_user(self, text: str) -> None:
        self._turns.append(Message(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        self._turns.append(Message(role="assistant", content=text))

    def to_ollama_messages(self) -> list[dict]:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend({"role": m.role, "content": m.content} for m in self._turns)
        return messages

    def clear(self) -> None:
        self._turns.clear()


# ── LLM wrapper ────────────────────────────────────────────────────────────────


class OllamaLLM:
    """
    Async wrapper around the Ollama Python client.

    Usage::

        llm = OllamaLLM(model="llama3.2", system_prompt="You are …")
        llm.load()
        history = ChatHistory(system_prompt=llm.system_prompt)

        # Single response
        reply = await llm.chat(history, "What is 2 + 2?")

        # Streaming
        async for chunk in llm.stream_chat(history, "Explain gravity"):
            print(chunk, end="", flush=True)
    """

    def __init__(
        self,
        model: str = "llama3.2",
        host: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 256,
        system_prompt: str = "",
    ) -> None:
        self._model = model
        self._host = host
        self._temperature = temperature
        self._max_tokens = max_tokens
        self.system_prompt = system_prompt
        self._client = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Initialise the async Ollama client."""
        import ollama  # noqa: PLC0415

        self._client = ollama.AsyncClient(host=self._host)
        logger.info("Ollama client ready (model=%s, host=%s)", self._model, self._host)

    async def ensure_model_pulled(self) -> None:
        """Pull the model if not already present (idempotent)."""
        if self._client is None:
            raise RuntimeError("Call OllamaLLM.load() first.")
        import ollama  # noqa: PLC0415

        try:
            await self._client.show(self._model)
            logger.info("Model '%s' already available.", self._model)
        except ollama.ResponseError:
            logger.info("Pulling model '%s' …", self._model)
            await self._client.pull(self._model)
            logger.info("Model '%s' pulled.", self._model)

    # ── Chat ───────────────────────────────────────────────────────────────────

    async def chat(self, history: ChatHistory, user_message: str) -> str:
        """
        Send *user_message* and return the complete assistant reply.

        Does NOT mutate *history* — the caller is responsible for updating it
        so the orchestrator controls what gets remembered.
        """
        if self._client is None:
            raise RuntimeError("Call OllamaLLM.load() first.")

        messages = history.to_ollama_messages()
        messages.append({"role": "user", "content": user_message})

        response = await self._client.chat(
            model=self._model,
            messages=messages,
            options={
                "temperature": self._temperature,
                "num_predict": self._max_tokens,
            },
        )
        return response["message"]["content"]

    async def stream_chat(
        self, history: ChatHistory, user_message: str
    ) -> AsyncGenerator[str, None]:
        """
        Stream the assistant reply token by token.

        Usage::

            full = ""
            async for chunk in llm.stream_chat(history, "Hello"):
                full += chunk
                print(chunk, end="", flush=True)
        """
        if self._client is None:
            raise RuntimeError("Call OllamaLLM.load() first.")

        messages = history.to_ollama_messages()
        messages.append({"role": "user", "content": user_message})

        async for chunk in await self._client.chat(
            model=self._model,
            messages=messages,
            stream=True,
            options={
                "temperature": self._temperature,
                "num_predict": self._max_tokens,
            },
        ):
            if content := chunk.get("message", {}).get("content"):
                yield content

    async def health_check(self) -> bool:
        """Return True if Ollama is reachable and the model exists."""
        if self._client is None:
            return False
        try:
            await self._client.show(self._model)
            return True
        except Exception as exc:
            logger.warning("Ollama health check failed: %s", exc)
            return False
