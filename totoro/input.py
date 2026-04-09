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
from totoro.colors import (
    RESET, BOLD,
    DIM, BLUE, BLUE_LT, AMBER, AMBER_LT, IVORY, IVORY_DK, COPPER,
)

MODES = ["default", "auto-approve", "plan-only"]
MODE_ICONS = {
    "default": "◆",
    "auto-approve": "⚡",
    "plan-only": "📋",
}
# bg color (truecolor bg), line color, line char
# bg: \033[48;2;R;G;Bm  fg on bg: dark text
_BG_BLUE   = "\033[48;2;86;160;240m\033[38;2;13;17;23m"    # #56A0F0 bg, #0D1117 text
_BG_AMBER  = "\033[48;2;245;178;64m\033[38;2;13;17;23m"   # #F5B240 bg, #0D1117 text
_BG_IVORY  = "\033[48;2;212;186;142m\033[38;2;13;17;23m"  # #D4BA8E bg, #0D1117 text
MODE_THEME = {
    "default":       (_BG_BLUE,  BLUE,  "─"),
    "auto-approve":  (_BG_AMBER, AMBER, "━"),
    "plan-only":     (_BG_IVORY, IVORY, "┄"),
}
MODE_LABELS = {
    "default": f"{BLUE}default{RESET}",
    "auto-approve": f"{AMBER}auto-approve{RESET}",
    "plan-only": f"{IVORY}plan-only{RESET}",
}
_DIM = DIM
_RESET = RESET
_BOLD = BOLD

# prompt_toolkit style — palette-based
_STYLE = Style.from_dict({
    "completion-menu":                "bg:#0D1117 #A8CFFA",
    "completion-menu.completion":     "bg:#0D1117 #A8CFFA",
    "completion-menu.completion.current": "bg:#1A5FA8 #F5ECD8 bold",
    "completion-menu.meta":           "bg:#0D1117 #60503A",
    "completion-menu.meta.current":   "bg:#1A5FA8 #D4BA8E",
    "scrollbar.background":           "bg:#0D1117",
    "scrollbar.button":               "bg:#60503A",
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

        from totoro.commands.registry import COMMAND_LIST

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

        @self._bindings.add("c-j")
        def _ctrl_j(event):
            """Ctrl+J: insert newline for multiline input."""
            event.current_buffer.insert_text("\n")

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
        width = shutil.get_terminal_size().columns
        bg_style, line_color, line_char = MODE_THEME.get(self.mode, (_BG_BLUE, _DIM, "─"))
        label = f" {self.mode.upper()} "
        # ─────────── DEFAULT ──
        tag_len = len(label) + 4  # " " + label + " " + "──"
        pad = width - tag_len
        return f"{line_color}{line_char * max(0, pad)}{_RESET} {bg_style}{label}{_RESET} {line_color}{line_char * 2}{_RESET}"

    def mode_bottom_bar(self) -> str:
        """Bottom bar: themed separator line."""
        import shutil
        width = shutil.get_terminal_size().columns
        _, line_color, line_char = MODE_THEME.get(self.mode, (_BG_BLUE, _DIM, "─"))
        return f"{line_color}{line_char * width}{_RESET}"

    @property
    def prompt_html(self) -> HTML:
        """Build prompt as HTML with top bar included for live mode switching."""
        import shutil
        width = shutil.get_terminal_size().columns
        _, _, line_char = MODE_THEME.get(self.mode, (_BG_BLUE, DIM, "─"))
        label = self.mode.upper()
        line_fg = {"default": "#56A0F0", "auto-approve": "#F5B240", "plan-only": "#D4BA8E"}.get(self.mode, "#60503A")
        tag_bg = {"default": "#56A0F0", "auto-approve": "#F5B240", "plan-only": "#D4BA8E"}.get(self.mode, "#56A0F0")
        icon = MODE_ICONS.get(self.mode, "◆")

        pad = width - len(label) - 6
        bar = line_char * max(0, pad)

        return HTML(
            f'<style fg="{line_fg}">{bar}</style>'
            f' <style bg="{tag_bg}" fg="#0D1117" bold="true"> {label} </style>'
            f' <style fg="{line_fg}">{line_char}{line_char}</style>\n'
            f'<style fg="{line_fg}" bold="true">{icon} &gt; </style>'
        )

    @property
    def prompt(self) -> str:
        """Plain ANSI prompt string (for non-prompt_toolkit contexts)."""
        icon = MODE_ICONS.get(self.mode, "◆")
        color = {"default": BLUE, "auto-approve": AMBER, "plan-only": IVORY}.get(self.mode, BLUE)
        return f"{color}{_BOLD}{icon} > {_RESET}"

    @property
    def is_auto_approve(self) -> bool:
        return self.mode == "auto-approve"

    @property
    def is_plan_only(self) -> bool:
        return self.mode == "plan-only"

    def _bottom_toolbar(self):
        """Dim line + next-mode hint (always visible during input)."""
        import shutil
        width = shutil.get_terminal_size().columns
        idx = MODES.index(self.mode)
        next_mode = MODES[(idx + 1) % len(MODES)]
        next_fg = {"default": "#56A0F0", "auto-approve": "#F5B240", "plan-only": "#D4BA8E"}.get(next_mode, "#60503A")
        bar = '─' * width
        return HTML(
            f'<style fg="#60503A">{bar}</style>\n'
            f'<style fg="#60503A">⏵⏵ </style>'
            f'<style fg="{next_fg}" bold="true">{next_mode}</style>'
            f'<style fg="#60503A"> on (shift+tab)</style>'
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
    from totoro.commands.registry import COMMAND_LIST
    from totoro.colors import DIM as _D, BLUE as _B, AMBER as _A, BODY as _BT, RESET as _R, BOLD as _BO

    print(f"{_D}  ── Commands ──{_R}")
    for i, (cmd, desc) in enumerate(COMMAND_LIST):
        num = f"{_A}{i + 1:>2}{_R}"
        print(f"  {num}) {_B}{cmd:<14}{_R} {_D}{desc}{_R}")
    print(f"{_D}  Enter number or command name (q to cancel){_R}")

    try:
        choice = input(f"  {_BO}#{_R} ").strip()
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

    print(f"  {_D}Unknown: {choice}{_R}")
    return None


def format_mode_help() -> str:
    """Format mode descriptions for /help output."""
    from totoro.colors import BOLD as _BO, RESET as _R
    lines = [
        f"  {_BO}Modes{_R} (cycle with {_BO}Shift+Tab{_R} or {_BO}/mode{_R}):",
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
