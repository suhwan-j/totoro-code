"""Auto-Dream memory extraction.

Persistent long-term memories from conversations.

Architecture:
  - Extraction rules defined in built-in/skills/remember/SKILL.md
  - User memories stored in .totoro/character.md (human-readable, editable)
  - Async extraction in background thread (non-blocking)
  - Memory injection into system prompt via before_model hook
  - Survives process restarts and model switches
"""

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)


# ─── Skill-based prompt loading ───


def _load_skill_rules() -> str:
    """Load extraction rules from remember/SKILL.md.

    Search order: project → global → built-in.
    Returns the SKILL.md body (after frontmatter), or a hardcoded fallback.
    """
    candidates = [
        Path.cwd() / ".totoro" / "skills" / "remember" / "SKILL.md",
        Path.home() / ".totoro" / "skills" / "remember" / "SKILL.md",
        Path(__file__).resolve().parent.parent.parent
        / "built-in"
        / "skills"
        / "remember"
        / "SKILL.md",
    ]
    for path in candidates:
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                # Strip YAML frontmatter
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        body = text[end + 3 :].strip()
                        if body:
                            logger.debug(f"Loaded remember skill from {path}")
                            return body
                return text.strip()
            except Exception as e:
                logger.debug(f"Failed to read {path}: {e}")
    return _FALLBACK_RULES


_FALLBACK_RULES = """Extract user profile facts: role, company, expertise, preferences, domain knowledge.
Return JSON array: [{"type": "user|feedback|domain|project", "name": "short_key", "content": "fact"}]
Return [] if nothing to extract."""


# ─── Prompt templates (use SKILL.md rules) ───

_SKILL_RULES: str | None = None  # lazy-loaded


def _get_skill_rules() -> str:
    """Get skill rules, loading once on first access."""
    global _SKILL_RULES
    if _SKILL_RULES is None:
        _SKILL_RULES = _load_skill_rules()
    return _SKILL_RULES


USER_INTENT_PROMPT = """{skill_rules}

---

Existing memories (do NOT duplicate these):
{existing_memories}

User message:
{user_message}

Based on the rules above, extract facts from this user message.
Return ONLY a JSON array. No explanation."""

CONVERSATION_EXTRACT_PROMPT = """{skill_rules}

---

Existing memories (do NOT duplicate these):
{existing_memories}

Conversation segment:
{conversation_segment}

Based on the rules above, extract facts from this conversation.
Return ONLY a JSON array. No explanation."""

MEMORY_CONTEXT_HEADER = """## Long-term Memories
The following memories were extracted from previous conversations. Use them to
personalize your responses and maintain continuity across sessions.

"""


# ─── Character file store (.totoro/character.md) ───

_SECTION_TITLES = {
    "user": "User Profile",
    "preferred": "Preferred Approaches",
    "avoided": "Avoided Approaches",
    "domain": "Domain Knowledge",
    # Legacy types (backward compatible with old character.md files)
    "feedback": "Work Style",
    "project": "Project Context",
}

_SECTION_TO_TYPE = {v: k for k, v in _SECTION_TITLES.items()}

_ENTRY_RE = re.compile(r"^- \*\*(.+?)\*\*:\s*(.+)$")


class CharacterFile:
    """Markdown-based persistent memory store at .totoro/character.md.

    Human-readable, git-friendly, directly editable.
    Thread-safe for concurrent reads/writes.
    """

    def __init__(self, path: str | Path | None = None):
        """Initialize the character file store.

        Args:
            path: Path to character.md.
                Defaults to ~/.totoro/character.md.
        """
        if path is None:
            base = Path.home() / ".totoro"
            base.mkdir(parents=True, exist_ok=True)
            path = base / "character.md"
        self._path = Path(path)
        self._lock = threading.Lock()

    def put(self, entry: dict) -> None:
        """Insert or update a memory entry. Upserts by (type, name).

        Args:
            entry: Dict with 'type', 'name', and 'content' keys.
        """
        mtype = entry.get("type", "user")
        name = entry.get("name", "info")
        content = entry.get("content", "")
        if not content:
            return

        with self._lock:
            data = self._read()
            data.setdefault(mtype, {})[name] = content
            self._write(data)

    def get_all(self) -> list[dict]:
        """Get all memories as list of dicts."""
        with self._lock:
            data = self._read()
        result = []
        for mtype, entries in data.items():
            for name, content in entries.items():
                result.append(
                    {"type": mtype, "name": name, "content": content}
                )
        return result

    def get_by_type(self, memory_type: str) -> list[dict]:
        """Get memories filtered by type.

        Args:
            memory_type: The memory type to filter by.

        Returns:
            List of memory dicts matching the given type.
        """
        with self._lock:
            data = self._read()
        entries = data.get(memory_type, {})
        return [
            {"type": memory_type, "name": k, "content": v}
            for k, v in entries.items()
        ]

    def count(self) -> int:
        with self._lock:
            data = self._read()
        return sum(len(v) for v in data.values())

    def clear(self) -> None:
        with self._lock:
            if self._path.exists():
                self._path.unlink()

    def remove(self, memory_type: str, name: str) -> bool:
        """Remove a specific memory by type and name.

        Args:
            memory_type: The memory type category.
            name: The memory entry name.

        Returns:
            True if the entry was found and removed, False otherwise.
        """
        with self._lock:
            data = self._read()
            entries = data.get(memory_type, {})
            if name in entries:
                del entries[name]
                self._write(data)
                return True
        return False

    def remove_by_index(self, index: int) -> dict | None:
        """Remove a memory by its display index (1-based).

        Args:
            index: 1-based display index of the memory to remove.

        Returns:
            The removed entry dict, or None if index is out of range.
        """
        all_entries = self.get_all()
        if 1 <= index <= len(all_entries):
            entry = all_entries[index - 1]
            if self.remove(entry["type"], entry["name"]):
                return entry
        return None

    def trim(self, max_entries: int) -> None:
        """Keep only the last max_entries per type.

        Args:
            max_entries: Maximum number of entries to retain per type.
        """
        with self._lock:
            data = self._read()
            for mtype in data:
                entries = data[mtype]
                if len(entries) > max_entries:
                    keys = list(entries.keys())
                    for k in keys[:-max_entries]:
                        del entries[k]
            self._write(data)

    # ─── File I/O ───

    def _read(self) -> dict[str, dict[str, str]]:
        """Parse character.md into {type: {name: content}}."""
        if not self._path.exists():
            return {}

        text = self._path.read_text(encoding="utf-8")
        data: dict[str, dict[str, str]] = {}
        current_type = None

        for line in text.splitlines():
            # Section header: ## User Profile
            if line.startswith("## "):
                title = line[3:].strip()
                current_type = _SECTION_TO_TYPE.get(
                    title, title.lower().replace(" ", "_")
                )
                data.setdefault(current_type, {})
                continue

            # Entry: - **name**: content
            if current_type is not None:
                m = _ENTRY_RE.match(line)
                if m:
                    data[current_type][m.group(1)] = m.group(2)

        return data

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        """Write data to character.md."""
        lines = [f"# Character — Auto-Dream Memory", ""]

        for mtype in (
            "user",
            "preferred",
            "avoided",
            "domain",
            "feedback",
            "project",
        ):
            entries = data.get(mtype, {})
            if not entries:
                continue
            title = _SECTION_TITLES.get(mtype, mtype.title())
            lines.append(f"## {title}")
            for name, content in entries.items():
                lines.append(f"- **{name}**: {content}")
            lines.append("")

        # Any extra types not in the standard list
        for mtype, entries in data.items():
            if mtype in _SECTION_TITLES or not entries:
                continue
            lines.append(f"## {mtype.title()}")
            for name, content in entries.items():
                lines.append(f"- **{name}**: {content}")
            lines.append("")

        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── Extractor ───


class AutoDreamExtractor:
    """Extracts long-term memories from conversations using a lightweight LLM.

    - Persistent: memories survive process restarts (SQLite)
    - Async: extraction runs in background thread
    - Injected: memories are prepended to system prompt via before_model
    """

    def __init__(
        self, model=None, config=None, store: CharacterFile | None = None
    ):
        """Initialize the Auto-Dream memory extractor.

        Args:
            model: Lightweight LLM for extraction (e.g. Haiku).
            config: Agent configuration with memory thresholds.
            store: Persistent memory store. Defaults to a new CharacterFile.
        """
        self._model = model
        self._store = store or CharacterFile()
        self._last_extraction_token_count = 0
        self._last_extraction_tool_count = 0
        self._last_extraction_turn = 0
        self._turn_count = 0
        self._extract_lock = threading.Lock()  # prevent concurrent extractions
        self._cached_memories: list[dict] | None = None  # invalidated on write
        self._pending_user_message: str | None = (
            None  # set by on_turn, consumed by after_model
        )

        # Thresholds from config
        if config and hasattr(config, "memory"):
            self._token_threshold = config.memory.extraction_threshold_tokens
            self._max_entries = config.memory.max_memory_entries
        else:
            self._token_threshold = 5000
            self._max_entries = 500

        # Turn-based threshold: extract every N user turns regardless of tokens
        self._turn_threshold = 3

    def should_extract(
        self, current_token_count: int, tool_count: int
    ) -> bool:
        """Check if extraction thresholds are met.

        Args:
            current_token_count: Current total token count in conversation.
            tool_count: Current total tool call count in conversation.

        Returns:
            True if any extraction threshold (token, tool, or turn) is met.
        """
        token_delta = current_token_count - self._last_extraction_token_count
        tool_delta = tool_count - self._last_extraction_tool_count
        turn_delta = self._turn_count - self._last_extraction_turn
        return (
            token_delta >= self._token_threshold
            or tool_delta >= 3
            or turn_delta >= self._turn_threshold
        )

    def extract(self, messages: list) -> list[dict]:
        """Extract memories from recent messages using the lightweight LLM.

        Args:
            messages: Conversation message list to extract from.

        Returns:
            List of extracted memory entry dicts.
        """
        if self._model is None:
            return []

        try:
            recent_segment = messages[-20:]
            conversation_text = _format_messages(recent_segment)
            existing_text = self._format_existing_memories()

            from langchain_core.messages import HumanMessage

            prompt = CONVERSATION_EXTRACT_PROMPT.format(
                skill_rules=_get_skill_rules(),
                existing_memories=existing_text,
                conversation_segment=conversation_text,
            )
            response = self._model.invoke([HumanMessage(content=prompt)])
            entries = _parse_json_response(response.content)

            # Store extracted memories persistently
            for entry in entries:
                self._store.put(entry)
            self._store.trim(self._max_entries)

            # Invalidate cache so next before_model picks up new memories
            self._cached_memories = None

            if entries:
                logger.info(f"Auto-Dream: extracted {len(entries)} memories")

            return entries
        except Exception as e:
            logger.warning(f"Auto-Dream extraction failed: {e}")
            return []

    def on_turn(self, user_message: str = "") -> None:
        """Call once per user turn. Saves user message for deferred analysis.

        Analysis happens later in after_model (after main model responds),
        to avoid concurrent API calls competing with the main model.

        Args:
            user_message: The user's input message text.
        """
        self._turn_count += 1
        # Save for deferred analysis in after_model
        if (
            user_message
            and len(user_message.strip()) >= 5
            and not user_message.strip().startswith("/")
        ):
            self._pending_user_message = user_message
        else:
            self._pending_user_message = None

    def _analyze_user_message_deferred(self, user_message: str) -> None:
        """Run user message analysis in background.

        Args:
            user_message: The user message text to analyze.
        """

        def _bg():
            try:
                self._analyze_user_message(user_message)
            except Exception as e:
                logger.debug(f"User message analysis failed: {e}")

        thread = threading.Thread(target=_bg, daemon=True)
        thread.start()

    def _analyze_user_message(self, user_message: str) -> list[dict]:
        """Analyze user message for personal facts.

        Args:
            user_message: The user message text to analyze.

        Returns:
            List of extracted memory entry dicts.
        """
        existing_text = self._format_existing_memories()

        from langchain_core.messages import HumanMessage

        prompt = USER_INTENT_PROMPT.format(
            skill_rules=_get_skill_rules(),
            existing_memories=existing_text,
            user_message=user_message,
        )
        response = self._model.invoke([HumanMessage(content=prompt)])
        raw = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        entries = _parse_json_response(raw)

        if not entries and raw.strip():
            logger.debug(f"Auto-Dream: LLM returned non-JSON: {raw[:200]}")

        for entry in entries:
            self._store.put(entry)

        if entries:
            self._cached_memories = None  # invalidate cache
            logger.info(
                f"Auto-Dream: extracted {len(entries)} facts from user message"
            )

        return entries

    def maybe_extract_async(
        self, messages: list, current_token_count: int, tool_count: int
    ) -> None:
        """Check thresholds and extract in background thread if needed.

        Args:
            messages: Current conversation message list.
            current_token_count: Current total token count.
            tool_count: Current total tool call count.
        """
        if not self.should_extract(current_token_count, tool_count):
            return

        # Update counters immediately to prevent re-triggering
        self._last_extraction_token_count = current_token_count
        self._last_extraction_tool_count = tool_count
        self._last_extraction_turn = self._turn_count

        # Skip if another extraction is already running
        if not self._extract_lock.acquire(blocking=False):
            return

        # Copy messages for thread safety
        messages_copy = list(messages)

        def _bg_extract():
            try:
                self.extract(messages_copy)
            finally:
                self._extract_lock.release()

        thread = threading.Thread(target=_bg_extract, daemon=True)
        thread.start()

    def extract_on_exit(self, agent, config: dict) -> None:
        """Run a final synchronous extraction when the session ends.

        Reads the full conversation from agent state and extracts any
        remaining memories that didn't hit the threshold during the session.

        Args:
            agent: The agent instance to read state from.
            config: LangGraph config dict with thread_id for state lookup.
        """
        if self._model is None:
            return
        try:
            state = agent.get_state(config)
            if state and hasattr(state, "values"):
                messages = state.values.get("messages", [])
                if messages and self._turn_count > self._last_extraction_turn:
                    self.extract(messages)
        except Exception as e:
            logger.debug(f"Auto-Dream exit extraction failed: {e}")

    def get_memories(self) -> list[dict]:
        """Get all extracted memories from persistent store."""
        return self._store.get_all()

    def get_memories_by_type(self, memory_type: str) -> list[dict]:
        """Get memories filtered by type.

        Args:
            memory_type: The memory type to filter by.

        Returns:
            List of memory dicts matching the given type.
        """
        return self._store.get_by_type(memory_type)

    def get_memory_count(self) -> int:
        """Get total number of stored memories."""
        return self._store.count()

    def clear(self) -> None:
        """Clear all memories from persistent store."""
        self._store.clear()
        self._cached_memories = None

    def format_memory_context(self, max_per_type: int | None = None) -> str:
        """Format memories for injection into system prompt.

        Includes ALL memories by default. If total count exceeds a reasonable
        limit for context injection, distributes slots proportionally across
        types so that no single type dominates and important early memories
        (like user role) aren't dropped.

        Args:
            max_per_type: Override max entries per type. None = use configured
                          max_memory_entries divided by number of types.

        Returns:
            Formatted markdown string of memories, or empty string if none.
        """
        if self._cached_memories is None:
            self._cached_memories = self._store.get_all()

        if not self._cached_memories:
            return ""

        # Group by type
        by_type: dict[str, list[dict]] = {}
        for m in self._cached_memories:
            by_type.setdefault(m["type"], []).append(m)

        # Calculate per-type budget if we need to trim
        # Max total entries for injection (keep system prompt reasonable)
        max_inject_total = (
            60  # ~60 entries ≈ 3000 tokens, reasonable for system prompt
        )
        if max_per_type is None:
            num_types = max(len(by_type), 1)
            total_entries = sum(len(v) for v in by_type.values())
            if total_entries > max_inject_total:
                # Distribute proportionally: each type gets at least 3 slots
                base_per_type = max(3, max_inject_total // num_types)
                max_per_type = base_per_type
            # else: no limit needed, include everything

        lines = [MEMORY_CONTEXT_HEADER]
        type_labels = _SECTION_TITLES
        for mtype, entries in by_type.items():
            label = type_labels.get(mtype, mtype.title())
            lines.append(f"### {label}")
            if max_per_type is not None and len(entries) > max_per_type:
                # Keep first few (important identity/role entries) + latest
                keep_first = max(2, max_per_type // 3)
                keep_last = max_per_type - keep_first
                selected = entries[:keep_first] + entries[-keep_last:]
            else:
                selected = entries
            for m in selected:
                lines.append(f"- **{m['name']}**: {m['content']}")
            lines.append("")

        return "\n".join(lines)

    def _format_existing_memories(self) -> str:
        """Format existing memories for the extraction prompt."""
        memories = self._store.get_all()
        if not memories:
            return "(none)"
        lines = []
        for m in memories[-20:]:
            lines.append(
                f"- [{m.get('type', '?')}]"
                f" {m.get('name', '?')}:"
                f" {m.get('content', '')[:200]}"
            )
        return "\n".join(lines)

    def format_memories_display(self) -> str:
        """Format memories for user display (/memory command)."""
        memories = self._store.get_all()
        if not memories:
            return "No memories stored."
        from totoro.colors import BOLD, RESET, DIM

        lines = [f"{BOLD}Stored Memories ({len(memories)}):{RESET}"]
        for i, m in enumerate(memories, 1):
            mtype = m.get("type", "unknown")
            name = m.get("name", "unnamed")
            content = m.get("content", "")[:100]
            lines.append(
                f"  {i}. {DIM}[{mtype}]{RESET} {BOLD}{name}{RESET}: {content}"
            )
        lines.append(
            f"\n  {DIM}Commands: /memory remove <#>"
            f" · /memory clean"
            f" · /memory clear{RESET}"
        )
        lines.append(f"  {DIM}Stored at: {self._store._path}{RESET}")
        return "\n".join(lines)

    def remove_memory_by_index(self, index: int) -> dict | None:
        """Remove a memory by display index (1-based).

        Args:
            index: 1-based display index of the memory to remove.

        Returns:
            The removed entry dict, or None if index is out of range.
        """
        return self._store.remove_by_index(index)

    def clear(self):
        """Clear all memories."""
        self._store.clear()


# ─── Middleware ───


class AutoDreamMiddleware(AgentMiddleware):
    """Middleware that:
    - before_model: injects memories into system prompt
    - after_model: triggers async extraction when thresholds met
    """

    def __init__(self, extractor: AutoDreamExtractor):
        """Initialize the Auto-Dream middleware.

        Args:
            extractor: The AutoDreamExtractor instance to delegate to.
        """
        self._extractor = extractor

    @property
    def name(self) -> str:
        return "AutoDreamMiddleware"

    # Memory injection is handled at system prompt build time in agent.py
    # (_load_character_md), not via before_model, because LangGraph's
    # add_messages reducer would append instead of replace.

    def after_model(self, state, runtime) -> dict[str, Any] | None:
        """Trigger async memory extraction after main model responds.

        1. Analyze pending user message for facts
        2. Check thresholds for full conversation extraction
        Both run async in background threads -- no blocking.

        Args:
            state: Current agent state containing messages.
            runtime: Middleware runtime context.

        Returns:
            Always None (extraction is async and does not modify state).
        """
        messages = (
            state.get("messages", [])
            if isinstance(state, dict)
            else getattr(state, "messages", [])
        )

        # 1. Deferred user message analysis (set by on_turn, consumed here)
        pending = self._extractor._pending_user_message
        if pending:
            self._extractor._pending_user_message = None
            self._extractor._analyze_user_message_deferred(pending)

        # 2. Threshold-based full conversation extraction
        from totoro.layers._token_utils import estimate_tokens

        token_count = estimate_tokens(messages)
        tool_count = sum(1 for m in messages if hasattr(m, "tool_call_id"))
        self._extractor.maybe_extract_async(messages, token_count, tool_count)
        return None


def _format_messages(messages: list) -> str:
    """Format message list as text for the extraction prompt.

    Args:
        messages: Messages to format.

    Returns:
        Formatted text with role-prefixed lines, each truncated to 500 chars.
    """
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
    """Extract JSON array from LLM response.

    Args:
        text: Raw LLM response text containing a JSON array.

    Returns:
        Parsed list of dicts, or empty list on parse failure.
    """
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
