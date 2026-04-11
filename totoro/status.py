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
    RESET as _RESET,
    BOLD as _BOLD,
    DIM as _DIM,
    IVORY_DK,
    BLUE as _CYAN,
    BLUE_LT as _BODY,
    BLUE_DK,
    AMBER as _YELLOW,
    AMBER_LT,
    COPPER as _RED,
    IVORY as _SECONDARY,
    IVORY_LT as _WHITE,
    ACCENT,
)

_ESC = "\033["
_GREEN = AMBER_LT  # progress / done → amber light
_BLUE = _CYAN  # tools → blue
_MAGENTA = _SECONDARY  # subagent spinner → ivory

_ICON_DONE = "✓"
_ICON_ACTIVE = "▸"
_ICON_PENDING = "○"
_ICON_AGENT = "◈"
_ICON_TOOL = "⚡"

# ─── Spinner frames for thinking animation ───
_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ─── Totoro character names ───
_MAIN_AGENT_NAME = "Totoro"

_CHARACTER_NAMES = {
    "catbus": "Catbus",  # 네코버스 — Router/Planner
    "satsuki": "Satsuki",  # 사츠키   — Senior Agent
    "mei": "Mei",  # 메이     — Explorer/Researcher
    "tatsuo": "Tatsuo",  # 타츠오   — Knowledge/Reviewer
    "susuwatari": "Susuwatari",  # 스스와타리 — Micro Agent
}

_CHARACTER_ICONS = {
    "catbus": "🚌",
    "satsuki": "🧒",
    "mei": "👧",
    "tatsuo": "👨",
    "susuwatari": "🌱",
}


def _pick_agent_name() -> str:
    """Return main agent name."""
    return _MAIN_AGENT_NAME


def _pick_subagent_name(agent_type: str) -> str:
    """Return character name for the subagent type.

    Args:
        agent_type: Subagent type key (e.g. "satsuki", "satsuki-0").

    Returns:
        Display name for the subagent type.
    """
    # Extract type from label like "satsuki-0" → "satsuki"
    base_type = (
        agent_type.rsplit("-", 1)[0] if "-" in agent_type else agent_type
    )
    return _CHARACTER_NAMES.get(base_type, agent_type)


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
        self._is_tty: bool = (
            sys.stdout.isatty()
        )  # Disable animations when piped
        self.todos: list[TodoItem] = []
        self.active_subagents: dict[str, SubagentInfo] = {}
        self.completed_subagents: deque[SubagentInfo] = deque(maxlen=50)
        self.current_tool: str | None = None
        self.current_tool_args: str = ""
        self.tool_count: int = 0
        self.token_input: int = 0  # Total input tokens (main agent)
        self.token_output: int = 0  # Total output tokens (main agent)
        self.token_cached: int = 0  # Cached input tokens (prompt caching)
        self.phase: str = "Initializing"
        self._last_panel_lines: int = 0
        self._panel_enabled: bool = True
        self.activity_log: deque[str] = deque(
            maxlen=6
        )  # Recent file operations
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

    def on_subagent_tool(
        self, agent_name: str, tool_name: str, tool_args: dict
    ):
        """Record a tool invocation from a subagent worker thread.

        Args:
            agent_name: Label of the subagent that invoked the tool.
            tool_name: Name of the tool being invoked.
            tool_args: Arguments passed to the tool.
        """
        with self._lock:
            self.tool_count += 1
            info = self.active_subagents.get(agent_name)
            if info:
                info.tool_count += 1
                summary = _format_tool_summary(tool_name, tool_args)
                info.current_tool = (
                    f"{tool_name}({summary})" if summary else tool_name
                )
            self._mark_dirty()

    def advance_plan(self):
        """Mark next pending todo as completed."""
        with self._lock:
            for todo in self.todos:
                if todo.status in ("pending", "in_progress"):
                    todo.status = "completed"
                    self._mark_dirty()
                    return True
            return False

    def set_plan_item_active(self, index: int):
        """Mark a specific todo as in_progress.

        Args:
            index: Zero-based index of the todo item to activate.
        """
        with self._lock:
            if 0 <= index < len(self.todos):
                self.todos[index].status = "in_progress"
                self._mark_dirty()

    # ─── Rendering (called from render thread or main thread) ───

    def render(self):
        if not self._panel_enabled or not self._is_tty:
            return
        with self._lock:
            # Once AI text has started, stop showing the thinking indicator
            if self._got_ai_text and not self.active_subagents:
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
            # Move cursor up, erase, move back up
            sys.stdout.write(f"{_ESC}{self._last_panel_lines}A")
            for _ in range(self._last_panel_lines):
                sys.stdout.write(f"{_ESC}2K\n")
            sys.stdout.write(f"{_ESC}{self._last_panel_lines}A")
            sys.stdout.flush()

    def _build_thinking_line(self) -> list[str]:
        """Minimal thinking indicator: spinning dot + agent name."""
        spinner = _SPINNER[self._spinner_idx]
        return [
            f"  {_CYAN}{spinner}{_RESET}"
            f" {_DIM}{self.agent_name}"
            f" is thinking...{_RESET}"
        ]

    def _build_panel(self) -> list[str]:
        width = shutil.get_terminal_size().columns - 2
        lines = []

        # ─── Header ───
        phase_color = (
            _YELLOW
            if self.phase == "Planning"
            else _GREEN
            if self.phase == "Executing"
            else _DIM
        )
        agent_count = len(self.active_subagents)
        done_count = sum(1 for t in self.todos if t.status == "completed")
        total_count = len(self.todos)

        counters = []
        if total_count > 0:
            counters.append(f"Plan: {done_count}/{total_count}")
        counters.append(f"Tools: {self.tool_count}")
        if agent_count > 0:
            counters.append(f"Agents: {agent_count}")
        total_in = self.token_input
        total_out = self.token_output
        total_cached = self.token_cached
        if self._pane_manager:
            for p in self._pane_manager.get_panes():
                total_in += p.token_input
                total_out += p.token_output
                total_cached += getattr(p, "token_cached", 0)
        if total_in or total_out:
            counters.append(
                _format_tokens_detail(total_in, total_out, total_cached)
            )
        counter_str = f" {_DIM}{' · '.join(counters)}{_RESET}"
        # Visible length of counter text (without ANSI codes)
        counter_plain = " " + " · ".join(counters)

        spinner = _SPINNER[self._spinner_idx]
        # Calculate trailing dashes to fit exactly within terminal width
        # Visible prefix length calculation
        prefix_len = (
            3
            + 1
            + 1
            + len(self.agent_name)
            + 1
            + len(self.phase)
            + len(counter_plain)
            + 1
        )
        trailing = max(0, width - prefix_len)
        lines.append(
            f"{_DIM}── {_CYAN}{spinner}"
            f" {self.agent_name}{_RESET}"
            f" {phase_color}{self.phase}{_RESET}"
            f"{counter_str}"
            f" {_DIM}{'─' * trailing}{_RESET}"
        )

        # ─── Plan ───
        if self.todos:
            if total_count > 0:
                ratio = done_count / total_count
                bar_width = max(1, min(20, width - 20))
                filled = int(ratio * bar_width)
                bar = f"{'█' * filled}{'░' * (bar_width - filled)}"
                pct = int(ratio * 100)
                lines.append(f"   {_GREEN}{bar}{_RESET} {pct}%")

            for todo in self.todos[:8]:
                if todo.status == "completed":
                    icon = f"{_GREEN}{_ICON_DONE}{_RESET}"
                    text = f"{_DIM}{todo.content[: width - 8]}{_RESET}"
                elif todo.status == "in_progress":
                    icon = f"{_YELLOW}{_ICON_ACTIVE}{_RESET}"
                    text = f"{_WHITE}{todo.content[: width - 8]}{_RESET}"
                else:
                    icon = f"{_DIM}{_ICON_PENDING}{_RESET}"
                    text = f"{_DIM}{todo.content[: width - 8]}{_RESET}"
                lines.append(f"   {icon} {text}")

            if len(self.todos) > 8:
                lines.append(
                    f"   {_DIM}... +{len(self.todos) - 8} more{_RESET}"
                )

        # ─── Subagents (Claude Code style) ───
        if self.active_subagents:
            # Collect pane data
            pane_data = {}
            if self._pane_manager:
                for pane in self._pane_manager.get_panes():
                    pane_data[pane.label] = pane

            agent_list = list(self.active_subagents.items())
            for idx, (name, info) in enumerate(agent_list):
                pane = pane_data.get(name)
                if pane:
                    elapsed_str = pane.elapsed
                    tool_count = pane.tool_count
                    token_in = pane.token_input
                    token_out = pane.token_output
                else:
                    elapsed_str = f"{time.time() - info.started_at:.0f}s"
                    tool_count = info.tool_count
                    token_in = token_out = 0

                # Status icon
                if pane and pane.status == "done":
                    s_icon = f"{_GREEN}{_ICON_DONE}{_RESET}"
                elif pane and pane.status == "error":
                    s_icon = f"{_RED}✗{_RESET}"
                else:
                    s_icon = f"{_MAGENTA}{_SPINNER[self._spinner_idx]}{_RESET}"

                # Agent header: ● Explore(description)
                display_name = self._subagent_display_names.get(name, name)
                desc_short = info.description[:50]
                lines.append(
                    f"   {s_icon} {_BOLD}{display_name}{_RESET}"
                    f"{_DIM}({desc_short}){_RESET}"
                )

                # Token + tool stats line
                stats_parts = [f"{elapsed_str}", f"{tool_count} tools"]
                if token_in or token_out:
                    tok_cached = (
                        getattr(pane, "token_cached", 0) if pane else 0
                    )
                    stats_parts.append(
                        _format_tokens_detail(token_in, token_out, tok_cached)
                    )
                lines.append(f"     {_DIM}{' · '.join(stats_parts)}{_RESET}")

                # Tool history (last 5) + current tool
                max_tool_lines = 5
                if pane and pane.tool_history:
                    history = pane.tool_history
                    hidden = max(0, len(history) - max_tool_lines)
                    visible = history[-max_tool_lines:]
                    for ti, tc in enumerate(visible):
                        prefix = "⎿ " if ti == 0 else "  "
                        tool_display = tc.summary[: width - 12]
                        if tc.is_error:
                            lines.append(
                                f"     {_DIM}{prefix}"
                            f"{_RESET}{_RED}"
                            f"{tool_display}{_RESET}"
                            )
                        else:
                            lines.append(
                                f"     {_DIM}{prefix}{tool_display}{_RESET}"
                            )
                    if hidden > 0:
                        lines.append(
                            f"     {_DIM}  +{hidden} more tool uses{_RESET}"
                        )

                # Current active tool (spinner)
                if pane and pane.current_tool:
                    spinner = _SPINNER[self._spinner_idx]
                    lines.append(
                        f"     {_BLUE}{spinner} {pane.current_tool}{_RESET}"
                    )
                elif info.current_tool:
                    spinner = _SPINNER[self._spinner_idx]
                    lines.append(
                        f"     {_BLUE}{spinner} {info.current_tool}{_RESET}"
                    )

        # ─── Current Tool (main agent, no subagents) ───
        elif self.current_tool and self.current_tool not in (
            "write_todos",
            "task",
            "orchestrate_tool",
        ):
            lines.append(
                f"   {_BLUE}{_ICON_TOOL}"
                f" {self.current_tool}{_RESET}"
                f" {_DIM}"
                f"{self.current_tool_args[:width - 15]}"
                f"{_RESET}"
            )

        # ─── Activity Log (only shown when no subagents are active) ───
        if self.activity_log and not self.active_subagents:
            lines.append(
                f"   {_DIM}── Recent {'─' * max(0, width - 14)}{_RESET}"
            )
            for entry in list(self.activity_log)[-4:]:
                if entry.startswith("+"):
                    lines.append(f"   {_GREEN}{entry[: width - 6]}{_RESET}")
                elif entry.startswith("~"):
                    lines.append(f"   {_YELLOW}{entry[: width - 6]}{_RESET}")
                else:
                    lines.append(f"   {_DIM}{entry[: width - 6]}{_RESET}")

        # ─── Footer ───
        lines.append(f"{_DIM}{'─' * width}{_RESET}")
        return lines

    def render_final_summary(self):
        with self._lock:
            self._clear_previous()
            self._last_panel_lines = 0

        # Only show summary when there's meaningful activity
        total = len(self.todos)
        agent_total = len(self.completed_subagents)
        has_tokens = (self.token_input + self.token_output) > 0
        if total == 0 and agent_total == 0 and not has_tokens:
            return

        width = shutil.get_terminal_size().columns - 2
        done = sum(1 for t in self.todos if t.status == "completed")

        # Collect total tokens: main agent + subagents
        total_in = self.token_input
        total_out = self.token_output
        total_cached = self.token_cached
        if self._pane_manager:
            for pane in self._pane_manager.get_panes():
                total_in += pane.token_input
                total_out += pane.token_output
                total_cached += getattr(pane, "token_cached", 0)

        parts = [f"Tools: {self.tool_count}"]
        if total > 0:
            parts.append(f"Plan: {done}/{total}")
        if agent_total > 0:
            parts.append(f"Subagents: {agent_total}")
        if total_in or total_out:
            parts.append(
                _format_tokens_detail(total_in, total_out, total_cached)
            )

        # Accumulate into session-level counter
        panes = self._pane_manager.get_panes() if self._pane_manager else []
        accumulate_session_tokens(
            self.token_input + sum(p.token_input for p in panes),
            self.token_output + sum(p.token_output for p in panes),
            total_cached,
        )

        summary = " · ".join(parts)
        trail = max(0, width - len(summary) - 12)
        line = (
            f"{_DIM}── {_CYAN}Done{_DIM}"
            f" ({summary})"
            f" {'─' * trail}{_RESET}"
        )
        print(line, flush=True)


# ─── Session-level token accumulator ───
# Persists across turns (StatusTracker is recreated each turn)
_session_tokens = {"input": 0, "output": 0, "cached": 0}


def get_session_tokens() -> dict:
    """Return a copy of session-level token counts.

    Returns:
        Dict with keys "input", "output", and "cached".
    """
    return _session_tokens.copy()


def accumulate_session_tokens(
    input_tokens: int, output_tokens: int, cached_tokens: int = 0
):
    """Add token counts to the session-level accumulator.

    Args:
        input_tokens: Number of input tokens to add.
        output_tokens: Number of output tokens to add.
        cached_tokens: Number of cached input tokens to add.
    """
    _session_tokens["input"] += input_tokens
    _session_tokens["output"] += output_tokens
    _session_tokens["cached"] += cached_tokens


def reset_session_tokens():
    _session_tokens["input"] = 0
    _session_tokens["output"] = 0
    _session_tokens["cached"] = 0


def _format_tokens(total: int) -> str:
    """Format token count with human-readable suffix.

    Args:
        total: Raw token count.

    Returns:
        Formatted string (e.g. "1.2k tokens", "12k tokens").
    """
    if total < 1000:
        return f"{total} tokens"
    if total < 10000:
        return f"{total / 1000:.1f}k tokens"
    return f"{total // 1000}k tokens"


def _format_tokens_short(count: int) -> str:
    """Compact token format without 'tokens' suffix.

    Args:
        count: Raw token count.

    Returns:
        Compact string (e.g. "1.2k", "200").
    """
    if count < 1000:
        return str(count)
    if count < 10000:
        return f"{count / 1000:.1f}k"
    return f"{count // 1000}k"


def _format_tokens_detail(input_tok: int, output_tok: int, cached: int) -> str:
    """Format token display with input/output breakdown.

    Uses arrows to indicate direction: up = new input tokens (uncached context
    sent to model), down = output tokens (model's response).

    Args:
        input_tok: Total input token count.
        output_tok: Total output token count.
        cached: Number of cached input tokens to subtract from input.

    Returns:
        Formatted string (e.g. "up 6.0k down 200 tokens").
    """
    effective_input = max(0, input_tok - cached)
    up = _format_tokens_short(effective_input)
    down = _format_tokens_short(output_tok)
    return f"↑ {up} ↓ {down} tokens"


def _format_tool_summary(name: str, args: dict) -> str:
    """Extract a short summary string from tool arguments for display.

    Args:
        name: Tool name (e.g. "execute", "write_file").
        args: Tool arguments dict.

    Returns:
        Short summary string (e.g. file path, command snippet).
    """
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
