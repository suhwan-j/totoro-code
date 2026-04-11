"""Shared utilities for Totoro."""

import re

# Matches Unicode surrogate characters (U+D800 to U+DFFF)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_text(text: str) -> str:
    """Remove surrogate characters that break UTF-8 encoding.

    Subprocess output on WSL/Windows can produce surrogate characters
    when decoding mixed-encoding byte streams. These surrogates are
    invalid in UTF-8 and will cause errors in JSON serialization,
    print(), and API calls.

    Args:
        text: Input string that may contain surrogate characters.

    Returns:
        Cleaned string with surrogates replaced.
    """
    if not isinstance(text, str):
        return str(text)
    return _SURROGATE_RE.sub("\ufffd", text)
