"""Sanitize middleware — strip surrogate characters before model calls.

deepagents' built-in execute tool (LocalShellBackend) uses subprocess.run(text=True)
without encoding="utf-8", errors="replace". This can introduce surrogate characters
(U+D800–U+DFFF) into tool result messages. When the full conversation is serialized
to JSON for the API call, json.dumps / str.encode('utf-8') will raise:

    UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcXX' ...

This middleware runs before every model call and strips surrogates from ALL message
content, preventing the error regardless of which tool introduced them.
"""
import re
from copy import copy
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _clean(text: str) -> str:
    """Replace surrogate characters with U+FFFD replacement character."""
    if not isinstance(text, str):
        return text
    return _SURROGATE_RE.sub("\ufffd", text)


def _sanitize_content(content):
    """Sanitize message content (str, list of blocks, or other)."""
    if isinstance(content, str):
        return _clean(content)
    if isinstance(content, list):
        cleaned = []
        for block in content:
            if isinstance(block, dict):
                cleaned.append({
                    k: _clean(v) if isinstance(v, str) else v
                    for k, v in block.items()
                })
            elif isinstance(block, str):
                cleaned.append(_clean(block))
            else:
                cleaned.append(block)
        return cleaned
    return content


class SanitizeMiddleware(AgentMiddleware):
    """Strip surrogate characters from all messages before model calls.

    Must be placed FIRST in the middleware stack so it runs before
    any serialization happens.
    """

    @property
    def name(self) -> str:
        return "SanitizeMiddleware"

    def before_model(self, state, runtime) -> dict[str, Any] | None:
        """Sanitize all message content to remove surrogates."""
        messages = (
            state.get("messages", [])
            if isinstance(state, dict)
            else getattr(state, "messages", [])
        )

        if not messages:
            return None

        needs_fix = False
        for msg in messages:
            content = getattr(msg, "content", None)
            if content is None:
                continue
            text = content if isinstance(content, str) else str(content)
            if _SURROGATE_RE.search(text):
                needs_fix = True
                break

        if not needs_fix:
            return None

        # Rebuild messages with sanitized content
        cleaned_messages = []
        for msg in messages:
            content = getattr(msg, "content", None)
            if content is not None:
                new_content = _sanitize_content(content)
                if new_content is not content:
                    msg = copy(msg)
                    msg.content = new_content
            cleaned_messages.append(msg)

        return {"messages": cleaned_messages}
