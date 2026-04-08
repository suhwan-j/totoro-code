"""Background hotkey listener for mode switching during streaming.

Sets terminal to cbreak mode during streaming so single keypresses
can be detected without waiting for Enter. Detects Shift+Tab (ESC [ Z).
"""

import sys
import select
import tty
import termios
import threading

_DIM = "\033[0;90m"
_RESET = "\033[0m"


class HotkeyListener:
    """Listens for Shift+Tab during streaming to cycle mode.

    Usage:
        hotkey = HotkeyListener(handler)
        hotkey.activate()   # sets cbreak mode, starts polling thread
        ...streaming...
        hotkey.deactivate() # restores terminal, stops thread
    """

    def __init__(self, handler):
        self._handler = handler
        self._thread: threading.Thread | None = None
        self._halt = threading.Event()
        self._old_settings = None

    def activate(self):
        """Enter cbreak mode and start polling thread."""
        if not sys.stdin.isatty():
            return
        try:
            fd = sys.stdin.fileno()
            self._old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)  # single-char input, normal output
        except (termios.error, OSError):
            self._old_settings = None
            return

        self._halt.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def deactivate(self):
        """Stop polling and restore terminal."""
        self._halt.set()
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None
        if self._old_settings is not None:
            try:
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, self._old_settings)
            except (termios.error, OSError):
                pass
            self._old_settings = None

    def shutdown(self):
        """Alias for deactivate."""
        self.deactivate()

    def _poll_loop(self):
        """Poll stdin for Shift+Tab."""
        while not self._halt.is_set():
            try:
                rlist, _, _ = select.select([sys.stdin], [], [], 0.15)
                if not rlist:
                    continue
                ch = sys.stdin.read(1)
                if ch == '\x1b':
                    self._read_escape_seq()
            except (OSError, ValueError):
                break

    def _read_escape_seq(self):
        """Read rest of escape sequence. Detect Shift+Tab (ESC [ Z)."""
        rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not rlist:
            return  # plain Escape — ignore

        ch2 = sys.stdin.read(1)
        if ch2 != '[':
            self._drain()
            return

        rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not rlist:
            return

        ch3 = sys.stdin.read(1)
        if ch3 == 'Z':
            # Shift+Tab!
            self._cycle_mode()
        else:
            self._drain()

    def _drain(self):
        """Consume remaining chars of an escape sequence."""
        while True:
            rlist, _, _ = select.select([sys.stdin], [], [], 0.01)
            if not rlist:
                break
            sys.stdin.read(1)

    def _cycle_mode(self):
        """Cycle mode and print inline notification."""
        new_mode = self._handler.cycle_mode()
        if new_mode == "auto-approve":
            color = "\033[1;33m"
        elif new_mode == "plan-only":
            color = "\033[1;36m"
        else:
            color = "\033[1;32m"
        sys.stdout.write(f"\r{_DIM}  ⏵⏵ {color}{new_mode}{_RESET}{_DIM} on (shift+tab to cycle){_RESET}\033[K\n")
        sys.stdout.flush()
