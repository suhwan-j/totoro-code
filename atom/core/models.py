"""LLM provider initialization."""
import os
from dotenv import load_dotenv


def create_lightweight_model(model_name: str = "claude-haiku-4-5-20251001"):
    """Create a lightweight LLM for Auto-Dream memory extraction.

    Tries providers in order: OpenRouter, Anthropic, OpenAI.
    Returns None if no provider is available.
    """
    load_dotenv()

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            from langchain_openai import ChatOpenAI
            model_map = {
                "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4-5",
                "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
            }
            resolved = model_map.get(model_name, model_name)
            return ChatOpenAI(
                model=resolved,
                openai_api_key=openrouter_key,
                openai_api_base=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                max_tokens=1024,
            )
        except Exception:
            pass

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model_name, max_tokens=1024)
        except Exception:
            pass

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model_name, max_tokens=1024)
        except Exception:
            pass

    return None
