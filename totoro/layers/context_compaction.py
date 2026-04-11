"""Context compaction — prevent context window overflow.

Uses a lightweight LLM for intelligent summarization when available,
with a fast heuristic fallback when no model is provided.
"""

import logging
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage
from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)

# ─── LLM summarization prompts ───

_SUMMARY_PROMPT = """Summarize the following conversation segment concisely.
Focus on:
1. Key decisions made
2. Important facts/findings discovered
3. Actions taken (files modified, commands run)
4. Unresolved issues or pending tasks

Keep it under 500 words. Use bullet points. Omit greetings, filler, and tool output details.

Conversation:
{conversation}"""

_EMERGENCY_SUMMARY_PROMPT = """Summarize this conversation in 3-5 bullet points.
Focus ONLY on: what the user asked for, what was done, and what's left to do.

Conversation:
{conversation}"""


class ContextCompactor:
    """3-tier context compaction based on usage ratio."""

    def __init__(
        self,
        auto_threshold: float = 0.7,
        reactive_threshold: float = 0.85,
        emergency_threshold: float = 0.95,
        model=None,
    ):
        """Initialize the 3-tier context compactor.

        Args:
            auto_threshold: Usage ratio to trigger auto compaction.
            reactive_threshold: Usage ratio to trigger reactive compaction.
            emergency_threshold: Usage ratio to trigger emergency compaction.
            model: Optional lightweight LLM for intelligent summarization.
        """
        self._auto = auto_threshold
        self._reactive = reactive_threshold
        self._emergency = emergency_threshold
        self._model = model

    def check_and_compact(
        self, messages: list, model_context_window: int = 200000
    ) -> list | None:
        """Check usage and compact if needed.

        Args:
            messages: The current conversation message list.
            model_context_window: Total context window size in tokens.

        Returns:
            Compacted message list, or None if no compaction needed.
        """
        from totoro.layers._token_utils import estimate_tokens

        token_count = estimate_tokens(messages)
        ratio = token_count / model_context_window

        if ratio < self._auto:
            return None
        if ratio >= self._emergency:
            return self._emergency_compact(messages)
        if ratio >= self._reactive:
            return self._reactive_compact(messages)
        return self._auto_compact(messages)

    def _auto_compact(self, messages: list) -> list:
        mid = len(messages) // 2
        summary = self._summarize(messages[:mid])
        return [
            SystemMessage(content=f"[Compacted context]\n{summary}"),
            *messages[mid:],
        ]

    def _reactive_compact(self, messages: list) -> list:
        keep = max(len(messages) // 3, 10)
        summary = self._summarize(messages[:-keep])
        recent = [_truncate_tool_result(m) for m in messages[-keep:]]
        return [
            SystemMessage(content=f"[Compacted context]\n{summary}"),
            *recent,
        ]

    def _emergency_compact(self, messages: list) -> list:
        summary = self._summarize(messages[:-5], emergency=True)
        return [
            SystemMessage(content=f"[Emergency compact]\n{summary}"),
            *messages[-5:],
        ]

    def _summarize(self, messages: list, emergency: bool = False) -> str:
        """Summarize messages using LLM or heuristic.

        Args:
            messages: Messages to summarize.
            emergency: If True, use a more aggressive summarization prompt.

        Returns:
            Summary text.
        """
        if self._model is not None:
            try:
                return self._llm_summarize(messages, emergency)
            except Exception as e:
                logger.warning(
                    f"LLM summarization failed, using fallback: {e}"
                )
        return _heuristic_summarize(messages)

    def _llm_summarize(self, messages: list, emergency: bool = False) -> str:
        """Use lightweight LLM to generate a real summary.

        Args:
            messages: Messages to summarize.
            emergency: If True, use the emergency (ultra-concise) prompt.

        Returns:
            Summary text from the LLM.
        """
        conversation_text = _format_for_summary(messages)
        if not conversation_text.strip():
            return "No significant content."

        # Truncate input to avoid exceeding the lightweight model's context
        if len(conversation_text) > 12000:
            conversation_text = (
                conversation_text[:12000] + "\n... (rest omitted)"
            )

        template = _EMERGENCY_SUMMARY_PROMPT if emergency else _SUMMARY_PROMPT
        prompt = template.format(conversation=conversation_text)
        response = self._model.invoke([HumanMessage(content=prompt)])
        return response.content.strip()


def _heuristic_summarize(messages: list) -> str:
    """Fast heuristic fallback — extract key lines from messages.

    Args:
        messages: Messages to summarize.

    Returns:
        Summary text built from the last 20 human/ai messages.
    """
    lines = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", "")
        if isinstance(content, list):
            # Handle multi-block content (e.g. tool_use blocks)
            text_parts = [
                b["text"]
                if isinstance(b, dict) and b.get("type") == "text"
                else str(b)
                for b in content
                if isinstance(b, (str, dict))
            ]
            content = " ".join(text_parts)
        if content and role in ("human", "ai"):
            lines.append(f"- [{role}] {content[:200]}")
    return "\n".join(lines[-20:]) if lines else "No significant content."


def _format_for_summary(messages: list) -> str:
    """Format messages as readable text for the LLM summarizer.

    Args:
        messages: Messages to format.

    Returns:
        Formatted text with role-prefixed lines.
    """
    lines = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", "")
        if isinstance(content, list):
            text_parts = [
                b["text"]
                if isinstance(b, dict) and b.get("type") == "text"
                else ""
                for b in content
                if isinstance(b, (str, dict))
            ]
            content = " ".join(text_parts)
        if not content:
            continue
        if role == "human":
            lines.append(f"[User] {content[:500]}")
        elif role == "ai":
            lines.append(f"[Assistant] {content[:500]}")
        elif role == "tool":
            # Keep tool results very short for summary input
            lines.append(f"[Tool result] {content[:150]}")
    return "\n".join(lines)


def _truncate_tool_result(message):
    """Return a copy with truncated content if it's a tool result.

    Args:
        message: A message object to potentially truncate.

    Returns:
        The original message, or a copy with content truncated to 2000 chars.
    """
    content = getattr(message, "content", None)
    if hasattr(message, "tool_call_id") and content and len(content) > 2000:
        from copy import copy

        msg = copy(message)
        msg.content = content[:2000] + "\n... (truncated)"
        return msg
    return message


class ContextCompactionMiddleware(AgentMiddleware):
    """Middleware wrapper for ContextCompactor."""

    def __init__(
        self,
        auto_threshold: float = 0.7,
        reactive_threshold: float = 0.85,
        emergency_threshold: float = 0.95,
        model_context_window: int = 200_000,
        model=None,
    ):
        """Initialize the context compaction middleware.

        Args:
            auto_threshold: Usage ratio to trigger auto compaction.
            reactive_threshold: Usage ratio to trigger reactive compaction.
            emergency_threshold: Usage ratio to trigger emergency compaction.
            model_context_window: Total context window size in tokens.
            model: Optional lightweight LLM for intelligent summarization.
        """
        self._compactor = ContextCompactor(
            auto_threshold,
            reactive_threshold,
            emergency_threshold,
            model=model,
        )
        self._context_window = model_context_window

    @property
    def name(self) -> str:
        return "ContextCompactionMiddleware"

    def before_model(self, state, runtime) -> dict[str, Any] | None:
        """Check context usage and compact messages if thresholds are exceeded.

        Args:
            state: Current agent state containing messages.
            runtime: Middleware runtime context.

        Returns:
            Dict with compacted messages, or None if no compaction needed.
        """
        messages = (
            state.get("messages", [])
            if isinstance(state, dict)
            else getattr(state, "messages", [])
        )
        compacted = self._compactor.check_and_compact(
            messages, self._context_window
        )
        if compacted is not None:
            import sys
            from totoro.layers._token_utils import estimate_tokens

            original = len(messages)
            new = len(compacted)
            ratio = estimate_tokens(messages) / self._context_window * 100
            print(
                f"\033[38;2;96;80;58m  [auto-compact]"
                f" {original} → {new} messages "
                f"(context was {ratio:.0f}% full)\033[0m",
                file=sys.stderr,
                flush=True,
            )
            return {"messages": compacted}
        return None
