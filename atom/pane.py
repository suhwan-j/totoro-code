"""Subagent pane state manager.

Tracks per-subagent progress (lines, tools, files, status).
Rendering is handled by StatusTracker which reads pane state.
"""

import time
import threading
from dataclasses import dataclass, field

_DIM = "\033[0;90m"
_GREEN = "\033[0;32m"
_RED = "\033[1;31m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


@dataclass
class SubagentEvent:
    """Event from a subagent worker."""
    label: str
    event_type: str  # "ai_text" | "tool_start" | "tool_end" | "diff" | "done" | "error"
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class SubagentResult:
    """Structured result from a completed subagent."""
    final_text: str = ""
    tools_used: list = field(default_factory=list)
    files_modified: list = field(default_factory=list)


@dataclass
class PaneState:
    """State of a single subagent."""
    label: str
    description: str
    recent_lines: list = field(default_factory=list)
    tool_count: int = 0
    start_time: float = field(default_factory=time.time)
    status: str = "running"
    current_tool: str = ""
    files: list = field(default_factory=list)
    max_lines: int = 20

    @property
    def elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m{secs % 60}s"

    def append(self, text: str):
        self.recent_lines.append(text)
        if len(self.recent_lines) > self.max_lines:
            self.recent_lines = self.recent_lines[-self.max_lines:]


class PaneManager:
    """Manages per-subagent state. StatusTracker reads this for rendering."""

    def __init__(self):
        self._lock = threading.Lock()
        self.panes: dict[str, PaneState] = {}

    def add_subagent(self, label: str, description: str):
        with self._lock:
            self.panes[label] = PaneState(label=label, description=description)

    def update_subagent(self, event: SubagentEvent):
        with self._lock:
            pane = self.panes.get(event.label)
            if pane is None:
                return

            if event.event_type == "ai_text":
                text = event.data.get("text", "")
                for line in text.splitlines():
                    if line.strip():
                        pane.append(line.strip()[:80])

            elif event.event_type == "tool_start":
                name = event.data.get("name", "?")
                pane.current_tool = name
                pane.append(f"▸ {name}")

            elif event.event_type == "tool_end":
                pane.tool_count += 1
                pane.current_tool = ""

            elif event.event_type == "diff":
                text = event.data.get("text", "")
                for line in text.splitlines()[:6]:
                    pane.append(line)

            elif event.event_type == "error":
                pane.status = "error"
                pane.append(f"✗ {event.data.get('text', 'Error')[:60]}")

    def complete_subagent(self, label: str):
        with self._lock:
            pane = self.panes.get(label)
            if pane:
                pane.status = "done"

    def get_panes(self) -> list[PaneState]:
        """Get snapshot of all panes for rendering."""
        with self._lock:
            return list(self.panes.values())

    def get_summary(self) -> str:
        """Build completion summary."""
        with self._lock:
            parts = []
            for pane in self.panes.values():
                icon = f"{_GREEN}✓{_RESET}" if pane.status == "done" else f"{_RED}✗{_RESET}"
                files_str = ""
                if pane.files:
                    files_str = f", {len(pane.files)} files"
                parts.append(
                    f"  {icon} {_BOLD}{pane.label}{_RESET} "
                    f"({pane.elapsed}, {pane.tool_count} tools{files_str})"
                )
            if parts:
                return f"\n{_DIM}── Subagent Summary ──{_RESET}\n" + "\n".join(parts)
            return ""

    def clear(self):
        with self._lock:
            self.panes.clear()

    @property
    def is_active(self) -> bool:
        """True if any subagent is still running."""
        with self._lock:
            return any(p.status == "running" for p in self.panes.values())
