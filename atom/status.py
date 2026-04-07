"""Live status dashboard — real-time visibility into agent orchestration.

Thread-safe: multiple subagent workers can update concurrently.
"""
import sys
import time
import shutil
import threading
from dataclasses import dataclass, field

from atom.utils import sanitize_text


# ─── ANSI helpers ───
_ESC = "\033["
_RESET = f"{_ESC}0m"
_BOLD = f"{_ESC}1m"
_DIM = f"{_ESC}0;90m"
_CYAN = f"{_ESC}1;36m"
_YELLOW = f"{_ESC}1;33m"
_GREEN = f"{_ESC}1;32m"
_BLUE = f"{_ESC}0;34m"
_MAGENTA = f"{_ESC}0;35m"
_RED = f"{_ESC}1;31m"
_WHITE = f"{_ESC}0;37m"

_H = "─"
_V = "│"
_TL = "╭"
_TR = "╮"
_BL = "╰"
_BR = "╯"
_LT = "├"
_RT = "┤"

_ICON_DONE = "✓"
_ICON_ACTIVE = "▸"
_ICON_PENDING = "○"
_ICON_AGENT = "◈"
_ICON_TOOL = "⚡"


@dataclass
class TodoItem:
    content: str
    status: str = "pending"


@dataclass
class SubagentInfo:
    name: str
    description: str
    started_at: float = field(default_factory=time.time)
    tool_count: int = 0
    current_tool: str = ""


class StatusTracker:
    """Thread-safe tracker and renderer for agent orchestration status."""

    def __init__(self):
        self._lock = threading.Lock()
        self.todos: list[TodoItem] = []
        self.active_subagents: dict[str, SubagentInfo] = {}
        self.completed_subagents: list[SubagentInfo] = []
        self.current_tool: str | None = None
        self.current_tool_args: str = ""
        self.tool_count: int = 0
        self.phase: str = "Initializing"
        self._last_panel_lines: int = 0
        self._panel_enabled: bool = True
        self.activity_log: list[str] = []  # Recent file operations
        self._max_log: int = 6

    # ─── Event handlers (thread-safe) ───

    def on_todos_updated(self, todos_data: list[dict]):
        with self._lock:
            self.todos = [
                TodoItem(
                    content=t.get("content", str(t)),
                    status=t.get("status", "pending"),
                )
                for t in todos_data
            ]
            if self.phase == "Initializing":
                self.phase = "Planning"

    def on_tool_start(self, name: str, args: dict):
        with self._lock:
            self.tool_count += 1
            self.current_tool = name
            self.current_tool_args = _format_tool_summary(name, args)

        if name == "write_todos":
            self.on_todos_updated(args.get("todos", []))
            return

        if name == "task":
            subagent_type = args.get("subagent_type", "general-purpose")
            description = args.get("description", "")
            self.on_subagent_start(subagent_type, description)
            return

        if name == "orchestrate_tool":
            with self._lock:
                self.phase = "Executing"
            return

        with self._lock:
            if self.phase in ("Initializing", "Planning"):
                self.phase = "Executing"

    def on_tool_end(self, name: str, result_preview: str = ""):
        if name == "task":
            with self._lock:
                if self.active_subagents:
                    finished_name = next(iter(self.active_subagents))
            self.on_subagent_end(finished_name)
            return

        with self._lock:
            self.current_tool = None
            self.current_tool_args = ""

    def on_subagent_start(self, name: str, description: str):
        with self._lock:
            self.active_subagents[name] = SubagentInfo(
                name=name,
                description=description[:100],
            )
            self.phase = "Executing"

    def on_subagent_end(self, name: str):
        with self._lock:
            if name in self.active_subagents:
                info = self.active_subagents.pop(name)
                self.completed_subagents.append(info)
            self.current_tool = None
            self.current_tool_args = ""

    def on_subagent_tool(self, agent_name: str, tool_name: str, tool_args: dict):
        """Called from subagent worker threads when a tool is invoked."""
        with self._lock:
            self.tool_count += 1
            info = self.active_subagents.get(agent_name)
            if info:
                info.tool_count += 1
                info.current_tool = f"{tool_name} {_format_tool_summary(tool_name, tool_args)}"

            # Log file operations for visibility
            if tool_name in ("write_file", "edit_file"):
                path = tool_args.get("path", tool_args.get("file_path", "?"))
                short = path.split("/")[-1] if "/" in path else path
                icon = "+" if tool_name == "write_file" else "~"
                self.activity_log.append(f"{icon} {agent_name}: {short}")
                if len(self.activity_log) > self._max_log:
                    self.activity_log.pop(0)
            elif tool_name == "execute":
                cmd = tool_args.get("command", "")[:50]
                self.activity_log.append(f"$ {agent_name}: {cmd}")
                if len(self.activity_log) > self._max_log:
                    self.activity_log.pop(0)

    def advance_plan(self):
        """Mark the next pending/in_progress todo as completed. Called by orchestrator."""
        with self._lock:
            for todo in self.todos:
                if todo.status in ("pending", "in_progress"):
                    todo.status = "completed"
                    return True
            return False

    def set_plan_item_active(self, index: int):
        """Mark a specific todo as in_progress."""
        with self._lock:
            if 0 <= index < len(self.todos):
                self.todos[index].status = "in_progress"

    # ─── Rendering (called from render thread or main thread) ───

    def render(self):
        if not self._panel_enabled:
            return
        with self._lock:
            self._clear_previous()
            lines = self._build_panel()
            output = "\n".join(lines)
            try:
                print(output, file=sys.stderr, flush=True)
            except UnicodeEncodeError:
                print(sanitize_text(output), file=sys.stderr, flush=True)
            self._last_panel_lines = len(lines)

    def _clear_previous(self):
        if self._last_panel_lines > 0:
            sys.stderr.write(f"{_ESC}{self._last_panel_lines}A")
            for _ in range(self._last_panel_lines):
                sys.stderr.write(f"{_ESC}2K\n")
            sys.stderr.write(f"{_ESC}{self._last_panel_lines}A")
            sys.stderr.flush()

    def _build_panel(self) -> list[str]:
        width = min(shutil.get_terminal_size().columns - 2, 72)
        lines = []

        # ─── Header ───
        title = f" {_ICON_AGENT} Atom Status "
        pad = width - len(title) - 2
        lines.append(f"{_DIM}{_TL}{_H}{_CYAN}{title}{_DIM}{_H * max(0, pad)}{_TR}{_RESET}")

        # ─── Phase & Counters ───
        phase_color = _YELLOW if self.phase == "Planning" else _GREEN if self.phase == "Executing" else _DIM
        agent_count = len(self.active_subagents)
        done_count = sum(1 for t in self.todos if t.status == "completed")
        total_count = len(self.todos)

        counters = []
        if total_count > 0:
            counters.append(f"Plan: {done_count}/{total_count}")
        counters.append(f"Tools: {self.tool_count}")
        if agent_count > 0:
            counters.append(f"Agents: {agent_count}")

        counter_str = f"{_DIM}  {' · '.join(counters)}{_RESET}"
        lines.append(f"{_DIM}{_V}{_RESET} {phase_color}{_BOLD}{self.phase}{_RESET}{counter_str}")

        # ─── Plan Progress ───
        if self.todos:
            lines.append(f"{_DIM}{_LT}{_H * 2} Plan {_H * max(0, width - 9)}{_RT}{_RESET}")
            if total_count > 0:
                ratio = done_count / total_count
                bar_width = min(20, width - 20)
                filled = int(ratio * bar_width)
                bar = f"{'█' * filled}{'░' * (bar_width - filled)}"
                pct = int(ratio * 100)
                lines.append(f"{_DIM}{_V}{_RESET}  {_GREEN}{bar}{_RESET} {pct}%")

            for todo in self.todos[:8]:
                if todo.status == "completed":
                    icon = f"{_GREEN}{_ICON_DONE}{_RESET}"
                    text = f"{_DIM}{todo.content[:width - 10]}{_RESET}"
                elif todo.status == "in_progress":
                    icon = f"{_YELLOW}{_ICON_ACTIVE}{_RESET}"
                    text = f"{_WHITE}{todo.content[:width - 10]}{_RESET}"
                else:
                    icon = f"{_DIM}{_ICON_PENDING}{_RESET}"
                    text = f"{_DIM}{todo.content[:width - 10]}{_RESET}"
                lines.append(f"{_DIM}{_V}{_RESET}  {icon} {text}")

            if len(self.todos) > 8:
                lines.append(f"{_DIM}{_V}  ... +{len(self.todos) - 8} more{_RESET}")

        # ─── Active Subagents (with their current tool) ───
        if self.active_subagents:
            lines.append(f"{_DIM}{_LT}{_H * 2} Subagents ({agent_count} active) {_H * max(0, width - 24)}{_RT}{_RESET}")
            for name, info in list(self.active_subagents.items()):
                elapsed = time.time() - info.started_at
                elapsed_str = f"{elapsed:.0f}s"
                lines.append(
                    f"{_DIM}{_V}{_RESET}  {_MAGENTA}{_ICON_AGENT} {name}{_RESET}"
                    f" {_DIM}({elapsed_str}, {info.tool_count} tools){_RESET}"
                )
                desc = info.description[:width - 8]
                lines.append(f"{_DIM}{_V}{_RESET}    {desc}")
                if info.current_tool:
                    tool_display = info.current_tool[:width - 10]
                    lines.append(f"{_DIM}{_V}{_RESET}    {_BLUE}{_ICON_TOOL} {tool_display}{_RESET}")

        # ─── Current Tool (main agent) ───
        elif self.current_tool and self.current_tool not in ("write_todos", "task", "orchestrate_tool"):
            lines.append(f"{_DIM}{_LT}{_H * 2} Active {_H * max(0, width - 11)}{_RT}{_RESET}")
            lines.append(f"{_DIM}{_V}{_RESET}  {_BLUE}{_ICON_TOOL} {self.current_tool}{_RESET} {_DIM}{self.current_tool_args[:width - 15]}{_RESET}")

        # ─── Activity Log (file changes from subagents) ───
        if self.activity_log:
            lines.append(f"{_DIM}{_LT}{_H * 2} Recent {_H * max(0, width - 11)}{_RT}{_RESET}")
            for entry in self.activity_log[-self._max_log:]:
                if entry.startswith("+"):
                    lines.append(f"{_DIM}{_V}{_RESET}  {_GREEN}{entry[:width - 6]}{_RESET}")
                elif entry.startswith("~"):
                    lines.append(f"{_DIM}{_V}{_RESET}  {_YELLOW}{entry[:width - 6]}{_RESET}")
                else:
                    lines.append(f"{_DIM}{_V}{_RESET}  {_DIM}{entry[:width - 6]}{_RESET}")

        # ─── Footer ───
        lines.append(f"{_DIM}{_BL}{_H * (width - 2)}{_BR}{_RESET}")
        return lines

    def render_final_summary(self):
        with self._lock:
            self._clear_previous()
            self._last_panel_lines = 0

        width = min(shutil.get_terminal_size().columns - 2, 72)
        done = sum(1 for t in self.todos if t.status == "completed")
        total = len(self.todos)
        agent_total = len(self.completed_subagents)

        parts = [f"Tools: {self.tool_count}"]
        if total > 0:
            parts.append(f"Plan: {done}/{total}")
        if agent_total > 0:
            parts.append(f"Subagents: {agent_total}")

        summary = " · ".join(parts)
        line = f"{_DIM}── {_CYAN}Done{_DIM} ({summary}) {'─' * max(0, width - len(summary) - 12)}{_RESET}"
        print(line, file=sys.stderr, flush=True)


def _format_tool_summary(name: str, args: dict) -> str:
    if name == "execute":
        return args.get("command", "")[:60]
    if name in ("write_file", "edit_file", "read_file"):
        return args.get("path", args.get("file_path", ""))
    if name == "git_tool":
        return args.get("command", "")[:60]
    if name in ("web_search_tool", "fetch_url_tool"):
        return args.get("query", args.get("url", ""))[:60]
    if name in ("ls", "glob"):
        return args.get("path", args.get("pattern", ""))[:60]
    if name == "grep":
        return args.get("pattern", "")[:60]
    return ""
