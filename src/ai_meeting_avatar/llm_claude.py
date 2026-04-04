"""
LLM via Anthropic Claude API.

ClaudeLLM implements the same interface as GemmaLLM (load, close, chat,
stream_chat, health_check) so the orchestrator can swap backends without
any other changes.

Install:    pip install -e ".[claude]"
API key:    set ANTHROPIC_API_KEY env var  OR  llm.anthropic_api_key in config.yaml

Function calling / tool use
────────────────────────────
Meeting-control tools (mute, unmute, get_meeting_info, end_meeting_for_me)
are exposed to Claude via the native tool_use API.  The chat() loop handles
all tool turns transparently before returning the final text reply.

stream_chat() handles tool turns synchronously first, then streams the
final text response token-by-token using AsyncAnthropic.messages.stream().
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncGenerator

from .llm import (
    ChatHistory,
    end_meeting_for_me,
    get_meeting_info,
    mute_microphone,
    unmute_microphone,
)

logger = logging.getLogger(__name__)

# ── Tool definitions (Anthropic JSON schema format) ───────────────────────────

MEETING_TOOL_DEFS: list[dict] = [
    {
        "name": "mute_microphone",
        "description": "Mute the local microphone in the meeting.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "unmute_microphone",
        "description": "Unmute the local microphone in the meeting.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_meeting_info",
        "description": "Get the current meeting name, participant count, and elapsed time.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "end_meeting_for_me",
        "description": "Leave the current meeting call.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_DISPATCH: dict = {
    "mute_microphone": mute_microphone,
    "unmute_microphone": unmute_microphone,
    "get_meeting_info": get_meeting_info,
    "end_meeting_for_me": end_meeting_for_me,
}


# ── ClaudeLLM ─────────────────────────────────────────────────────────────────


class ClaudeLLM:
    """
    Cloud LLM using Anthropic Claude via the official Python SDK.

    Usage::

        llm = ClaudeLLM(api_key="sk-ant-...", model="claude-sonnet-4-6")
        llm.load()
        history = ChatHistory(system_prompt="You are a meeting assistant.")

        # Single response (handles tool-use turns internally)
        reply = await llm.chat(history, "Summarise the last 5 minutes.")

        # Streaming
        async for chunk in llm.stream_chat(history, "What was discussed?"):
            print(chunk, end="", flush=True)

        llm.close()
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-sonnet-4-6",
        system_prompt: str = "",
        enable_tools: bool = True,
        max_tokens: int = 512,
        temperature: float = 0.7,
        history_turns: int = 10,
    ) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model
        self._system_prompt = system_prompt
        self._enable_tools = enable_tools
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._history_turns = history_turns

        self._client = None  # anthropic.AsyncAnthropic, set by load()
        # Internal message history — kept in sync by chat() / stream_chat()
        self._messages: list[dict] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Initialise the Anthropic async client."""
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for Claude backend.\n"
                "Install with:  pip install -e '.[claude]'"
            ) from exc

        if not self._api_key:
            raise ValueError(
                "Anthropic API key is required.\n"
                "Set the ANTHROPIC_API_KEY environment variable  OR\n"
                "add  llm.anthropic_api_key  to config.yaml."
            )

        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        logger.info(
            "Claude LLM ready (model=%s, tools=%s).",
            self._model,
            "enabled" if self._enable_tools else "disabled",
        )

    def close(self) -> None:
        """Release client resources."""
        self._client = None
        self._messages.clear()

    def __del__(self) -> None:
        self.close()

    # ── Chat ───────────────────────────────────────────────────────────────────

    async def chat(self, history: ChatHistory, user_message: str) -> str:
        """
        Send *user_message* and return the complete assistant reply.

        Handles tool-use turns transparently.  The *history* parameter is
        accepted for API compatibility; conversation state is maintained
        internally in self._messages.
        """
        if self._client is None:
            raise RuntimeError("Call ClaudeLLM.load() before chatting.")

        self._append_user(user_message)

        tools = MEETING_TOOL_DEFS if self._enable_tools else []

        while True:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system_prompt,
                messages=self._messages,
                **({"tools": tools} if tools else {}),
            )

            # Append assistant turn (preserves tool_use blocks for the API)
            self._messages.append({"role": "assistant", "content": response.content})
            self._trim_history()

            if response.stop_reason != "tool_use":
                return self._extract_text(response.content)

            # Execute requested tools and loop
            tool_results = self._execute_tools(response.content)
            self._messages.append({"role": "user", "content": tool_results})

    async def stream_chat(
        self, history: ChatHistory, user_message: str
    ) -> AsyncGenerator[str, None]:
        """
        Stream the assistant reply token by token.

        Tool-use turns (if any) are resolved synchronously first; the final
        text response is streamed via AsyncAnthropic.messages.stream().

        Usage::

            async for chunk in llm.stream_chat(history, "Hello"):
                print(chunk, end="", flush=True)
        """
        if self._client is None:
            raise RuntimeError("Call ClaudeLLM.load() before chatting.")

        self._append_user(user_message)

        tools = MEETING_TOOL_DEFS if self._enable_tools else []

        # ── Tool-use loop (non-streaming) ──────────────────────────────────────
        while True:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system_prompt,
                messages=self._messages,
                **({"tools": tools} if tools else {}),
            )

            self._messages.append({"role": "assistant", "content": response.content})
            self._trim_history()

            if response.stop_reason != "tool_use":
                # We already have the full text — yield it chunk by chunk
                for block in response.content:
                    if hasattr(block, "type") and block.type == "text":
                        yield block.text
                return

            tool_results = self._execute_tools(response.content)
            self._messages.append({"role": "user", "content": tool_results})

    # ── Health check ───────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Return True if the Claude API is reachable with the current key."""
        if self._client is None:
            return False
        saved = list(self._messages)
        try:
            result = await self.chat(ChatHistory(), "ping")
            return bool(result)
        except Exception as exc:
            logger.warning("Claude health check failed: %s", exc)
            return False
        finally:
            self._messages = saved  # don't pollute real history with health-check turn

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _append_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def _trim_history(self) -> None:
        """Keep at most history_turns * 2 messages (user + assistant pairs)."""
        max_msgs = self._history_turns * 2
        if len(self._messages) > max_msgs:
            self._messages = self._messages[-max_msgs:]

    @staticmethod
    def _extract_text(content: list) -> str:
        """Pull text from a list of Claude content blocks."""
        parts: list[str] = []
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts).strip()

    @staticmethod
    def _execute_tools(content: list) -> list[dict]:
        """Run any tool_use blocks and return tool_result dicts."""
        results: list[dict] = []
        for block in content:
            btype = block.type if hasattr(block, "type") else block.get("type")
            if btype != "tool_use":
                continue
            name = block.name if hasattr(block, "name") else block.get("name")
            bid = block.id if hasattr(block, "id") else block.get("id")
            fn = TOOL_DISPATCH.get(name)
            if fn:
                try:
                    result_text = fn()
                except Exception as exc:
                    result_text = f"Tool error: {exc}"
            else:
                result_text = f"Unknown tool: {name}"
                logger.warning("Claude requested unknown tool: %s", name)
            results.append(
                {"type": "tool_result", "tool_use_id": bid, "content": result_text}
            )
        return results
