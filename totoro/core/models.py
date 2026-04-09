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


def create_lightweight_model(model_name: str = "claude-haiku-4-5-20251001"):
    """Create a lightweight LLM for Auto-Dream memory extraction.

    Tries providers in order: OpenRouter, Anthropic, OpenAI, vLLM.
    Returns None if no provider is available.
    """
    for factory in (_make_openrouter, _make_anthropic, _make_openai, _make_vllm):
        model = factory(model_name)
        if model is not None:
            return model
    return None


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
            openai_api_base=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
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
        return ChatOpenAI(model=model_name, openai_api_key=key, max_tokens=1024)
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
