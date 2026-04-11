"""LLM provider initialization.

All providers read from os.environ, which is populated by
inject_env_from_settings() at CLI boot (from .totoro/settings.json).
No .env file is needed.
"""

import os

# Anthropic model ID → OpenRouter model ID mapping.
# Needed because AgentConfig.fallback_model defaults to Anthropic format
# ("claude-haiku-4-5-20251001") but OpenRouter requires "anthropic/..." prefix.
_OPENROUTER_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4-5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
}

# Provider-specific lightweight models for Auto-Dream / Context Compaction.
# Used when the configured fallback_model doesn't match the active provider.
_LIGHTWEIGHT_MODELS = {
    "openrouter": "anthropic/claude-haiku-4-5",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-5.4-mini",
    "vllm": None,  # Uses whatever model is served
}


def create_lightweight_model(
    model_name: str = "claude-haiku-4-5-20251001",
    provider: str | None = None,
):
    """Create a lightweight LLM for Auto-Dream memory extraction.

    Resolves the model name to match the active provider. For example,
    if provider is "openai" but model_name is "claude-haiku-4-5-20251001",
    it automatically switches to "gpt-4o-mini" instead.

    Args:
        model_name: Model identifier to instantiate.
        provider: Resolved provider name. If provided, ensures the model
            is compatible with this provider.

    Returns:
        An LLM model instance, or None if no provider is available.
    """
    # If provider is known, ensure model is compatible
    if provider and provider != "auto":
        model_name = _resolve_lightweight_model(model_name, provider)
        factory = _PROVIDER_FACTORIES.get(provider)
        if factory:
            model = factory(model_name)
            if model is not None:
                return model

    # Fallback: try all providers in order
    for factory in (
        _make_openrouter,
        _make_anthropic,
        _make_openai,
        _make_vllm,
    ):
        model = factory(model_name)
        if model is not None:
            return model
    return None


def _resolve_lightweight_model(model_name: str, provider: str) -> str:
    """Ensure the lightweight model name is valid for the given provider.

    Args:
        model_name: Configured fallback model name.
        provider: Active provider name.

    Returns:
        A model name compatible with the provider.
    """
    # Check if the model looks like it belongs to a different provider
    is_claude = "claude" in model_name.lower()
    is_gpt = "gpt" in model_name.lower()

    if provider == "openai" and is_claude:
        return _LIGHTWEIGHT_MODELS["openai"]
    if provider == "anthropic" and is_gpt:
        return _LIGHTWEIGHT_MODELS["anthropic"]
    if provider == "openrouter" and not model_name.startswith(
        ("anthropic/", "openai/")
    ):
        mapped = _OPENROUTER_MODEL_MAP.get(model_name)
        if mapped:
            return mapped

    return model_name


_PROVIDER_FACTORIES: dict = {}  # Populated after factory functions are defined


def _make_openrouter(model_name: str):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    resolved = _OPENROUTER_MODEL_MAP.get(model_name, model_name)
    try:
        from langchain_openrouter import ChatOpenRouter

        return ChatOpenRouter(model=resolved, api_key=key, max_tokens=1024)
    except Exception:
        pass
    try:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=resolved,
            openai_api_key=key,
            openai_api_base=os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            max_tokens=1024,
        )
    except Exception:
        return None


def _make_anthropic(model_name: str):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model_name, max_tokens=1024)
    except Exception:
        return None


def _make_openai(model_name: str):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name, openai_api_key=key, max_tokens=1024
        )
    except Exception:
        return None


def _make_vllm(model_name: str):
    base_url = os.environ.get("VLLM_BASE_URL")
    if not base_url:
        return None
    try:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            openai_api_key=os.environ.get("VLLM_API_KEY", "EMPTY"),
            openai_api_base=base_url,
            max_tokens=1024,
        )
    except Exception:
        return None


# Initialize provider factory map (must be after function definitions)
_PROVIDER_FACTORIES.update(
    {
        "openrouter": _make_openrouter,
        "anthropic": _make_anthropic,
        "openai": _make_openai,
        "vllm": _make_vllm,
    }
)
