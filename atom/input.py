"""Input handler with slash command autocomplete dropdown.

Uses prompt_toolkit for inline autocomplete — typing "/" shows a
filterable command menu (like Claude Code).
"""
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

# Mode definitions
MODES = ["default", "auto-approve", "plan-only"]
MODE_ICONS = {
    "default": "◆",
    "auto-approve": "⚡",
    "plan-only": "📋",
}
# bg color, fg on bg, line color, line char
MODE_THEME = {
    "default":       ("\033[42m\033[1;30m", "\033[0;32m", "─"),       # green bg, green line
    "auto-approve":  ("\033[43m\033[1;30m", "\033[0;33m", "━"),       # yellow bg, yellow bold line
    "plan-only":     ("\033[46m\033[1;30m", "\033[0;36m", "┄"),       # cyan bg, cyan dashed line
}
MODE_LABELS = {
    "default": "\033[1;32mdefault\033[0m",
    "auto-approve": "\033[1;33mauto-approve\033[0m",
    "plan-only": "\033[1;36mplan-only\033[0m",
}
_DIM = "\033[0;90m"
_RESET = "\033[0m"
_BOLD = "\033[1m"

# prompt_toolkit style
_STYLE = Style.from_dict({
    "completion-menu":                "bg:#1a1a2e #e0e0e0",
    "completion-menu.completion":     "bg:#1a1a2e #e0e0e0",
    "completion-menu.completion.current": "bg:#16213e #00d4ff bold",
    "completion-menu.meta":           "bg:#1a1a2e #888888",
    "completion-menu.meta.current":   "bg:#16213e #aaddff",
    "scrollbar.background":           "bg:#1a1a2e",
    "scrollbar.button":               "bg:#333355",
    # Transparent background for bottom toolbar
    "bottom-toolbar":                 "noreverse",
    "bottom-toolbar.text":            "noreverse",
})


class SlashCompleter(Completer):
    """Autocomplete for slash commands — triggers on '/'."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Only complete when input starts with "/"
        if not text.startswith("/"):
            return

        from atom.commands.registry import COMMAND_LIST

        query = text.lower()
        for cmd, desc in COMMAND_LIST:
            if cmd.startswith(query) or query.lstrip("/") in cmd:
                # Replace entire input with the command
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=desc,
                )


class InputHandler:
    """Handles user input with inline slash command autocomplete.

    Typing "/" shows a dropdown menu of commands that filters as you type.
    Shift+Tab cycles through modes.
    """

    def __init__(self, initial_mode: str = "default"):
        self.mode = initial_mode

        # Key bindings
        self._bindings = KeyBindings()
        handler_ref = self

        @self._bindings.add("s-tab")
        def _shift_tab(event):
            """Shift+Tab: cycle mode in-place, redraw prompt."""
            handler_ref.cycle_mode()
            event.app.invalidate()  # force redraw with new toolbar/prompt

        self._session = PromptSession(
            completer=SlashCompleter(),
            key_bindings=self._bindings,
            style=_STYLE,
            complete_while_typing=True,
            complete_in_thread=True,
            reserve_space_for_menu=0,
        )

    def cycle_mode(self) -> str:
        """Cycle to next mode and return the new mode name."""
        idx = MODES.index(self.mode)
        self.mode = MODES[(idx + 1) % len(MODES)]
        return self.mode

    def mode_top_bar(self) -> str:
        """Top bar:  ──────────── DEFAULT ──  with colored bg label and themed line."""
        import shutil
        width = min(shutil.get_terminal_size().columns, 120)
        bg_style, line_color, line_char = MODE_THEME.get(self.mode, ("\033[47m\033[1;30m", _DIM, "─"))
        label = f" {self.mode.upper()} "
        # ─────────── DEFAULT ──
        tag_len = len(label) + 4  # " " + label + " " + "──"
        pad = width - tag_len
        return f"{line_color}{line_char * max(0, pad)}{_RESET} {bg_style}{label}{_RESET} {line_color}{line_char * 2}{_RESET}"

    def mode_bottom_bar(self) -> str:
        """Bottom bar: themed separator line."""
        import shutil
        width = min(shutil.get_terminal_size().columns, 120)
        _, line_color, line_char = MODE_THEME.get(self.mode, ("\033[47m\033[1;30m", _DIM, "─"))
        return f"{line_color}{line_char * width}{_RESET}"

    @property
    def prompt_html(self) -> HTML:
        """Build prompt as HTML with top bar included for live mode switching."""
        import shutil
        width = min(shutil.get_terminal_size().columns, 120)
        _, _, line_char = MODE_THEME.get(self.mode, ("\033[47m\033[1;30m", _DIM, "─"))
        label = self.mode.upper()
        line_fg = {"default": "ansigreen", "auto-approve": "ansiyellow", "plan-only": "ansicyan"}.get(self.mode, "ansigray")
        icon = MODE_ICONS.get(self.mode, "◆")

        pad = width - len(label) - 6
        bar = line_char * max(0, pad)

        return HTML(
            f'<style fg="{line_fg}">{bar}</style>'
            f' <style bg="{line_fg}" fg="ansiblack" bold="true"> {label} </style>'
            f' <style fg="{line_fg}">{line_char}{line_char}</style>\n'
            f'<style fg="{line_fg}" bold="true">{icon} &gt; </style>'
        )

    @property
    def prompt(self) -> str:
        """Plain ANSI prompt string (for non-prompt_toolkit contexts)."""
        icon = MODE_ICONS.get(self.mode, "◆")
        if self.mode == "default":
            return f"\033[1;32m{icon} > \033[0m"
        if self.mode == "auto-approve":
            return f"\033[1;33m{icon} > \033[0m"
        return f"\033[1;36m{icon} > \033[0m"

    @property
    def is_auto_approve(self) -> bool:
        return self.mode == "auto-approve"

    @property
    def is_plan_only(self) -> bool:
        return self.mode == "plan-only"

    def _bottom_toolbar(self):
        """Dim line + next-mode hint (always visible during input)."""
        import shutil
        width = min(shutil.get_terminal_size().columns, 120)
        idx = MODES.index(self.mode)
        next_mode = MODES[(idx + 1) % len(MODES)]
        next_fg = {"default": "ansigreen", "auto-approve": "ansiyellow", "plan-only": "ansicyan"}.get(next_mode, "ansigray")
        bar = '─' * width
        return HTML(
            f'<style fg="ansigray">{bar}</style>\n'
            f'<style fg="ansigray">⏵⏵ </style>'
            f'<style fg="{next_fg}" bold="true">{next_mode}</style>'
            f'<style fg="ansigray"> on (shift+tab)</style>'
        )

    def read_input(self) -> str | None:
        """Read a line of input with inline slash-command autocomplete.

        Returns:
            The user's input string, or None on EOF/interrupt.
        """
        try:
            return self._session.prompt(
                lambda: self.prompt_html,
                bottom_toolbar=self._bottom_toolbar,
                refresh_interval=0.5,
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return None


def pick_command() -> str | None:
    """Show a numbered menu of slash commands (fallback).

    Returns the selected command string (e.g. "/model") or None if cancelled.
    """
    from atom.commands.registry import COMMAND_LIST

    DIM = "\033[0;90m"
    CYAN = "\033[1;36m"
    BOLD = "\033[1m"
    YELLOW = "\033[1;33m"
    R = "\033[0m"

    print(f"{DIM}  ── Commands ──{R}")
    for i, (cmd, desc) in enumerate(COMMAND_LIST):
        num = f"{YELLOW}{i + 1:>2}{R}"
        print(f"  {num}) {CYAN}{cmd:<14}{R} {DIM}{desc}{R}")
    print(f"{DIM}  Enter number or command name (q to cancel){R}")

    try:
        choice = input(f"  {BOLD}#{R} ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not choice or choice.lower() == "q":
        return None

    # By number
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(COMMAND_LIST):
            return COMMAND_LIST[idx][0]
    except ValueError:
        pass

    # By name (with or without /)
    if not choice.startswith("/"):
        choice = "/" + choice
    names = [cmd for cmd, _ in COMMAND_LIST]
    if choice in names:
        return choice
    matches = [c for c in names if c.startswith(choice)]
    if len(matches) == 1:
        return matches[0]

    print(f"  {DIM}Unknown: {choice}{R}")
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
