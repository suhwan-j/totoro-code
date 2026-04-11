"""Subagent pane state manager.

Tracks per-subagent progress (lines, tools, files, status).
Rendering is handled by StatusTracker which reads pane state.
"""

import time
import threading
from dataclasses import dataclass, field

from totoro.colors import DIM as _DIM, AMBER_LT as _GREEN, COPPER as _RED, BOLD as _BOLD, RESET as _RESET


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
class ToolCall:
    """Record of a single tool invocation."""
    name: str
    summary: str
    is_error: bool = False
    result_preview: str = ""
    file_path: str = ""
    content_lines: list = field(default_factory=list)  # preview lines for write/edit
    line_count: int = 0  # total lines written/edited


@dataclass
class PaneState:
    """State of a single subagent."""
    label: str
    description: str
    recent_lines: list = field(default_factory=list)
    tool_count: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    status: str = "running"
    current_tool: str = ""
    files: list = field(default_factory=list)
    max_lines: int = 20
    pid: int | None = None
    tool_history: list = field(default_factory=list)  # list[ToolCall]
    token_input: int = 0
    token_output: int = 0
    token_cached: int = 0
    summary_text: str = ""  # First meaningful line from subagent final_text
    _pending_tool_queue: list = field(default_factory=list)  # queue of (name, args) from tool_start events

    @property
    def elapsed(self) -> str:
        end = self.end_time if self.end_time else time.time()
        secs = int(end - self.start_time)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m{secs % 60}s"

    def append(self, text: str):
        """Append a line to recent output, trimming to max_lines.

        Args:
            text: Line of text to append.
        """
        self.recent_lines.append(text)
        if len(self.recent_lines) > self.max_lines:
            self.recent_lines = self.recent_lines[-self.max_lines:]


class PaneManager:
    """Manages per-subagent state. StatusTracker reads this for rendering."""

    def __init__(self):
        self._lock = threading.Lock()
        self.panes: dict[str, PaneState] = {}

    def add_subagent(self, label: str, description: str, pid: int | None = None):
        """Register a new subagent pane.

        Args:
            label: Unique label for the subagent.
            description: Short description of the subagent's task.
            pid: Optional process ID of the subagent.
        """
        with self._lock:
            self.panes[label] = PaneState(label=label, description=description, pid=pid)

    def set_pid(self, label: str, pid: int):
        """Set the process ID for an existing subagent pane.

        Args:
            label: Label of the subagent.
            pid: Process ID to assign.
        """
        with self._lock:
            pane = self.panes.get(label)
            if pane:
                pane.pid = pid

    def update_subagent(self, event: SubagentEvent):
        """Process an event from a subagent and update pane state.

        Args:
            event: Event containing type, label, and data from the subagent.
        """
        with self._lock:
            pane = self.panes.get(event.label)
            if pane is None:
                return

            if event.event_type == "ai_text":
                text = event.data.get("text", "")
                for line in text.splitlines():
                    if line.strip():
                        pane.append(line.strip()[:120])

            elif event.event_type == "tool_start":
                summary = event.data.get("summary", event.data.get("name", "?"))
                pane.current_tool = summary
                pane._pending_tool_queue.append({
                    "name": event.data.get("name", ""),
                    "args": event.data.get("args", {}),
                    "summary": summary,
                })
                pane.append(f"▸ {summary}")

            elif event.event_type == "tool_end":
                pane.tool_count += 1
                name = event.data.get("name", "")
                result_text = event.data.get("result", "")
                is_error = event.data.get("is_error", False)

                # Match with pending tool_start by name (FIFO within same name)
                pending = {}
                summary = name
                for i, p in enumerate(pane._pending_tool_queue):
                    if p["name"] == name:
                        pending = p.get("args", {})
                        summary = p.get("summary", name)
                        pane._pending_tool_queue.pop(i)
                        break
                if not pending and pane._pending_tool_queue:
                    # Fallback: pop first entry if no name match
                    p = pane._pending_tool_queue.pop(0)
                    pending = p.get("args", {})
                    summary = p.get("summary", name)

                file_path = pending.get("file_path", "")
                content_lines = pending.get("content_preview", [])
                line_count = pending.get("line_count", 0)

                pane.tool_history.append(ToolCall(
                    name=name, summary=summary,
                    is_error=is_error, result_preview=result_text[:200],
                    file_path=file_path,
                    content_lines=content_lines,
                    line_count=line_count,
                ))
                # Show result preview for verbose insight
                if is_error and result_text:
                    pane.append(f"✗ {name}: {result_text[:80]}")
                elif result_text and name not in ("write_file", "edit_file"):
                    # Show short result for non-file tools
                    pane.append(f"  ⎿ {result_text[:80]}")
                pane.current_tool = ""

            elif event.event_type == "tokens":
                pane.token_input += event.data.get("input", 0)
                pane.token_output += event.data.get("output", 0)
                pane.token_cached += event.data.get("cached", 0)

            elif event.event_type == "diff":
                text = event.data.get("text", "")
                for line in text.splitlines()[:6]:
                    pane.append(line)

            elif event.event_type == "hitl_request":
                tools = event.data.get("tool_requests", [])
                names = ", ".join(t.get("name", "?") for t in tools)
                pane.status = "waiting_approval"
                pane.current_tool = f"⏸ {names} (waiting for approval)"
                pane.append(f"⏸ Waiting: {names}")

            elif event.event_type == "hitl_response":
                if pane.status == "waiting_approval":
                    pane.status = "running"
                    pane.current_tool = ""

            elif event.event_type == "done":
                if pane.status in ("running", "waiting_approval"):
                    pane.status = "done"
                    pane.end_time = time.time()

            elif event.event_type == "error":
                pane.status = "error"
                pane.end_time = time.time()
                pane.append(f"✗ {event.data.get('text', 'Error')[:60]}")

    def complete_subagent(self, label: str):
        """Mark a subagent as done and record its end time.

        Args:
            label: Label of the subagent to complete.
        """
        with self._lock:
            pane = self.panes.get(label)
            if pane:
                pane.status = "done"
                if pane.end_time is None:
                    pane.end_time = time.time()

    def get_panes(self) -> list[PaneState]:
        """Get snapshot of all panes for rendering.

        Returns:
            List of current PaneState objects.
        """
        with self._lock:
            return list(self.panes.values())

    def get_summary(self) -> str:
        """Build completion summary.

        Returns:
            Formatted multi-line string summarizing all subagent results.
        """
        with self._lock:
            parts = []
            for pane in self.panes.values():
                icon = f"{_GREEN}✓{_RESET}" if pane.status == "done" else f"{_RED}✗{_RESET}"
                files_str = ""
                if pane.files:
                    files_str = f", {len(pane.files)} files"
                tok_str = ""
                if pane.token_input or pane.token_output:
                    from totoro.status import _format_tokens_detail
                    tok_str = f", {_format_tokens_detail(pane.token_input, pane.token_output, pane.token_cached)}"
                line = (
                    f"  {icon} {_BOLD}{pane.label}{_RESET} "
                    f"({pane.elapsed}, {pane.tool_count} tools{files_str}{tok_str})"
                )
                parts.append(line)
                if pane.summary_text:
                    parts.append(f"   {_DIM}⎿ {pane.summary_text}{_RESET}")
            if parts:
                return f"\n{_DIM}── Subagent Summary ──{_RESET}\n" + "\n".join(parts)
            return ""

    def clear(self):
        with self._lock:
            self.panes.clear()

    @property
    def is_active(self) -> bool:
        """True if any subagent is still running or waiting for approval."""
        with self._lock:
            return any(p.status in ("running", "waiting_approval") for p in self.panes.values())
