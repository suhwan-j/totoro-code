"""curses-based split-pane TUI for parallel subagent display.

Only active during orchestrate_tool execution. Enters curses mode,
renders left (dashboard) + right (subagent detail), exits when done.
"""

import curses
import os
import queue
import re
import time
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from totoro.status import StatusTracker
    from totoro.pane import PaneManager


# curses color pair IDs (mapped to palette)
_PAIR_DIM = 1  # Ivory dark  #4A3C28 → divider / dim
_PAIR_GREEN = 2  # Amber light #FAC775 → done / progress
_PAIR_YELLOW = 3  # Amber       #EF9F27 → active / accent
_PAIR_CYAN = 4  # Blue        #378ADD → prompt / heading
_PAIR_RED = 5  # Copper      #C85A38 → error
_PAIR_BLUE = 6  # Blue light  #85B7EB → body / tools
_PAIR_MAGENTA = 7  # Ivory       #C4A876 → secondary
_PAIR_BOLD_WHITE = 8  # Ivory light #EDE0C4 → body text
_PAIR_DIVIDER = 9  # Ivory dark  #4A3C28

_ANSI_RE = re.compile(r"(?:\033|\x1b)\[[0-9;]*[A-Za-z]|\^\[\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _wcwidth(ch: str) -> int:
    """Return display width of a character (2 for CJK, 1 for others).

    Args:
        ch: Single character to measure.

    Returns:
        Display width: 2 for CJK/fullwidth characters, 1 otherwise.
    """
    cp = ord(ch)
    # CJK Unified Ideographs, Hangul Syllables, fullwidth forms, etc.
    if (
        0x1100 <= cp <= 0x115F  # Hangul Jamo
        or 0x2E80 <= cp <= 0x9FFF  # CJK radicals, unified ideographs
        or 0xAC00 <= cp <= 0xD7AF  # Hangul Syllables
        or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
        or 0xFE10 <= cp <= 0xFE6F  # CJK forms
        or 0xFF01 <= cp <= 0xFF60  # Fullwidth forms
        or 0xFFE0 <= cp <= 0xFFE6  # Fullwidth signs
        or 0x20000 <= cp <= 0x2FA1F
    ):  # CJK Extension B+
        return 2
    return 1


def _wcswidth(text: str) -> int:
    """Return total display width of a string.

    Args:
        text: String to measure.

    Returns:
        Total display width accounting for CJK/fullwidth characters.
    """
    return sum(_wcwidth(ch) for ch in text)


def _truncate_to_width(text: str, max_width: int) -> str:
    """Truncate string to fit within max_width display columns.

    Args:
        text: String to truncate.
        max_width: Maximum display width in columns.

    Returns:
        Truncated string that fits within max_width.
    """
    if max_width <= 0:
        return ""
    w = 0
    for i, ch in enumerate(text):
        cw = _wcwidth(ch)
        if w + cw > max_width:
            return text[:i]
        w += cw
    return text


def _wrap_text(text: str, max_width: int, max_lines: int = 3) -> list[str]:
    """Wrap text into multiple lines respecting CJK display width.

    Args:
        text: Text to wrap.
        max_width: Maximum display width per line.
        max_lines: Maximum number of wrapped lines.

    Returns:
        List of wrapped line strings.
    """
    if max_width <= 0:
        return [text[:20]]
    lines = []
    remaining = text
    while remaining and len(lines) < max_lines:
        if _wcswidth(remaining) <= max_width:
            lines.append(remaining)
            break
        # Find the split point
        w = 0
        split = 0
        for i, ch in enumerate(remaining):
            cw = _wcwidth(ch)
            if w + cw > max_width:
                split = i
                break
            w += cw
        else:
            split = len(remaining)
        if split == 0:
            split = 1
        lines.append(remaining[:split])
        remaining = remaining[split:]
    if remaining and len(lines) == max_lines:
        # Indicate truncation on last line
        last = lines[-1]
        if _wcswidth(last) > max_width - 1:
            last = _truncate_to_width(last, max_width - 1)
        lines[-1] = last + "…"
    return lines if lines else [text[:1]]


def _short_path(path: str) -> str:
    """Extract short display path from full file path."""
    if not path:
        return "?"
    import os

    return os.path.basename(path)


def _extract_filename_from_summary(summary: str) -> str:
    """Extract filename from tool summary like 'write_file(foo.py)'."""
    import re

    m = re.search(r"\(([^)]+)\)", summary)
    return m.group(1) if m else ""


class SplitPaneTUI:
    """Split-pane TUI using curses. Left=dashboard, Right=subagent detail."""

    def __init__(
        self,
        tracker: "StatusTracker",
        pane_manager: "PaneManager",
        hitl_pending: "queue.Queue | None" = None,
        response_queues: "dict | None" = None,
    ):
        self.tracker = tracker
        self.pane_manager = pane_manager
        self.hitl_pending = hitl_pending
        self.response_queues = response_queues or {}
        self._global_auto_approve = False
        self._stdscr = None
        self._left_win = None
        self._right_win = None
        self._div_col = 0
        self._running = False

    def run(self, stdscr):
        """Main entry called via curses.wrapper().

        Sets up windows and runs the render loop until all panes complete
        or the user presses Ctrl+C.

        Args:
            stdscr: The curses standard screen object.
        """
        self._stdscr = stdscr
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()

        # Init custom colors from palette (curses uses 0-1000 range)
        # Only if terminal supports color changes; fall back to defaults
        _can_change = curses.can_change_color()
        if _can_change:

            def _rgb(r, g, b):
                return (
                    int(r / 255 * 1000),
                    int(g / 255 * 1000),
                    int(b / 255 * 1000),
                )

            curses.init_color(16, *_rgb(96, 80, 58))  # #60503A ivory dark
            curses.init_color(17, *_rgb(255, 216, 153))  # #FFD899 amber light
            curses.init_color(18, *_rgb(245, 178, 64))  # #F5B240 amber
            curses.init_color(19, *_rgb(86, 160, 240))  # #56A0F0 blue
            curses.init_color(20, *_rgb(224, 104, 64))  # #E06840 copper
            curses.init_color(21, *_rgb(168, 207, 250))  # #A8CFFA blue light
            curses.init_color(22, *_rgb(212, 186, 142))  # #D4BA8E ivory
            curses.init_color(23, *_rgb(245, 236, 216))  # #F5ECD8 ivory light
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
        self._right_win = curses.newwin(
            h, w - self._div_col - 1, 0, self._div_col + 1
        )

        self._running = True
        prev_h, prev_w = h, w
        while self._running:
            try:
                h, w = stdscr.getmaxyx()

                # Recreate windows on terminal resize
                if h != prev_h or w != prev_w:
                    prev_h, prev_w = h, w
                    self._div_col = int(w * 0.5)
                    try:
                        self._left_win = curses.newwin(
                            h, max(1, self._div_col), 0, 0
                        )
                        self._right_win = curses.newwin(
                            h,
                            max(1, w - self._div_col - 1),
                            0,
                            self._div_col + 1,
                        )
                    except curses.error:
                        pass
                    stdscr.clear()

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

                # Check for pending HITL requests
                if (
                    self.hitl_pending is not None
                    and not self._global_auto_approve
                ):
                    try:
                        hitl_event = self.hitl_pending.get_nowait()
                        self._handle_hitl_batch(stdscr, hitl_event)
                        continue
                    except queue.Empty:
                        pass
                # Auto-approve mode: drain silently
                if self.hitl_pending is not None and self._global_auto_approve:
                    while True:
                        try:
                            ev = self.hitl_pending.get_nowait()
                            self._approve_event(ev)
                        except queue.Empty:
                            break

                # Check for key press (Ctrl+C handling)
                key = stdscr.getch()
                if key == 3:  # Ctrl+C
                    break
                if key == curses.KEY_RESIZE:
                    continue  # Immediately re-render on resize

                time.sleep(0.3)
            except curses.error:
                pass
            except KeyboardInterrupt:
                break

    def stop(self):
        self._running = False

    def _send_hitl_response_event(self, label: str):
        """Update pane status back to running after HITL response."""
        from totoro.pane import SubagentEvent

        if self.pane_manager:
            self.pane_manager.update_subagent(
                SubagentEvent(
                    label=label,
                    event_type="hitl_response",
                    data={},
                )
            )

    def _exit_curses(self, stdscr):
        """Cleanly exit curses for terminal input."""
        curses.endwin()
        os.system("stty sane 2>/dev/null")
        import sys

        sys.stdout.write("\033[?25h")  # Show cursor
        sys.stdout.flush()

    def _enter_curses(self, stdscr):
        """Re-enter curses after terminal input."""
        stdscr.refresh()
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(300)
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        self._div_col = int(w * 0.5)
        try:
            self._left_win = curses.newwin(h, max(1, self._div_col), 0, 0)
            self._right_win = curses.newwin(
                h, max(1, w - self._div_col - 1), 0, self._div_col + 1
            )
        except curses.error:
            pass

    def _approve_event(self, event):
        """Send approve decision for a single HITL event."""
        rq = self.response_queues.get(event.label)
        if rq:
            try:
                rq.put({"decisions": [{"type": "approve"}]}, timeout=1)
            except Exception:
                pass
        self._send_hitl_response_event(event.label)

    def _drain_and_approve_pending(self):
        """Approve all pending HITL requests in the queue."""
        while True:
            try:
                ev = self.hitl_pending.get_nowait()
                self._approve_event(ev)
            except queue.Empty:
                break

    def _handle_hitl_batch(self, stdscr, first_event):
        """Exit curses, process HITL events, re-enter.

        Avoids repeated curses exit/enter cycles that corrupt terminal state.
        """
        self._exit_curses(stdscr)

        # Process first event + any that arrive while we're prompting
        pending = [first_event]
        while pending:
            event = pending.pop(0)
            label = event.label
            tool_requests = event.data.get("tool_requests", [])
            decisions = []

            for tr in tool_requests:
                tool_name = tr.get("name", "?")
                tool_args = tr.get("args", {})

                print(
                    f"\n  \033[33m[APPROVAL REQUIRED]"
                    f"\033[0m \033[1m{tool_name}\033[0m"
                )
                if isinstance(tool_args, dict):
                    for k, v in tool_args.items():
                        v_str = str(v)
                        if len(v_str) > 300:
                            v_str = v_str[:300] + "..."
                        print(f"    {k}: {v_str}")
                print(
                    "  \033[1m(a)\033[0mpprove / "
                    "\033[1m(A)\033[0mpprove all / "
                    "\033[1m(r)\033[0meject / "
                    "\033[1m(e)\033[0mdit ?"
                )

                try:
                    choice = input("  > ").strip()
                except (EOFError, KeyboardInterrupt):
                    decisions.append(
                        {"type": "reject", "message": "Aborted by user"}
                    )
                    break

                if choice in ("A", "approve all", "aa"):
                    decisions.append({"type": "approve_all"})
                    self._global_auto_approve = True
                    # Set module-level flag too
                    import totoro.orchestrator as _orch

                    _orch._runtime_auto_approve = True
                    print(
                        "  \033[33m\u26a1 Auto-approve"
                        " enabled for all"
                        " subagents\033[0m"
                    )
                    break
                elif choice.lower() in ("r", "reject", "n", "no"):
                    decisions.append(
                        {
                            "type": "reject",
                            "message": f"User rejected {tool_name}",
                        }
                    )
                elif choice.lower() in ("e", "edit"):
                    try:
                        edit_instruction = input("  How to change? > ").strip()
                    except (EOFError, KeyboardInterrupt):
                        decisions.append({"type": "approve"})
                        continue
                    if not edit_instruction or not isinstance(tool_args, dict):
                        decisions.append({"type": "approve"})
                    else:
                        edited_args = dict(tool_args)
                        if (
                            "=" in edit_instruction
                            and " " not in edit_instruction.split("=")[0]
                        ):
                            key, val = edit_instruction.split("=", 1)
                            edited_args[key.strip()] = val.strip()
                        decisions.append(
                            {
                                "type": "edit",
                                "edited_action": {
                                    "name": tool_name,
                                    "args": edited_args,
                                },
                            }
                        )
                else:
                    decisions.append({"type": "approve"})

            # Send response to child
            rq = self.response_queues.get(label)
            if rq:
                try:
                    rq.put({"decisions": decisions}, timeout=1)
                except Exception:
                    pass
            self._send_hitl_response_event(label)

            # If approve-all, drain and approve everything remaining
            if self._global_auto_approve:
                self._drain_and_approve_pending()
                break

            # Check for more events that arrived while we were prompting
            # Brief wait to batch events arriving close together
            try:
                next_ev = self.hitl_pending.get(timeout=0.3)
                pending.append(next_ev)
            except queue.Empty:
                pass

        self._enter_curses(stdscr)

    def _render_divider(self, height: int):
        """Draw vertical divider as subtle dotted line.

        Args:
            height: Terminal height in rows.
        """
        for row in range(height):
            try:
                ch = "┊" if row % 2 == 0 else " "
                self._stdscr.addstr(
                    row,
                    self._div_col,
                    ch,
                    curses.color_pair(_PAIR_DIM) | curses.A_DIM,
                )
            except curses.error:
                pass

    def _render_left(self, height: int):
        """Render dashboard on left pane.

        Args:
            height: Terminal height in rows.
        """
        win = self._left_win
        win.erase()
        w = self._div_col - 1

        row = 0
        # Header
        prefix = "── "
        title = "◈ Totoro "
        self._waddstr(win, row, 0, prefix, _PAIR_DIM)
        title_col = _wcswidth(prefix)
        self._waddstr(win, 0, title_col, title, _PAIR_CYAN, bold=True)
        phase = self.tracker.phase
        phase_pair = (
            _PAIR_YELLOW
            if phase == "Planning"
            else _PAIR_GREEN
            if phase == "Executing"
            else _PAIR_DIM
        )
        phase_col = title_col + _wcswidth(title)
        self._waddstr(win, 0, phase_col, phase, phase_pair, bold=True)

        # Counters
        done_count = sum(
            1 for t in self.tracker.todos if t.status == "completed"
        )
        total_count = len(self.tracker.todos)
        agent_count = len(self.tracker.active_subagents)
        counters = []
        if total_count > 0:
            counters.append(f"Plan: {done_count}/{total_count}")
        counters.append(f"Tools: {self.tracker.tool_count}")
        if agent_count > 0:
            counters.append(f"Agents: {agent_count}")
        counter_text = f"  {' · '.join(counters)}"
        self._waddstr(
            win, 0, phase_col + len(phase) + 1, counter_text, _PAIR_DIM
        )
        row = 1

        # Separator
        self._waddstr(win, row, 0, "┈" * w, _PAIR_DIM)
        row += 1

        # Plan progress
        if self.tracker.todos and total_count > 0:
            ratio = done_count / total_count
            bar_width = max(1, min(20, w - 10))
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
                if pane:
                    elapsed = pane.elapsed
                    tool_count = pane.tool_count
                else:
                    elapsed = f"{time.time() - info.started_at:.0f}s"
                    tool_count = info.tool_count

                connector = "└── " if is_last else "├── "
                child_pre = "    " if is_last else "│   "

                # Status icon + PID
                pid_str = f"({pane.pid})" if pane and pane.pid else ""
                name_with_pid = f"{name} {pid_str}" if pid_str else name

                if pane and pane.status == "done":
                    self._waddstr(win, row, 3, connector, _PAIR_DIM)
                    self._waddstr(
                        win, row, 3 + len(connector), "✓ ", _PAIR_GREEN
                    )
                    self._waddstr(
                        win,
                        row,
                        3 + len(connector) + 2,
                        name,
                        _PAIR_GREEN,
                        bold=True,
                    )
                    if pid_str:
                        self._waddstr(
                            win,
                            row,
                            3 + len(connector) + 2 + len(name) + 1,
                            pid_str,
                            _PAIR_DIM,
                        )
                elif pane and pane.status == "waiting_approval":
                    self._waddstr(win, row, 3, connector, _PAIR_DIM)
                    self._waddstr(
                        win, row, 3 + len(connector), "⏸ ", _PAIR_YELLOW
                    )
                    self._waddstr(
                        win,
                        row,
                        3 + len(connector) + 2,
                        name,
                        _PAIR_YELLOW,
                        bold=True,
                    )
                    if pid_str:
                        self._waddstr(
                            win,
                            row,
                            3 + len(connector) + 2 + len(name) + 1,
                            pid_str,
                            _PAIR_DIM,
                        )
                else:
                    self._waddstr(win, row, 3, connector, _PAIR_DIM)
                    self._waddstr(
                        win, row, 3 + len(connector), "◈ ", _PAIR_MAGENTA
                    )
                    self._waddstr(
                        win,
                        row,
                        3 + len(connector) + 2,
                        name,
                        _PAIR_BOLD_WHITE,
                        bold=True,
                    )
                    if pid_str:
                        self._waddstr(
                            win,
                            row,
                            3 + len(connector) + 2 + len(name) + 1,
                            pid_str,
                            _PAIR_DIM,
                        )

                token_total = 0
                if pane:
                    token_total = pane.token_input + pane.token_output
                stats_text = f"  {elapsed} · {tool_count} tools"
                if token_total > 0:
                    if token_total < 1000:
                        stats_text += f" · {token_total} tok"
                    else:
                        stats_text += f" · {token_total // 1000}k tok"
                self._waddstr(
                    win,
                    row,
                    3 + len(connector) + 2 + len(name_with_pid),
                    stats_text,
                    _PAIR_DIM,
                )
                row += 1

                # Task goal/description (max 2 lines)
                if pane and pane.description and row < height - 1:
                    indent = 3 + len(child_pre)
                    prefix = "▸ "
                    # Leave 2-char margin to avoid touching divider
                    max_w = w - indent - _wcswidth(prefix) - 2
                    desc_lines = _wrap_text(
                        pane.description, max_w, max_lines=2
                    )
                    for li, dline in enumerate(desc_lines):
                        if row >= height - 1:
                            break
                        self._waddstr(win, row, 3, child_pre, _PAIR_DIM)
                        pfx = prefix if li == 0 else "  "
                        self._waddstr(
                            win, row, indent, f"{pfx}{dline}", _PAIR_MAGENTA
                        )
                        row += 1

                # Current tool
                if pane and pane.current_tool and row < height - 1:
                    self._waddstr(win, row, 3, child_pre, _PAIR_DIM)
                    self._waddstr(
                        win,
                        row,
                        3 + len(child_pre),
                        f"⚡ {pane.current_tool}",
                        _PAIR_BLUE,
                    )
                    row += 1

        win.noutrefresh()

    def _render_right(self, height: int):
        """Render subagent detail panels on right pane.

        Completed/error panes collapse to a single summary line,
        giving maximum space to still-running panes.

        Args:
            height: Terminal height in rows.
        """
        win = self._right_win
        win.erase()
        _, w = win.getmaxyx()

        panes = self.pane_manager.get_panes()
        if not panes:
            win.noutrefresh()
            return

        # Only show running panes — completed ones disappear from right panel
        # (left panel tree still shows them with ✓)
        running_panes = [
            p for p in panes if p.status in ("running", "waiting_approval")
        ]
        n_running = len(running_panes)

        if n_running == 0:
            # All done — show brief summary
            row = 0
            for pane in panes:
                if row >= height - 1:
                    break
                icon = "✓ " if pane.status == "done" else "✗ "
                pair = _PAIR_GREEN if pane.status == "done" else _PAIR_RED
                self._waddstr(win, row, 1, icon, pair)
                self._waddstr(win, row, 3, pane.label, pair, bold=True)
                stats = f"  {pane.elapsed} · {pane.tool_count} tools"
                self._waddstr(win, row, 3 + len(pane.label), stats, _PAIR_DIM)
                row += 1
            win.noutrefresh()
            return

        row = 0

        # ── Running panes only (full detail, split entire height) ──
        if n_running > 0:
            pane_height = max(3, height // n_running)

            for idx, pane in enumerate(running_panes):
                start_row = row
                end_row = row + pane_height if idx < n_running - 1 else height
                if start_row >= height - 1:
                    break

                # Header
                elapsed = pane.elapsed
                pid_str = f"({pane.pid})" if pane.pid else ""
                label_display = (
                    f"{pane.label} {pid_str}" if pid_str else pane.label
                )

                self._waddstr(win, row, 1, "◈ ", _PAIR_CYAN)
                self._waddstr(
                    win, row, 3, pane.label, _PAIR_BOLD_WHITE, bold=True
                )
                if pid_str:
                    self._waddstr(
                        win, row, 3 + len(pane.label) + 1, pid_str, _PAIR_DIM
                    )

                token_total = pane.token_input + pane.token_output
                stats = f"  {elapsed} · {pane.tool_count} tools"
                if token_total > 0:
                    if token_total < 1000:
                        stats += f" · {token_total} tok"
                    else:
                        stats += f" · {token_total // 1000}k tok"
                self._waddstr(
                    win, row, 3 + len(label_display), stats, _PAIR_DIM
                )
                row += 1

                # Current tool indicator
                if pane.current_tool and row < end_row - 1:
                    self._waddstr(
                        win,
                        row,
                        2,
                        _truncate_to_width(f"⚡ {pane.current_tool}", w - 3),
                        _PAIR_YELLOW,
                    )
                    row += 1

                # Tool history — Claude Code style with content preview
                if pane.tool_history and row < end_row - 1:
                    # Show the most recent tool calls that fit
                    history = pane.tool_history
                    # Render from the end, showing as many as space allows
                    # First pass: figure out how many we can show
                    avail = end_row - row - 1
                    visible = []
                    budget = avail
                    for tc in reversed(history):
                        # Each tool call needs at least 1 line (header)
                        # Plus optional content lines
                        needed = 1  # ● ToolName(args)
                        if (
                            tc.name in ("write_file", "edit_file")
                            and tc.content_lines
                        ):
                            needed += 1  # ⎿ Wrote N lines
                            needed += min(len(tc.content_lines), 6)
                            if tc.line_count > len(tc.content_lines):
                                needed += 1  # … +N more lines
                        elif tc.name == "execute" and tc.result_preview:
                            needed += min(tc.result_preview.count("\n") + 1, 3)
                        elif tc.result_preview and tc.name not in (
                            "write_file",
                            "edit_file",
                        ):
                            needed += 1  # ⎿ result
                        if budget < needed:
                            break
                        budget -= needed
                        visible.append(tc)
                    visible.reverse()

                    hidden = len(history) - len(visible)
                    if hidden > 0 and row < end_row - 1:
                        self._waddstr(
                            win,
                            row,
                            2,
                            f"  +{hidden} earlier tool calls",
                            _PAIR_DIM,
                        )
                        row += 1

                    for tc in visible:
                        if row >= end_row - 1:
                            break

                        # Resolve display filename
                        _fname = (
                            _short_path(tc.file_path)
                            if tc.file_path
                            else _extract_filename_from_summary(tc.summary)
                            or "?"
                        )

                        # ● ToolHeader — Claude Code style
                        if tc.name == "write_file":
                            header = f"● Write({_fname})"
                            self._waddstr(
                                win,
                                row,
                                1,
                                _truncate_to_width(header, w - 2),
                                _PAIR_CYAN,
                                bold=True,
                            )
                            row += 1
                            if row < end_row - 1:
                                sub = (
                                    f"  ⎿  Wrote"
                                    f" {tc.line_count}"
                                    f" lines to {_fname}"
                                )
                                self._waddstr(
                                    win,
                                    row,
                                    2,
                                    _truncate_to_width(sub, w - 3),
                                    _PAIR_DIM,
                                )
                                row += 1
                            # Content preview with line numbers
                            for li, line in enumerate(tc.content_lines[:6]):
                                if row >= end_row - 1:
                                    break
                                num = f"{li + 1:>4} "
                                self._waddstr(win, row, 3, num, _PAIR_DIM)
                                self._waddstr(
                                    win,
                                    row,
                                    3 + len(num),
                                    _truncate_to_width(line, w - 4 - len(num)),
                                    _PAIR_BOLD_WHITE,
                                )
                                row += 1
                            if (
                                tc.line_count > len(tc.content_lines)
                                and row < end_row - 1
                            ):
                                extra = tc.line_count - len(tc.content_lines)
                                self._waddstr(
                                    win,
                                    row,
                                    3,
                                    f"     … +{extra} more lines",
                                    _PAIR_DIM,
                                )
                                row += 1

                        elif tc.name == "edit_file":
                            header = f"● Edit({_fname})"
                            self._waddstr(
                                win,
                                row,
                                1,
                                _truncate_to_width(header, w - 2),
                                _PAIR_CYAN,
                                bold=True,
                            )
                            row += 1
                            if tc.content_lines and row < end_row - 1:
                                sub = f"  ⎿  Changed {tc.line_count} lines"
                                self._waddstr(
                                    win,
                                    row,
                                    2,
                                    _truncate_to_width(sub, w - 3),
                                    _PAIR_DIM,
                                )
                                row += 1
                                for line in tc.content_lines[:4]:
                                    if row >= end_row - 1:
                                        break
                                    display = f"     +{line}"
                                    self._waddstr(
                                        win,
                                        row,
                                        3,
                                        _truncate_to_width(display, w - 4),
                                        _PAIR_GREEN,
                                    )
                                    row += 1

                        elif tc.name == "execute":
                            cmd = (
                                tc.summary[2:]
                                if tc.summary.startswith("$ ")
                                else tc.summary
                            )
                            header = f"● Bash({_strip_ansi(cmd)})"
                            self._waddstr(
                                win,
                                row,
                                1,
                                _truncate_to_width(header, w - 2),
                                _PAIR_CYAN,
                                bold=True,
                            )
                            row += 1
                            if tc.result_preview and row < end_row - 1:
                                clean_result = _strip_ansi(tc.result_preview)
                                for rline in clean_result.split("\n")[:3]:
                                    if row >= end_row - 1:
                                        break
                                    self._waddstr(
                                        win,
                                        row,
                                        3,
                                        _truncate_to_width(
                                            f"  ⎿  {rline.strip()}", w - 4
                                        ),
                                        _PAIR_DIM,
                                    )
                                    row += 1

                        elif tc.name == "read_file":
                            header = f"● Read({_fname})"
                            self._waddstr(
                                win,
                                row,
                                1,
                                _truncate_to_width(header, w - 2),
                                _PAIR_CYAN,
                                bold=True,
                            )
                            row += 1

                        else:
                            # Generic tool
                            pair = _PAIR_RED if tc.is_error else _PAIR_CYAN
                            header = f"● {_strip_ansi(tc.summary)}"
                            self._waddstr(
                                win,
                                row,
                                1,
                                _truncate_to_width(header, w - 2),
                                pair,
                                bold=True,
                            )
                            row += 1
                            if tc.result_preview and row < end_row - 1:
                                self._waddstr(
                                    win,
                                    row,
                                    3,
                                    _truncate_to_width(
                                        "  ⎿  "
                                        + _strip_ansi(
                                            tc.result_preview
                                        )[:80],
                                        w - 4,
                                    ),
                                    _PAIR_DIM,
                                )
                                row += 1

                # AI text lines (when no tool history yet or between tools)
                elif pane.recent_lines and row < end_row - 1:
                    content_rows = end_row - row - 1
                    visible_lines = pane.recent_lines[-max(1, content_rows) :]
                    for line in visible_lines:
                        if row >= end_row - 1:
                            break
                        clean = _truncate_to_width(_strip_ansi(line), w - 3)
                        if clean.startswith("●") or clean.startswith("▸"):
                            self._waddstr(win, row, 2, clean, _PAIR_CYAN)
                        elif (
                            clean.startswith("✗")
                            or "error" in clean.lower()[:30]
                        ):
                            self._waddstr(win, row, 2, clean, _PAIR_RED)
                        else:
                            self._waddstr(win, row, 2, clean, _PAIR_BOLD_WHITE)
                        row += 1

                # Separator between running panes (not after last)
                if idx < n_running - 1 and end_row - 1 < height:
                    self._waddstr(
                        win, end_row - 1, 0, "┈" * (w - 1), _PAIR_DIM
                    )
                    row = end_row

        win.noutrefresh()

    @staticmethod
    def _waddstr(
        win, row: int, col: int, text: str, pair: int, bold: bool = False
    ):
        """Safe addstr with color, CJK-aware width truncation.

        Args:
            win: Curses window to write to.
            row: Row position.
            col: Column position.
            text: Text to display.
            pair: Curses color pair ID.
            bold: Whether to apply bold attribute.
        """
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
