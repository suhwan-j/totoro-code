"""Context compaction — prevent context window overflow."""
from typing import Any

from langchain_core.messages import SystemMessage
from langchain.agents.middleware.types import AgentMiddleware


class ContextCompactor:
    """3-tier context compaction based on usage ratio."""

    def __init__(self, auto_threshold: float = 0.7, reactive_threshold: float = 0.85, emergency_threshold: float = 0.95):
        self._auto = auto_threshold
        self._reactive = reactive_threshold
        self._emergency = emergency_threshold

    def check_and_compact(self, messages: list, model_context_window: int = 200000) -> list | None:
        """Check usage and compact if needed. Returns compacted messages or None."""
        token_count = _estimate_tokens(messages)
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
        summary = _summarize_messages(messages[:mid])
        return [SystemMessage(content=f"[Compacted context]\n{summary}"), *messages[mid:]]

    def _reactive_compact(self, messages: list) -> list:
        keep = max(len(messages) // 3, 10)
        summary = _summarize_messages(messages[:-keep])
        recent = [_truncate_tool_result(m) for m in messages[-keep:]]
        return [SystemMessage(content=f"[Compacted context]\n{summary}"), *recent]

    def _emergency_compact(self, messages: list) -> list:
        return [
            SystemMessage(content="[Emergency compact] Previous conversation compacted due to context limit."),
            *messages[-5:],
        ]


def _estimate_tokens(messages: list) -> int:
    total = sum(len(getattr(m, "content", str(m)) or "") for m in messages)
    return total // 4


def _summarize_messages(messages: list) -> str:
    lines = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", "")
        if content and role in ("human", "ai"):
            lines.append(f"- [{role}] {content[:200]}")
    return "\n".join(lines[-20:]) if lines else "No significant content."


def _truncate_tool_result(message):
    """Return a copy with truncated content if it's a tool result."""
    content = getattr(message, "content", None)
    if hasattr(message, "tool_call_id") and content and len(content) > 2000:
        # Create a copy to avoid mutating original (immutable state principle)
        from copy import copy
        msg = copy(message)
        msg.content = content[:2000] + "\n... (truncated)"
        return msg
    return message


class ContextCompactionMiddleware(AgentMiddleware):
    """Middleware wrapper for ContextCompactor — runs before each model call."""

    def __init__(
        self,
        auto_threshold: float = 0.7,
        reactive_threshold: float = 0.85,
        emergency_threshold: float = 0.95,
        model_context_window: int = 200_000,
    ):
        self._compactor = ContextCompactor(auto_threshold, reactive_threshold, emergency_threshold)
        self._context_window = model_context_window

    @property
    def name(self) -> str:
        return "ContextCompactionMiddleware"

    def before_model(self, state, runtime) -> dict[str, Any] | None:
        messages = state.get("messages", []) if isinstance(state, dict) else getattr(state, "messages", [])
        compacted = self._compactor.check_and_compact(messages, self._context_window)
        if compacted is not None:
            return {"messages": compacted}
        return None
