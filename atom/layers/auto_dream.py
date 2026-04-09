"""Auto-Dream memory extraction — extracts long-term memories from conversations."""
import json
import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)


EXTRACTION_PROMPT = """Analyze the following conversation segment and extract information worth
storing as long-term memory.

Extract these types:
1. user: user's role, expertise, preferences
2. feedback: corrections or confirmations about work style
3. domain: domain knowledge (business logic, terminology, rules)
4. project: project-specific context (architecture decisions, conventions)

Do NOT extract:
- Code itself (available in git/files)
- Temporary task state
- Information already in existing memories

Existing memories:
{existing_memories}

Conversation segment:
{conversation_segment}

Return as JSON array:
[{{"type": "...", "name": "...", "content": "..."}}]
Return empty array [] if nothing to extract."""


class AutoDreamExtractor:
    """Extracts long-term memories from conversations using a lightweight LLM.

    Triggered when token or tool-call thresholds are reached.
    Runs synchronously in the after_model middleware hook.
    """

    def __init__(self, model=None, store=None, config=None):
        self._model = model
        self._store = store
        self._last_extraction_token_count = 0
        self._last_extraction_tool_count = 0
        self._memories: list[dict] = []  # In-memory fallback if no store

        # Thresholds from config
        if config and hasattr(config, "memory"):
            self._token_threshold = config.memory.extraction_threshold_tokens
            self._max_entries = config.memory.max_memory_entries
        else:
            self._token_threshold = 3000
            self._max_entries = 500

    def should_extract(self, current_token_count: int, tool_count: int) -> bool:
        """Check if extraction thresholds are met."""
        token_delta = current_token_count - self._last_extraction_token_count
        tool_delta = tool_count - self._last_extraction_tool_count
        return token_delta >= self._token_threshold or tool_delta >= 3

    def extract(self, messages: list) -> list[dict]:
        """Extract memories from recent messages using the lightweight LLM.

        Returns list of extracted memory entries.
        """
        if self._model is None:
            return []

        try:
            recent_segment = messages[-20:]
            conversation_text = _format_messages(recent_segment)
            existing_text = self._format_existing_memories()

            from langchain_core.messages import HumanMessage
            prompt = EXTRACTION_PROMPT.format(
                existing_memories=existing_text,
                conversation_segment=conversation_text,
            )
            response = self._model.invoke([HumanMessage(content=prompt)])
            entries = _parse_json_response(response.content)

            # Store extracted memories
            for entry in entries:
                self._store_memory(entry)

            return entries
        except Exception as e:
            logger.debug(f"Auto-dream extraction failed: {e}")
            return []

    def maybe_extract(self, messages: list, current_token_count: int, tool_count: int) -> list[dict]:
        """Check thresholds and extract if needed. Returns extracted entries."""
        if not self.should_extract(current_token_count, tool_count):
            return []

        entries = self.extract(messages)
        self._last_extraction_token_count = current_token_count
        self._last_extraction_tool_count = tool_count
        return entries

    def _store_memory(self, entry: dict) -> None:
        """Store a memory entry."""
        if len(self._memories) >= self._max_entries:
            self._memories.pop(0)  # Remove oldest
        self._memories.append(entry)

        # Also store in LangGraph store if available
        if self._store is not None:
            try:
                namespace = ("memory", entry.get("type", "general"))
                key = entry.get("name", f"memory-{len(self._memories)}")
                self._store.put(namespace, key, entry)
            except Exception as e:
                logger.debug(f"Failed to persist memory to store: {e}")

    def get_memories(self) -> list[dict]:
        """Get all extracted memories."""
        return list(self._memories)

    def get_memories_by_type(self, memory_type: str) -> list[dict]:
        """Get memories filtered by type."""
        return [m for m in self._memories if m.get("type") == memory_type]

    def _format_existing_memories(self) -> str:
        """Format existing memories for the extraction prompt."""
        if not self._memories:
            return "(none)"
        lines = []
        for m in self._memories[-20:]:  # Last 20 memories
            lines.append(f"- [{m.get('type', '?')}] {m.get('name', '?')}: {m.get('content', '')[:200]}")
        return "\n".join(lines)

    def format_memories_display(self) -> str:
        """Format memories for user display."""
        if not self._memories:
            return "No memories extracted yet."
        from atom.colors import BOLD, RESET
        lines = [f"{BOLD}Extracted Memories:{RESET}"]
        for i, m in enumerate(self._memories, 1):
            mtype = m.get("type", "unknown")
            name = m.get("name", "unnamed")
            content = m.get("content", "")[:100]
            lines.append(f"  {i}. [{mtype}] {name}: {content}")
        return "\n".join(lines)


class AutoDreamMiddleware(AgentMiddleware):
    """Middleware that triggers Auto-Dream extraction after model calls."""

    def __init__(self, extractor: AutoDreamExtractor):
        self._extractor = extractor

    @property
    def name(self) -> str:
        return "AutoDreamMiddleware"

    def after_model(self, state, runtime) -> dict[str, Any] | None:
        """After each model call, check if memory extraction is needed."""
        messages = state.get("messages", []) if isinstance(state, dict) else getattr(state, "messages", [])
        token_count = _estimate_tokens(messages)
        tool_count = sum(1 for m in messages if hasattr(m, "tool_call_id"))
        self._extractor.maybe_extract(messages, token_count, tool_count)
        return None


def _estimate_tokens(messages: list) -> int:
    """Estimate token count from messages (4 chars ~ 1 token)."""
    total_chars = sum(len(getattr(m, "content", str(m)) or "") for m in messages)
    return total_chars // 4


def _format_messages(messages: list) -> str:
    """Format message list as text for the extraction prompt."""
    lines = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", str(m))
        if content:
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = " ".join(text_parts)
            lines.append(f"[{role}] {str(content)[:500]}")
    return "\n".join(lines)


def _parse_json_response(text: str) -> list[dict]:
    """Extract JSON array from LLM response."""
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
