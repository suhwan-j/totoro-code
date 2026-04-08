"""curses-based split-pane TUI for parallel subagent display.

Only active during orchestrate_tool execution. Enters curses mode,
renders left (dashboard) + right (subagent detail), exits when done.
"""

import curses
import re
import time
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atom.status import StatusTracker
    from atom.pane import PaneManager


# curses color pair IDs
_PAIR_DIM = 1
_PAIR_GREEN = 2
_PAIR_YELLOW = 3
_PAIR_CYAN = 4
_PAIR_RED = 5
_PAIR_BLUE = 6
_PAIR_MAGENTA = 7
_PAIR_BOLD_WHITE = 8
_PAIR_DIVIDER = 9

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


class SplitPaneTUI:
    """Split-pane TUI using curses. Left=dashboard, Right=subagent detail."""

    def __init__(self, tracker: 'StatusTracker', pane_manager: 'PaneManager'):
        self.tracker = tracker
        self.pane_manager = pane_manager
        self._stdscr = None
        self._left_win = None
        self._right_win = None
        self._div_col = 0
        self._running = False

    def run(self, stdscr):
        """Main entry called via curses.wrapper(). Sets up windows and render loop."""
        self._stdscr = stdscr
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()

        # Init color pairs with default background (-1)
        curses.init_pair(_PAIR_DIM, curses.COLOR_WHITE, -1)
        curses.init_pair(_PAIR_GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(_PAIR_YELLOW, curses.COLOR_YELLOW, -1)
        curses.init_pair(_PAIR_CYAN, curses.COLOR_CYAN, -1)
        curses.init_pair(_PAIR_RED, curses.COLOR_RED, -1)
        curses.init_pair(_PAIR_BLUE, curses.COLOR_BLUE, -1)
        curses.init_pair(_PAIR_MAGENTA, curses.COLOR_MAGENTA, -1)
        curses.init_pair(_PAIR_BOLD_WHITE, curses.COLOR_WHITE, -1)
        curses.init_pair(_PAIR_DIVIDER, curses.COLOR_WHITE, -1)

        stdscr.nodelay(True)  # non-blocking getch
        stdscr.timeout(300)

        h, w = stdscr.getmaxyx()
        self._div_col = int(w * 0.55)

        self._left_win = curses.newwin(h, self._div_col, 0, 0)
        self._right_win = curses.newwin(h, w - self._div_col - 1, 0, self._div_col + 1)

        self._running = True
        while self._running:
            try:
                h, w = stdscr.getmaxyx()
                self._render_divider(h)
                self._render_left(h)
                self._render_right(h)
                stdscr.refresh()

                # Check for completion
                if not self.pane_manager.is_active:
                    # Small delay to show final state
                    time.sleep(0.5)
                    self._render_left(h)
                    self._render_right(h)
                    stdscr.refresh()
                    time.sleep(0.5)
                    break

                # Check for key press (Ctrl+C handling)
                key = stdscr.getch()
                if key == 3:  # Ctrl+C
                    break

                time.sleep(0.3)
            except curses.error:
                pass
            except KeyboardInterrupt:
                break

    def stop(self):
        self._running = False

    def _render_divider(self, height: int):
        """Draw vertical divider line."""
        for row in range(height):
            try:
                self._stdscr.addch(row, self._div_col, curses.ACS_VLINE,
                                   curses.color_pair(_PAIR_DIM))
            except curses.error:
                pass

    def _render_left(self, height: int):
        """Render dashboard on left pane."""
        win = self._left_win
        win.erase()
        w = self._div_col - 1

        row = 0
        # Header
        row = self._waddstr(win, row, 0, "── ", _PAIR_DIM)
        self._waddstr(win, 0, 3, "◈ Atom ", _PAIR_CYAN, bold=True)
        phase = self.tracker.phase
        phase_pair = _PAIR_YELLOW if phase == "Planning" else _PAIR_GREEN if phase == "Executing" else _PAIR_DIM
        self._waddstr(win, 0, 10, phase, phase_pair, bold=True)

        # Counters
        done_count = sum(1 for t in self.tracker.todos if t.status == "completed")
        total_count = len(self.tracker.todos)
        agent_count = len(self.tracker.active_subagents)
        counters = []
        if total_count > 0:
            counters.append(f"Plan: {done_count}/{total_count}")
        counters.append(f"Tools: {self.tracker.tool_count}")
        if agent_count > 0:
            counters.append(f"Agents: {agent_count}")
        counter_text = f"  {' · '.join(counters)}"
        self._waddstr(win, 0, 10 + len(phase) + 1, counter_text, _PAIR_DIM)
        row = 1

        # Separator
        self._waddstr(win, row, 0, "─" * w, _PAIR_DIM)
        row += 1

        # Plan progress
        if self.tracker.todos and total_count > 0:
            ratio = done_count / total_count
            bar_width = min(20, w - 10)
            filled = int(ratio * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            pct = int(ratio * 100)
            self._waddstr(win, row, 3, bar, _PAIR_GREEN)
            self._waddstr(win, row, 3 + bar_width + 1, f"{pct}%", _PAIR_DIM)
            row += 1

            for todo in self.tracker.todos[:8]:
                if row >= height - 2:
                    break
                if todo.status == "completed":
                    self._waddstr(win, row, 3, "✓ ", _PAIR_GREEN)
                    self._waddstr(win, row, 5, todo.content[:w - 8], _PAIR_DIM)
                elif todo.status == "in_progress":
                    self._waddstr(win, row, 3, "▸ ", _PAIR_YELLOW)
                    self._waddstr(win, row, 5, todo.content[:w - 8], _PAIR_BOLD_WHITE)
                else:
                    self._waddstr(win, row, 3, "○ ", _PAIR_DIM)
                    self._waddstr(win, row, 5, todo.content[:w - 8], _PAIR_DIM)
                row += 1

        # Subagent tree
        if self.tracker.active_subagents:
            pane_data = {}
            if self.pane_manager:
                for pane in self.pane_manager.get_panes():
                    pane_data[pane.label] = pane

            agents = list(self.tracker.active_subagents.items())
            for idx, (name, info) in enumerate(agents):
                if row >= height - 1:
                    break
                is_last = idx == len(agents) - 1
                pane = pane_data.get(name)
                elapsed = f"{time.time() - info.started_at:.0f}s"
                tool_count = pane.tool_count if pane else info.tool_count

                connector = "└── " if is_last else "├── "
                child_pre = "    " if is_last else "│   "

                # Status icon
                if pane and pane.status == "done":
                    self._waddstr(win, row, 3, connector, _PAIR_DIM)
                    self._waddstr(win, row, 3 + len(connector), "✓ ", _PAIR_GREEN)
                    self._waddstr(win, row, 3 + len(connector) + 2, name, _PAIR_GREEN, bold=True)
                else:
                    self._waddstr(win, row, 3, connector, _PAIR_DIM)
                    self._waddstr(win, row, 3 + len(connector), "◈ ", _PAIR_MAGENTA)
                    self._waddstr(win, row, 3 + len(connector) + 2, name, _PAIR_BOLD_WHITE, bold=True)

                stats_text = f"  {elapsed} · {tool_count} tools"
                self._waddstr(win, row, 3 + len(connector) + 2 + len(name), stats_text, _PAIR_DIM)
                row += 1

                # Current tool or recent line
                if pane and pane.current_tool and row < height - 1:
                    self._waddstr(win, row, 3, child_pre, _PAIR_DIM)
                    self._waddstr(win, row, 3 + len(child_pre), f"⚡ {pane.current_tool}", _PAIR_BLUE)
                    row += 1

        win.noutrefresh()

    def _render_right(self, height: int):
        """Render subagent detail panels on right pane."""
        win = self._right_win
        win.erase()
        _, w = win.getmaxyx()

        panes = self.pane_manager.get_panes()
        if not panes:
            win.noutrefresh()
            return

        row = 0
        pane_height = max(4, (height - 1) // max(len(panes), 1))

        for pane in panes:
            if row >= height - 1:
                break

            # Header
            elapsed = pane.elapsed
            if pane.status == "done":
                self._waddstr(win, row, 1, "✓ ", _PAIR_GREEN)
                self._waddstr(win, row, 3, pane.label, _PAIR_GREEN, bold=True)
            elif pane.status == "error":
                self._waddstr(win, row, 1, "✗ ", _PAIR_RED)
                self._waddstr(win, row, 3, pane.label, _PAIR_RED, bold=True)
            else:
                self._waddstr(win, row, 1, "◈ ", _PAIR_CYAN)
                self._waddstr(win, row, 3, pane.label, _PAIR_BOLD_WHITE, bold=True)

            stats = f"  {elapsed} · {pane.tool_count} tools"
            self._waddstr(win, row, 3 + len(pane.label), stats, _PAIR_DIM)
            row += 1

            # Output lines
            visible_lines = pane.recent_lines[-(pane_height - 2):]
            for line in visible_lines:
                if row >= height - 1:
                    break
                clean = _strip_ansi(line)[:w - 2]

                # Color based on content
                if clean.startswith("●") or clean.startswith("▸"):
                    self._waddstr(win, row, 2, clean, _PAIR_CYAN)
                elif clean.startswith("+") or clean.startswith("  ⎿"):
                    self._waddstr(win, row, 2, clean, _PAIR_GREEN)
                elif clean.startswith("-"):
                    self._waddstr(win, row, 2, clean, _PAIR_RED)
                elif "error" in clean.lower()[:30]:
                    self._waddstr(win, row, 2, clean, _PAIR_RED)
                else:
                    self._waddstr(win, row, 2, clean, _PAIR_DIM)
                row += 1

            # Separator
            if row < height - 1:
                self._waddstr(win, row, 0, "─" * (w - 1), _PAIR_DIM)
                row += 1

        win.noutrefresh()

    @staticmethod
    def _waddstr(win, row: int, col: int, text: str, pair: int, bold: bool = False):
        """Safe addstr with color."""
        try:
            h, w = win.getmaxyx()
            if row >= h or col >= w:
                return
            text = text[:w - col - 1]  # prevent overflow
            attr = curses.color_pair(pair)
            if bold:
                attr |= curses.A_BOLD
            win.addstr(row, col, text, attr)
        except curses.error:
            pass
