"""Token estimation utilities shared across layers.

Provides a weighted estimator that accounts for CJK characters
(Korean, Japanese, Chinese) which typically consume 2-3 tokens per character,
unlike Latin text which averages ~4 characters per token.
"""

import re

# CJK Unified Ideographs + Hangul Syllables + Katakana/Hiragana ranges
_CJK_RE = re.compile(
    r"[\u3000-\u303f"  # CJK punctuation
    r"\u3040-\u309f"  # Hiragana
    r"\u30a0-\u30ff"  # Katakana
    r"\u4e00-\u9fff"  # CJK Unified Ideographs
    r"\uac00-\ud7af"  # Hangul Syllables
    r"\uf900-\ufaff"  # CJK Compatibility Ideographs
    r"]"
)


def estimate_tokens(messages: list) -> int:
    """Estimate token count from messages with CJK-aware weighting.

    - Latin/ASCII text: ~4 chars per token (standard heuristic)
    - CJK characters (Korean, Japanese, Chinese): ~1.5 chars per token
      (each character typically becomes 2-3 BPE tokens)

    This avoids under-counting for CJK-heavy conversations, which would
    cause context compaction to trigger too late.

    Args:
        messages: List of message objects with .content attributes.

    Returns:
        Estimated total token count across all messages.
    """
    total = 0
    for m in messages:
        content = getattr(m, "content", None)
        if content is None:
            continue
        if isinstance(content, list):
            # Multi-block content (tool_use, text blocks, etc.)
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            text = " ".join(text_parts)
        else:
            text = str(content)
        total += _estimate_text_tokens(text)
    return total


def _estimate_text_tokens(text: str) -> int:
    """Estimate tokens for a single text string.

    Args:
        text: The text string to estimate.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    cjk_chars = len(_CJK_RE.findall(text))
    non_cjk_chars = len(text) - cjk_chars
    # CJK: ~2 tokens per char, Latin: ~0.25 tokens per char
    return int(cjk_chars * 2 + non_cjk_chars / 4)


# ─── Model context window mapping ───

# Known context window sizes by model name substring (checked in order).
# Covers common model families. Falls back to 200K if no match.
_CONTEXT_WINDOW_MAP: list[tuple[str, int]] = [
    # Claude family
    ("claude-opus-4", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-3-7", 200_000),
    ("claude-3-5", 200_000),
    ("claude-3-opus", 200_000),
    ("claude-3-sonnet", 200_000),
    ("claude-3-haiku", 200_000),
    ("claude-haiku-4", 200_000),
    ("claude", 200_000),  # fallback for any Claude
    # OpenAI GPT family
    ("gpt-4.1", 1_047_576),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4-1106", 128_000),
    ("gpt-4-0125", 128_000),
    ("gpt-4", 8_192),
    ("gpt-3.5-turbo-16k", 16_384),
    ("gpt-3.5-turbo", 16_384),
    ("o3", 200_000),
    ("o4-mini", 200_000),
    ("o1", 200_000),
    # Google Gemini
    ("gemini-2", 1_048_576),
    ("gemini-1.5-pro", 2_097_152),
    ("gemini-1.5-flash", 1_048_576),
    ("gemini-pro", 32_768),
    ("gemini", 1_048_576),
    # Meta Llama
    ("llama-3.3", 128_000),
    ("llama-3.2", 128_000),
    ("llama-3.1", 128_000),
    ("llama-3", 8_192),
    ("llama", 8_192),
    # Mistral
    ("mistral-large", 128_000),
    ("mistral-medium", 32_000),
    ("mistral-small", 32_000),
    ("mixtral", 32_768),
    ("mistral", 32_000),
    # DeepSeek
    ("deepseek-r1", 128_000),
    ("deepseek-v3", 128_000),
    ("deepseek-v2", 128_000),
    ("deepseek", 64_000),
    # Qwen
    ("qwen", 128_000),
]

_DEFAULT_CONTEXT_WINDOW = 200_000


def get_model_context_window(model_name: str) -> int:
    """Return the context window size for a model name.

    Checks known model families by substring match. Returns 200K default
    if no match is found (safe assumption for modern models).

    Args:
        model_name: Model name/identifier, optionally with provider prefix.

    Returns:
        Context window size in tokens.
    """
    lower = model_name.lower()
    # Strip common provider prefixes
    for prefix in (
        "anthropic/",
        "openai/",
        "google/",
        "meta-llama/",
        "mistralai/",
        "deepseek/",
        "qwen/",
    ):
        if lower.startswith(prefix):
            lower = lower[len(prefix) :]
            break

    for pattern, window in _CONTEXT_WINDOW_MAP:
        if pattern in lower:
            return window

    return _DEFAULT_CONTEXT_WINDOW
