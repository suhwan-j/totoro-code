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
    from totoro.status import StatusTracker
    from totoro.pane import PaneManager


# curses color pair IDs (mapped to palette)
_PAIR_DIM = 1        # Ivory dark  #4A3C28 → divider / dim
_PAIR_GREEN = 2      # Amber light #FAC775 → done / progress
_PAIR_YELLOW = 3     # Amber       #EF9F27 → active / accent
_PAIR_CYAN = 4       # Blue        #378ADD → prompt / heading
_PAIR_RED = 5        # Copper      #C85A38 → error
_PAIR_BLUE = 6       # Blue light  #85B7EB → body / tools
_PAIR_MAGENTA = 7    # Ivory       #C4A876 → secondary
_PAIR_BOLD_WHITE = 8 # Ivory light #EDE0C4 → body text
_PAIR_DIVIDER = 9    # Ivory dark  #4A3C28

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


def _wcwidth(ch: str) -> int:
    """Return display width of a character (2 for CJK, 1 for others)."""
    cp = ord(ch)
    # CJK Unified Ideographs, Hangul Syllables, fullwidth forms, etc.
    if (0x1100 <= cp <= 0x115F or   # Hangul Jamo
        0x2E80 <= cp <= 0x9FFF or   # CJK radicals, unified ideographs
        0xAC00 <= cp <= 0xD7AF or   # Hangul Syllables
        0xF900 <= cp <= 0xFAFF or   # CJK Compatibility Ideographs
        0xFE10 <= cp <= 0xFE6F or   # CJK forms
        0xFF01 <= cp <= 0xFF60 or   # Fullwidth forms
        0xFFE0 <= cp <= 0xFFE6 or   # Fullwidth signs
        0x20000 <= cp <= 0x2FA1F):  # CJK Extension B+
        return 2
    return 1


def _wcswidth(text: str) -> int:
    """Return total display width of a string."""
    return sum(_wcwidth(ch) for ch in text)


def _truncate_to_width(text: str, max_width: int) -> str:
    """Truncate string to fit within max_width display columns."""
    w = 0
    for i, ch in enumerate(text):
        cw = _wcwidth(ch)
        if w + cw > max_width:
            return text[:i]
        w += cw
    return text


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

        # Init custom colors from palette (curses uses 0-1000 range)
        # Only if terminal supports color changes; fall back to defaults
        _can_change = curses.can_change_color()
        if _can_change:
            def _rgb(r, g, b):
                return int(r / 255 * 1000), int(g / 255 * 1000), int(b / 255 * 1000)
            curses.init_color(16, *_rgb(96, 80, 58))      # #60503A ivory dark
            curses.init_color(17, *_rgb(255, 216, 153))   # #FFD899 amber light
            curses.init_color(18, *_rgb(245, 178, 64))    # #F5B240 amber
            curses.init_color(19, *_rgb(86, 160, 240))    # #56A0F0 blue
            curses.init_color(20, *_rgb(224, 104, 64))    # #E06840 copper
            curses.init_color(21, *_rgb(168, 207, 250))   # #A8CFFA blue light
            curses.init_color(22, *_rgb(212, 186, 142))   # #D4BA8E ivory
            curses.init_color(23, *_rgb(245, 236, 216))   # #F5ECD8 ivory light
            curses.init_pair(_PAIR_DIM, 16, -1)
            curses.init_pair(_PAIR_GREEN, 17, -1)
            curses.init_pair(_PAIR_YELLOW, 18, -1)
            curses.init_pair(_PAIR_CYAN, 19, -1)
            curses.init_pair(_PAIR_RED, 20, -1)
            curses.init_pair(_PAIR_BLUE, 21, -1)
            curses.init_pair(_PAIR_MAGENTA, 22, -1)
            curses.init_pair(_PAIR_BOLD_WHITE, 23, -1)
            curses.init_pair(_PAIR_DIVIDER, 16, -1)
        else:
            # Fallback: closest standard colors
            curses.init_pair(_PAIR_DIM, curses.COLOR_YELLOW, -1)
            curses.init_pair(_PAIR_GREEN, curses.COLOR_YELLOW, -1)
            curses.init_pair(_PAIR_YELLOW, curses.COLOR_YELLOW, -1)
            curses.init_pair(_PAIR_CYAN, curses.COLOR_CYAN, -1)
            curses.init_pair(_PAIR_RED, curses.COLOR_RED, -1)
            curses.init_pair(_PAIR_BLUE, curses.COLOR_CYAN, -1)
            curses.init_pair(_PAIR_MAGENTA, curses.COLOR_WHITE, -1)
            curses.init_pair(_PAIR_BOLD_WHITE, curses.COLOR_WHITE, -1)
            curses.init_pair(_PAIR_DIVIDER, curses.COLOR_YELLOW, -1)

        stdscr.nodelay(True)  # non-blocking getch
        stdscr.timeout(300)

        h, w = stdscr.getmaxyx()
        self._div_col = int(w * 0.5)

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
        """Draw vertical divider as subtle dotted line."""
        for row in range(height):
            try:
                ch = "┊" if row % 2 == 0 else " "
                self._stdscr.addstr(row, self._div_col, ch,
                                    curses.color_pair(_PAIR_DIM) | curses.A_DIM)
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
        self._waddstr(win, 0, 3, "◈ Totoro ", _PAIR_CYAN, bold=True)
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
        self._waddstr(win, row, 0, "┈" * w, _PAIR_DIM)
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
                content = _truncate_to_width(todo.content, w - 8)
                if todo.status == "completed":
                    self._waddstr(win, row, 3, "✓ ", _PAIR_GREEN)
                    self._waddstr(win, row, 5, content, _PAIR_DIM)
                elif todo.status == "in_progress":
                    self._waddstr(win, row, 3, "▸ ", _PAIR_YELLOW)
                    self._waddstr(win, row, 5, content, _PAIR_BOLD_WHITE)
                else:
                    self._waddstr(win, row, 3, "○ ", _PAIR_DIM)
                    self._waddstr(win, row, 5, content, _PAIR_DIM)
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

                # Status icon + PID
                pid_str = f"({pane.pid})" if pane and pane.pid else ""
                name_with_pid = f"{name} {pid_str}" if pid_str else name

                if pane and pane.status == "done":
                    self._waddstr(win, row, 3, connector, _PAIR_DIM)
                    self._waddstr(win, row, 3 + len(connector), "✓ ", _PAIR_GREEN)
                    self._waddstr(win, row, 3 + len(connector) + 2, name, _PAIR_GREEN, bold=True)
                    if pid_str:
                        self._waddstr(win, row, 3 + len(connector) + 2 + len(name) + 1, pid_str, _PAIR_DIM)
                else:
                    self._waddstr(win, row, 3, connector, _PAIR_DIM)
                    self._waddstr(win, row, 3 + len(connector), "◈ ", _PAIR_MAGENTA)
                    self._waddstr(win, row, 3 + len(connector) + 2, name, _PAIR_BOLD_WHITE, bold=True)
                    if pid_str:
                        self._waddstr(win, row, 3 + len(connector) + 2 + len(name) + 1, pid_str, _PAIR_DIM)

                stats_text = f"  {elapsed} · {tool_count} tools"
                self._waddstr(win, row, 3 + len(connector) + 2 + len(name_with_pid), stats_text, _PAIR_DIM)
                row += 1

                # Current tool or recent line
                if pane and pane.current_tool and row < height - 1:
                    self._waddstr(win, row, 3, child_pre, _PAIR_DIM)
                    self._waddstr(win, row, 3 + len(child_pre), f"⚡ {pane.current_tool}", _PAIR_BLUE)
                    row += 1

        win.noutrefresh()

    def _render_right(self, height: int):
        """Render subagent detail panels on right pane, each getting 1/N of the height."""
        win = self._right_win
        win.erase()
        _, w = win.getmaxyx()

        panes = self.pane_manager.get_panes()
        if not panes:
            win.noutrefresh()
            return

        n = len(panes)
        pane_height = max(3, height // n)  # each pane gets 1/N of total height

        for idx, pane in enumerate(panes):
            # Fixed region for this pane: [start_row, end_row)
            start_row = idx * pane_height
            end_row = (idx + 1) * pane_height if idx < n - 1 else height  # last pane gets remainder
            if start_row >= height - 1:
                break

            row = start_row

            # Header
            elapsed = pane.elapsed
            pid_str = f"({pane.pid})" if pane.pid else ""
            label_display = f"{pane.label} {pid_str}" if pid_str else pane.label

            if pane.status == "done":
                self._waddstr(win, row, 1, "✓ ", _PAIR_GREEN)
                self._waddstr(win, row, 3, pane.label, _PAIR_GREEN, bold=True)
                if pid_str:
                    self._waddstr(win, row, 3 + len(pane.label) + 1, pid_str, _PAIR_DIM)
            elif pane.status == "error":
                self._waddstr(win, row, 1, "✗ ", _PAIR_RED)
                self._waddstr(win, row, 3, pane.label, _PAIR_RED, bold=True)
                if pid_str:
                    self._waddstr(win, row, 3 + len(pane.label) + 1, pid_str, _PAIR_DIM)
            else:
                self._waddstr(win, row, 1, "◈ ", _PAIR_CYAN)
                self._waddstr(win, row, 3, pane.label, _PAIR_BOLD_WHITE, bold=True)
                if pid_str:
                    self._waddstr(win, row, 3 + len(pane.label) + 1, pid_str, _PAIR_DIM)

            stats = f"  {elapsed} · {pane.tool_count} tools"
            self._waddstr(win, row, 3 + len(label_display), stats, _PAIR_DIM)
            row += 1

            # Current tool indicator
            if pane.current_tool and row < end_row - 1:
                self._waddstr(win, row, 2, f"⚡ {pane.current_tool}"[:w - 3], _PAIR_YELLOW)
                row += 1

            # Output lines — fill remaining space in this pane's region
            content_rows = end_row - row - 1  # -1 for separator
            visible_lines = pane.recent_lines[-max(1, content_rows):]
            for line in visible_lines:
                if row >= end_row - 1:
                    break
                clean = _truncate_to_width(_strip_ansi(line), w - 3)

                # Color based on content
                if clean.startswith("●") or clean.startswith("▸"):
                    self._waddstr(win, row, 2, clean, _PAIR_CYAN)
                elif clean.startswith("  ⎿"):
                    self._waddstr(win, row, 2, clean, _PAIR_DIM)
                elif clean.startswith("+"):
                    self._waddstr(win, row, 2, clean, _PAIR_GREEN)
                elif clean.startswith("✗") or "error" in clean.lower()[:30]:
                    self._waddstr(win, row, 2, clean, _PAIR_RED)
                else:
                    self._waddstr(win, row, 2, clean, _PAIR_DIM)
                row += 1

            # Separator between panes (not after last)
            if idx < n - 1 and end_row - 1 < height:
                self._waddstr(win, end_row - 1, 0, "┈" * (w - 1), _PAIR_DIM)

        win.noutrefresh()

    @staticmethod
    def _waddstr(win, row: int, col: int, text: str, pair: int, bold: bool = False):
        """Safe addstr with color, CJK-aware width truncation."""
        try:
            h, w = win.getmaxyx()
            if row >= h or col >= w:
                return
            text = _truncate_to_width(text, w - col - 1)
            attr = curses.color_pair(pair)
            if bold:
                attr |= curses.A_BOLD
            win.addstr(row, col, text, attr)
        except curses.error:
            pass
