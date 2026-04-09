"""File operation diff display helpers."""

import os
import sys

from totoro.utils import sanitize_text

# ANSI colors (palette-based)
from totoro.colors import DIM as _DIM, AMBER_LT as _GREEN, COPPER as _RED, BLUE as _CYAN, RESET as _RESET


def find_line_number(file_path: str, search_text: str) -> int | None:
    """Find the line number where search_text starts in file."""
    try:
        with open(file_path) as f:
            content = f.read()
        idx = content.find(search_text)
        if idx == -1:
            return None
        return content[:idx].count("\n") + 1
    except (OSError, UnicodeDecodeError):
        return None


def format_file_diff(tool_name: str, args: dict, start_line: int | None = None) -> str | None:
    """Format a file operation as a visual diff block."""
    file_path = args.get("file_path", "")
    # Show relative path if possible
    try:
        rel = os.path.relpath(file_path)
    except ValueError:
        rel = file_path

    if tool_name == "write_file":
        content = args.get("content", "")
        lines = content.splitlines()
        added = len(lines)
        header = f"\n{_CYAN}● Write({rel}){_RESET}"
        header += f"\n  {_DIM}⎿  Created ({added} lines){_RESET}"
        # Show first/last few lines as preview
        preview_lines = []
        max_preview = 8
        if added <= max_preview:
            for i, line in enumerate(lines, 1):
                preview_lines.append(f"      {_DIM}{i:>4}{_RESET} {_GREEN}+{line}{_RESET}")
        else:
            for i, line in enumerate(lines[:4], 1):
                preview_lines.append(f"      {_DIM}{i:>4}{_RESET} {_GREEN}+{line}{_RESET}")
            preview_lines.append(f"      {_DIM}     ... ({added - 6} more lines){_RESET}")
            for i, line in enumerate(lines[-2:], added - 1):
                preview_lines.append(f"      {_DIM}{i:>4}{_RESET} {_GREEN}+{line}{_RESET}")
        return header + "\n" + "\n".join(preview_lines)

    if tool_name == "edit_file":
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        old_lines = old_string.splitlines()
        new_lines = new_string.splitlines()
        added = len(new_lines)
        removed = len(old_lines)

        header = f"\n{_CYAN}● Update({rel}){_RESET}"
        header += f"\n  {_DIM}⎿  Added {added} lines, removed {removed} lines{_RESET}"

        diff_lines = []

        # Show removed/added lines with line numbers
        max_context = 10
        for i, line in enumerate(old_lines[:max_context]):
            ln = f"{start_line + i:>4}" if start_line else "    "
            diff_lines.append(f"      {_DIM}{ln}{_RESET} {_RED}-{line}{_RESET}")
        if len(old_lines) > max_context:
            diff_lines.append(f"      {_DIM}     ... ({len(old_lines) - max_context} more removed){_RESET}")

        for i, line in enumerate(new_lines[:max_context]):
            ln = f"{start_line + i:>4}" if start_line else "    "
            diff_lines.append(f"      {_DIM}{ln}{_RESET} {_GREEN}+{line}{_RESET}")
        if len(new_lines) > max_context:
            diff_lines.append(f"      {_DIM}     ... ({len(new_lines) - max_context} more added){_RESET}")

        return header + "\n" + "\n".join(diff_lines)

    return None


def safe_print(text: str, **kwargs):
    """Print with surrogate-safe encoding."""
    try:
        print(sanitize_text(text), **kwargs)
    except UnicodeEncodeError:
        safe = text.encode("utf-8", errors="replace").decode("utf-8")
        print(safe, **kwargs)
