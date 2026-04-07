"""Input handler with Shift+Tab mode cycling.

Uses readline for proper line editing (backspace, arrows, history).
Shift+Tab is bound as a readline macro that submits "/mode".
"""
import sys
import readline as _rl

# Mode definitions
MODES = ["default", "auto-approve", "plan-only"]
MODE_LABELS = {
    "default": "\033[1;32mdefault\033[0m",
    "auto-approve": "\033[1;33mauto-approve\033[0m",
    "plan-only": "\033[1;36mplan-only\033[0m",
}
MODE_ICONS = {
    "default": "◆",
    "auto-approve": "⚡",
    "plan-only": "📋",
}


class InputHandler:
    """Handles user input with Shift+Tab mode cycling.

    Uses standard input() (backed by readline) for all input so that
    backspace, arrow keys, and history all work normally.
    Shift+Tab is a readline macro that clears the line and submits "/mode".
    """

    def __init__(self, initial_mode: str = "default"):
        self.mode = initial_mode
        self._bind_shift_tab()

    def _bind_shift_tab(self):
        """Bind Shift+Tab (ESC [ Z) to clear line and submit /mode."""
        try:
            # Macro: Ctrl-U (kill line) + type "/mode" + Ctrl-M (enter)
            _rl.parse_and_bind(r'"\e[Z": "\C-u/mode\C-m"')
        except Exception:
            pass

    def cycle_mode(self) -> str:
        """Cycle to next mode and return the new mode name."""
        idx = MODES.index(self.mode)
        self.mode = MODES[(idx + 1) % len(MODES)]
        return self.mode

    @property
    def prompt(self) -> str:
        """Build prompt string showing current mode."""
        icon = MODE_ICONS.get(self.mode, "◆")
        if self.mode == "default":
            return f"\033[1;32m{icon} > \033[0m"
        label = MODE_LABELS.get(self.mode, self.mode)
        return f"\033[1;32m{icon} [{label}\033[1;32m] > \033[0m"

    @property
    def is_auto_approve(self) -> bool:
        return self.mode == "auto-approve"

    @property
    def is_plan_only(self) -> bool:
        return self.mode == "plan-only"

    def read_input(self) -> str | None:
        """Read a line of input.

        Shift+Tab submits "/mode" via readline macro, which the CLI loop
        handles like any other slash command. Normal line editing works
        perfectly (backspace, arrows, history).

        Returns:
            The user's input string, or None on EOF/interrupt.
        """
        try:
            return input(self.prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return None


def format_mode_help() -> str:
    """Format mode descriptions for /help output."""
    lines = [
        "  \033[1mModes\033[0m (cycle with \033[1mShift+Tab\033[0m or \033[1m/mode\033[0m):",
    ]
    for mode in MODES:
        icon = MODE_ICONS[mode]
        label = MODE_LABELS[mode]
        if mode == "default":
            desc = "Normal mode with approval prompts"
        elif mode == "auto-approve":
            desc = "Skip all approval prompts"
        elif mode == "plan-only":
            desc = "Agent plans but doesn't execute"
        lines.append(f"    {icon} {label}: {desc}")
    return "\n".join(lines)
