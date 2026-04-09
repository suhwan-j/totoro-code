"""Live status dashboard — real-time visibility into agent orchestration.

Thread-safe: multiple subagent workers can update concurrently.
"""
import sys
import time
import random
import shutil
import threading
from collections import deque
from dataclasses import dataclass, field

from totoro.utils import sanitize_text


# ─── ANSI helpers (palette-based) ───
from totoro.colors import (
    RESET as _RESET, BOLD as _BOLD,
    DIM as _DIM, IVORY_DK,
    BLUE as _CYAN, BLUE_LT as _BODY, BLUE_DK,
    AMBER as _YELLOW, AMBER_LT,
    COPPER as _RED,
    IVORY as _SECONDARY, IVORY_LT as _WHITE,
    ACCENT,
)
_ESC = "\033["
_GREEN = AMBER_LT           # progress / done → amber light
_BLUE = _CYAN                # tools → blue
_MAGENTA = _SECONDARY        # subagent spinner → ivory

_ICON_DONE = "✓"
_ICON_ACTIVE = "▸"
_ICON_PENDING = "○"
_ICON_AGENT = "◈"
_ICON_TOOL = "⚡"

# ─── Spinner frames for thinking animation ───
_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ─── Fun agent names ───
_AGENT_NAMES = [
    "Nova", "Pixel", "Spark", "Nimbus", "Echo",
    "Quasar", "Bolt", "Orbit", "Flux", "Comet",
    "Prism", "Vortex", "Nebula", "Helix", "Drift",
]

# ─── Fun subagent names mapped by type ───
_SUBAGENT_NAMES = {
    "coder":      ["Bytesmith", "Syntex", "Forger", "Weaver", "Cipher"],
    "researcher": ["Scout", "Seeker", "Oracle", "Lens", "Probe"],
    "explorer":   ["Pathfinder", "Rover", "Compass", "Atlas", "Trailblazer"],
    "reviewer":   ["Sentinel", "Warden", "Inspector", "Aegis", "Guardian"],
    "planner":    ["Architect", "Blueprint", "Strategist", "Navigator", "Compass"],
}

_used_names: set = set()


def _pick_agent_name() -> str:
    """Pick a random fun name for the main agent (per session)."""
    return random.choice(_AGENT_NAMES)


def _pick_subagent_name(agent_type: str) -> str:
    """Pick a fun name for a subagent based on its type."""
    names = _SUBAGENT_NAMES.get(agent_type, _AGENT_NAMES)
    available = [n for n in names if n not in _used_names]
    if not available:
        available = names
    name = random.choice(available)
    _used_names.add(name)
    return name


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
        self.completed_subagents: deque[SubagentInfo] = deque(maxlen=50)
        self.current_tool: str | None = None
        self.current_tool_args: str = ""
        self.tool_count: int = 0
        self.phase: str = "Initializing"
        self._last_panel_lines: int = 0
        self._panel_enabled: bool = True
        self.activity_log: deque[str] = deque(maxlen=6)  # Recent file operations
        self._dirty: bool = True  # Only re-render when state changes
        self._pane_manager = None  # Set by CLI for detailed subagent view
        self.agent_name: str = _pick_agent_name()
        self._spinner_idx: int = 0
        self._subagent_display_names: dict[str, str] = {}  # label -> fun name
        self._got_ai_text: bool = False  # True once AI starts outputting text

    # ─── Event handlers (thread-safe) ───

    def _mark_dirty(self):
        """Mark state as changed so next render() actually draws."""
        self._dirty = True

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
            self._mark_dirty()

    def on_tool_start(self, name: str, args: dict):
        with self._lock:
            self.tool_count += 1
            self.current_tool = name
            self.current_tool_args = _format_tool_summary(name, args)
            self._mark_dirty()

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
                self._mark_dirty()
            return

        with self._lock:
            if self.phase in ("Initializing", "Planning"):
                self.phase = "Executing"
                self._mark_dirty()

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
            self._mark_dirty()

    def on_subagent_start(self, name: str, description: str):
        with self._lock:
            # Assign a fun display name based on agent type
            agent_type = name.rsplit("-", 1)[0] if "-" in name else name
            display_name = _pick_subagent_name(agent_type)
            self._subagent_display_names[name] = display_name
            self.active_subagents[name] = SubagentInfo(
                name=name,
                description=description[:100],
            )
            self.phase = "Executing"
            self._mark_dirty()

    def on_subagent_end(self, name: str):
        with self._lock:
            if name in self.active_subagents:
                info = self.active_subagents.pop(name)
                self.completed_subagents.append(info)
            self.current_tool = None
            self.current_tool_args = ""
            self._mark_dirty()

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
            elif tool_name == "execute":
                cmd = tool_args.get("command", "")[:50]
                self.activity_log.append(f"$ {agent_name}: {cmd}")
            self._mark_dirty()

    def advance_plan(self):
        """Mark the next pending/in_progress todo as completed. Called by orchestrator."""
        with self._lock:
            for todo in self.todos:
                if todo.status in ("pending", "in_progress"):
                    todo.status = "completed"
                    self._mark_dirty()
                    return True
            return False

    def set_plan_item_active(self, index: int):
        """Mark a specific todo as in_progress."""
        with self._lock:
            if 0 <= index < len(self.todos):
                self.todos[index].status = "in_progress"
                self._mark_dirty()

    # ─── Rendering (called from render thread or main thread) ───

    def render(self):
        if not self._panel_enabled:
            return
        with self._lock:
            # Once AI text has started, stop showing the thinking indicator
            if self._got_ai_text and not self.active_subagents and not self.todos:
                if self._last_panel_lines > 0:
                    self._clear_previous()
                    self._last_panel_lines = 0
                return

            # Always advance spinner for animation
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER)
            self._dirty = True  # spinner always changes

            self._clear_previous()

            # Simple thinking animation when no tools/plan/subagents
            has_meaningful_status = (
                self.tool_count > 0 or self.todos or self.active_subagents
            )
            if not has_meaningful_status:
                lines = self._build_thinking_line()
            else:
                lines = self._build_panel()

            output = "\n".join(lines)
            sys.stdout.flush()
            try:
                sys.stdout.write(output + "\n")
                sys.stdout.flush()
            except UnicodeEncodeError:
                sys.stdout.write(sanitize_text(output) + "\n")
                sys.stdout.flush()
            self._last_panel_lines = len(lines)

    def _clear_previous(self):
        if self._last_panel_lines > 0:
            # Move cursor up, erase each line, move cursor back up — all on stdout
            sys.stdout.write(f"{_ESC}{self._last_panel_lines}A")
            for _ in range(self._last_panel_lines):
                sys.stdout.write(f"{_ESC}2K\n")
            sys.stdout.write(f"{_ESC}{self._last_panel_lines}A")
            sys.stdout.flush()

    def _build_thinking_line(self) -> list[str]:
        """Minimal thinking indicator: spinning dot + agent name."""
        spinner = _SPINNER[self._spinner_idx]
        return [f"  {_CYAN}{spinner}{_RESET} {_DIM}{self.agent_name} is thinking...{_RESET}"]

    def _build_panel(self) -> list[str]:
        width = shutil.get_terminal_size().columns - 2
        lines = []

        # ─── Header ───
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
        counter_str = f" {_DIM}{' · '.join(counters)}{_RESET}"

        spinner = _SPINNER[self._spinner_idx]
        lines.append(f"{_DIM}── {_CYAN}{spinner} {self.agent_name}{_RESET} {phase_color}{self.phase}{_RESET}{counter_str} {_DIM}{'─' * max(0, width - 40)}{_RESET}")

        # ─── Plan ───
        if self.todos:
            if total_count > 0:
                ratio = done_count / total_count
                bar_width = min(20, width - 20)
                filled = int(ratio * bar_width)
                bar = f"{'█' * filled}{'░' * (bar_width - filled)}"
                pct = int(ratio * 100)
                lines.append(f"   {_GREEN}{bar}{_RESET} {pct}%")

            for todo in self.todos[:8]:
                if todo.status == "completed":
                    icon = f"{_GREEN}{_ICON_DONE}{_RESET}"
                    text = f"{_DIM}{todo.content[:width - 8]}{_RESET}"
                elif todo.status == "in_progress":
                    icon = f"{_YELLOW}{_ICON_ACTIVE}{_RESET}"
                    text = f"{_WHITE}{todo.content[:width - 8]}{_RESET}"
                else:
                    icon = f"{_DIM}{_ICON_PENDING}{_RESET}"
                    text = f"{_DIM}{todo.content[:width - 8]}{_RESET}"
                lines.append(f"   {icon} {text}")

            if len(self.todos) > 8:
                lines.append(f"   {_DIM}... +{len(self.todos) - 8} more{_RESET}")

        # ─── Subagents (tree structure) ───
        if self.active_subagents:
            # Collect pane data
            pane_data = {}
            if self._pane_manager:
                for pane in self._pane_manager.get_panes():
                    pane_data[pane.label] = pane

            agent_list = list(self.active_subagents.items())
            for idx, (name, info) in enumerate(agent_list):
                is_last = idx == len(agent_list) - 1
                pane = pane_data.get(name)
                elapsed = time.time() - info.started_at
                elapsed_str = f"{elapsed:.0f}s"
                tool_count = pane.tool_count if pane else info.tool_count

                # Status icon with spinner for active agents
                if pane and pane.status == "done":
                    s_icon = f"{_GREEN}{_ICON_DONE}{_RESET}"
                elif pane and pane.status == "error":
                    s_icon = f"{_RED}✗{_RESET}"
                else:
                    s_icon = f"{_MAGENTA}{_SPINNER[self._spinner_idx]}{_RESET}"

                # Tree connector
                connector = "└── " if is_last else "├── "
                child_prefix = "    " if is_last else "│   "

                # Use fun display name
                display_name = self._subagent_display_names.get(name, name)
                lines.append(
                    f"   {_DIM}{connector}{_RESET}{s_icon} {_BOLD}{display_name}{_RESET}"
                    f" {_DIM}({name}){_RESET}"
                    f"  {_DIM}{elapsed_str} · {tool_count} tools{_RESET}"
                )

                # Recent activity from pane
                if pane and pane.recent_lines:
                    for line in pane.recent_lines[-3:]:
                        display = sanitize_text(str(line))[:width - 12]
                        lines.append(f"   {_DIM}{child_prefix}{_RESET}{display}")
                else:
                    desc = info.description[:width - 12]
                    lines.append(f"   {_DIM}{child_prefix}{desc}{_RESET}")

                # Current tool
                if pane and pane.current_tool:
                    lines.append(f"   {_DIM}{child_prefix}{_BLUE}{_ICON_TOOL} {pane.current_tool}{_RESET}")
                elif info.current_tool:
                    lines.append(f"   {_DIM}{child_prefix}{_BLUE}{_ICON_TOOL} {info.current_tool}{_RESET}")

        # ─── Current Tool (main agent, no subagents) ───
        elif self.current_tool and self.current_tool not in ("write_todos", "task", "orchestrate_tool"):
            lines.append(f"   {_BLUE}{_ICON_TOOL} {self.current_tool}{_RESET} {_DIM}{self.current_tool_args[:width - 15]}{_RESET}")

        # ─── Activity Log ───
        if self.activity_log:
            lines.append(f"   {_DIM}── Recent {'─' * max(0, width - 14)}{_RESET}")
            for entry in self.activity_log:
                if entry.startswith("+"):
                    lines.append(f"   {_GREEN}{entry[:width - 6]}{_RESET}")
                elif entry.startswith("~"):
                    lines.append(f"   {_YELLOW}{entry[:width - 6]}{_RESET}")
                else:
                    lines.append(f"   {_DIM}{entry[:width - 6]}{_RESET}")

        # ─── Footer ───
        lines.append(f"{_DIM}{'─' * width}{_RESET}")
        return lines

    def render_final_summary(self):
        with self._lock:
            self._clear_previous()
            self._last_panel_lines = 0

        # Only show summary when there's meaningful activity (plan or subagents)
        total = len(self.todos)
        agent_total = len(self.completed_subagents)
        if total == 0 and agent_total == 0:
            return

        width = shutil.get_terminal_size().columns - 2
        done = sum(1 for t in self.todos if t.status == "completed")

        parts = [f"Tools: {self.tool_count}"]
        if total > 0:
            parts.append(f"Plan: {done}/{total}")
        if agent_total > 0:
            parts.append(f"Subagents: {agent_total}")

        summary = " · ".join(parts)
        line = f"{_DIM}── {_CYAN}Done{_DIM} ({summary}) {'─' * max(0, width - len(summary) - 12)}{_RESET}"
        print(line, flush=True)


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
