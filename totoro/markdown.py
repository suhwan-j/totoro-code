"""Lightweight Markdown-to-ANSI renderer for terminal output.

Converts common Markdown elements to ANSI escape sequences for styled
terminal display. Used to render AI response text in the Totoro CLI.

Supported elements:
    - Headings (``#``, ``##``, ``###``)
    - Bold (``**text**``), italic (``*text*``), inline code (``` `code` ```)
    - Fenced code blocks (````` ``` `````)
    - Unordered (``-``, ``*``) and ordered (``1.``) lists
    - Horizontal rules (``---``, ``***``, ``___``)
"""

import re

from totoro.colors import (
    RESET,
    BOLD,
    DIM,
    BLUE,
    AMBER,
    AMBER_LT,
    IVORY,
    IVORY_DK,
    IVORY_LT,
)

# AI response body text color (white/ivory)
_TEXT = IVORY_LT

# ─── Pre-compiled regex patterns ───
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`]+?)`")
_HEADING_RE = re.compile(r"^(#{1,3}) (.+)$")
_ULIST_RE = re.compile(r"^(\s*)[-*] (.+)$")
_OLIST_RE = re.compile(r"^(\s*)(\d+)\. (.+)$")
_HR_RE = re.compile(r"^(---+|\*\*\*+|___+)\s*$")
_FENCE_RE = re.compile(r"^```")


def render(text: str) -> str:
    """Convert Markdown text to ANSI-formatted terminal output.

    Processes each line independently for block-level elements (headings,
    code blocks, lists) and applies inline formatting (bold, code, italic)
    within each line.

    Args:
        text: Markdown-formatted string to render.

    Returns:
        ANSI escape-styled string suitable for terminal display.

    Example:
        >>> render("## Title\\n- **bold** item")
        '\\n\\033[1mTitle\\033[0m\\n...'
    """
    lines = text.split("\n")
    out = []
    in_code = False

    for line in lines:
        # Fenced code block toggle
        if _FENCE_RE.match(line.rstrip()):
            in_code = not in_code
            if in_code:
                # Opening fence — show language hint if present
                lang = line.rstrip()[3:].strip()
                label = f" {lang} " if lang else ""
                out.append(
                    f"{DIM}┌{label}{'─' * max(0, 50 - len(label))}{RESET}"
                )
            else:
                out.append(f"{DIM}└{'─' * 50}{RESET}")
            continue

        if in_code:
            out.append(f"{DIM}│{RESET} {AMBER_LT}{line}{RESET}")
            continue

        # Horizontal rule
        if _HR_RE.match(line):
            out.append(f"{DIM}{'─' * 50}{RESET}")
            continue

        # Headings
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2)
            title = _inline(title)
            if level == 1:
                out.append(f"\n{BOLD}{BLUE}{title}{RESET}")
            elif level == 2:
                out.append(f"\n{BOLD}{title}{RESET}")
            else:
                out.append(f"{BOLD}{title}{RESET}")
            continue

        # Unordered list
        m = _ULIST_RE.match(line)
        if m:
            indent, content = m.group(1), m.group(2)
            out.append(
                f"{indent}{DIM}•{RESET} {_TEXT}{_inline(content)}{RESET}"
            )
            continue

        # Ordered list
        m = _OLIST_RE.match(line)
        if m:
            indent, num, content = m.group(1), m.group(2), m.group(3)
            out.append(
                f"{indent}{DIM}{num}.{RESET} {_TEXT}{_inline(content)}{RESET}"
            )
            continue

        # Regular line — apply inline formatting
        out.append(f"{_TEXT}{_inline(line)}{RESET}")

    return "\n".join(out)


def _inline(text: str) -> str:
    """Apply inline Markdown formatting to a single line.

    Processes bold (``**text**``), inline code (``` `text` ```), and
    italic (``*text*``) in that order to avoid conflicts.

    Args:
        text: Single line of text to format.

    Returns:
        Line with ANSI escape codes for inline formatting applied.
    """
    # Bold: **text**
    text = _BOLD_RE.sub(f"{BOLD}\\1{RESET}{_TEXT}", text)
    # Inline code: `text`
    text = _INLINE_CODE_RE.sub(f"{AMBER}\\1{RESET}{_TEXT}", text)
    # Italic: *text* (but not inside bold)
    text = _ITALIC_RE.sub(f"{IVORY}\\1{RESET}{_TEXT}", text)
    return text
