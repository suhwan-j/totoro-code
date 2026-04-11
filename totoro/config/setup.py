"""Interactive setup wizard for Totoro CLI."""

import getpass
import json
import os
import sys
from pathlib import Path

# ANSI colors (palette-based)
from totoro.colors import (
    BLUE as _CYAN,
    AMBER as _YELLOW,
    AMBER_LT as _GREEN,
    DIM as _DIM,
    BOLD as _BOLD,
    COPPER as _RED,
    RESET as _RESET,
    ACCENT,
    BODY,
    SECONDARY,
)

PROVIDERS = [
    ("openrouter", "OpenRouter", "recommended, multi-model"),
    ("anthropic", "Anthropic", "Claude direct"),
    ("openai", "OpenAI", "GPT models"),
    ("vllm", "vLLM", "self-hosted"),
]

# Provider → available models
_PROVIDER_MODELS = {
    "openrouter": [
        ("z-ai/glm-5.1", "GLM 5.1", "default"),
        ("z-ai/glm-5v-turbo", "GLM 5v Turbo", "vision & multimodal"),
        ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5", "fast & cheap"),
        (
            "google/gemini-3.1-flash-lite-preview",
            "Gemini 3.1 Flash",
            "fast & cheap",
        ),
        ("qwen/qwen3.5-35b-a3b", "Qwen3.5 35B", "open-source & efficient"),
    ],
    "anthropic": [
        ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", "recommended"),
        ("claude-opus-4-5-20250918", "Claude Opus 4.5", "most capable"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "fast & cheap"),
    ],
    "openai": [
        ("gpt-5.4", "GPT-5.4", "recommended"),
        ("gpt-5.4-mini", "GPT-5.4 Mini", "fast"),
    ],
    "vllm": [],  # user specifies custom model
}

# Provider → env var mapping
_ENV_MAP = {
    "openrouter": {
        "api_key": "OPENROUTER_API_KEY",
        "base_url": "OPENROUTER_BASE_URL",
    },
    "anthropic": {"api_key": "ANTHROPIC_API_KEY"},
    "openai": {"api_key": "OPENAI_API_KEY"},
    "vllm": {"api_key": "VLLM_API_KEY", "base_url": "VLLM_BASE_URL"},
}

_EXTRAS_ENV_MAP = {
    "tavily_api_key": "TAVILY_API_KEY",
}


def run_setup_wizard(project_root: Path = None) -> dict:
    """Run interactive setup wizard. Saves to ~/.totoro/settings.json.

    Args:
        project_root: Optional project root path (currently unused).

    Returns:
        Dict of saved settings including provider, api_key, model, etc.
    """
    print()
    print(f"  {_DIM}╭─────────────────────────────────────╮{_RESET}")
    print(
        f"  {_DIM}│  {ACCENT}Totoro Setup{_DIM}"
        f"                         │{_RESET}"
    )
    print(f"  {_DIM}╰─────────────────────────────────────╯{_RESET}")
    print()

    # Load existing settings for defaults
    existing = load_provider_settings()

    # 1. Select provider
    provider = _select_provider(existing)

    # 2. Enter API key
    api_key = _enter_api_key(provider, existing)

    # 3. Base URL (for openrouter / vllm)
    base_url = _enter_base_url(provider, existing)

    # 4. Select main model
    model = _select_model(provider, existing)

    # 5. Select lightweight model
    fallback_model = _select_lightweight_model(provider, model, existing)

    settings = {"provider": provider, "api_key": api_key}
    if model:
        settings["model"] = model
    if fallback_model:
        settings["fallback_model"] = fallback_model
    if base_url:
        settings["base_url"] = base_url

    # 6. Optional extras
    extras = _configure_extras(existing)
    if extras:
        settings["extras"] = extras

    # Save
    save_settings(settings)

    print()
    print(
        f"  {_GREEN}✓{_RESET} Saved to {_BOLD}~/.totoro/settings.json{_RESET}"
    )
    print()

    return settings


def _select_provider(existing: dict | None) -> str:
    """Prompt user to select an LLM provider interactively.

    Args:
        existing: Existing settings dict for showing
            current selection, or None.

    Returns:
        Selected provider key string.
    """
    current = existing.get("provider") if existing else None
    print(f"  Select your LLM provider:")
    print()
    for i, (key, name, desc) in enumerate(PROVIDERS, 1):
        marker = (
            f" {_DIM}(current){_RESET}"
            if key == current else ""
        )
        print(
            f"    {_BOLD}{i}){_RESET}"
            f" {name:<12}"
            f" {_DIM}({desc}){_RESET}{marker}"
        )
    print()

    while True:
        try:
            default_hint = ""
            if current:
                idx = next(
                    (
                        i
                        for i, (k, _, _) in enumerate(PROVIDERS, 1)
                        if k == current
                    ),
                    None,
                )
                if idx:
                    default_hint = f" [{idx}]"
            choice = input(
                f"  > {default_hint and f'{_DIM}{default_hint}{_RESET} '}"
            )
            choice = choice.strip()

            if not choice and current:
                return current

            num = int(choice)
            if 1 <= num <= len(PROVIDERS):
                return PROVIDERS[num - 1][0]
            print(
                f"  {_RED}1-{len(PROVIDERS)} 사이의 숫자를 입력하세요.{_RESET}"
            )
        except (ValueError, EOFError):
            print(
                f"  {_RED}1-{len(PROVIDERS)} 사이의 숫자를 입력하세요.{_RESET}"
            )
        except KeyboardInterrupt:
            print()
            raise SystemExit(0)


def _enter_api_key(provider: str, existing: dict | None) -> str:
    """Prompt user to enter an API key for the selected provider.

    Args:
        provider: Provider key string.
        existing: Existing settings dict or None.

    Returns:
        API key string.
    """
    current_key = existing.get("api_key") if existing else None
    masked = (
        f"{current_key[:8]}...{current_key[-4:]}"
        if current_key and len(current_key) > 12
        else None
    )

    provider_name = next(
        (name for k, name, _ in PROVIDERS if k == provider), provider
    )
    hint = f" {_DIM}(Enter to keep: {masked}){_RESET}" if masked else ""
    print(f"\n  Enter your {_BOLD}{provider_name}{_RESET} API key:{hint}")

    while True:
        try:
            key = getpass.getpass(prompt="  > ")
            if not key and current_key:
                return current_key
            if key.strip():
                return key.strip()
            print(f"  {_RED}API 키를 입력하세요.{_RESET}")
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)


def _enter_base_url(provider: str, existing: dict | None) -> str | None:
    """Prompt user to enter a base URL for providers that require one.

    Args:
        provider: Provider key string.
        existing: Existing settings dict for showing current URL, or None.

    Returns:
        Base URL string, or None if the provider doesn't need one.
    """
    if provider == "openrouter":
        default = "https://openrouter.ai/api/v1"
        current = (existing or {}).get("base_url", default)
        print(
            f"\n  OpenRouter Base URL"
            f" {_DIM}(Enter for default:"
            f" {current}){_RESET}"
        )
        try:
            url = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)
        return url if url else current

    if provider == "vllm":
        current = (existing or {}).get("base_url")
        hint = f" {_DIM}(Enter to keep: {current}){_RESET}" if current else ""
        print(
            f"\n  Enter your vLLM base URL (e.g. http://localhost:8000/v1):{hint}"
        )
        while True:
            try:
                url = input("  > ").strip()
                if not url and current:
                    return current
                if url:
                    return url
                print(f"  {_RED}vLLM base URL을 입력하세요.{_RESET}")
            except (EOFError, KeyboardInterrupt):
                print()
                raise SystemExit(0)

    return None


def _select_model(provider: str, existing: dict | None) -> str | None:
    """Show available models for the selected provider and let user pick one.

    Args:
        provider: Provider key string.
        existing: Existing settings dict for showing current model, or None.

    Returns:
        Selected model ID string, or None if no model was chosen.
    """
    models = _PROVIDER_MODELS.get(provider, [])

    if not models:
        # vLLM or unknown — ask for custom model name
        current = (existing or {}).get("model")
        hint = f" {_DIM}(Enter to keep: {current}){_RESET}" if current else ""
        print(f"\n  Enter model name for your vLLM endpoint:{hint}")
        try:
            name = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)
        if not name and current:
            return current
        return name if name else None

    current = (existing or {}).get("model")
    print(f"\n  Select model:")
    print()
    for i, (model_id, display_name, note) in enumerate(models, 1):
        marker = f" {_CYAN}← current{_RESET}" if model_id == current else ""
        note_str = f" {_DIM}({note}){_RESET}" if note else ""
        print(f"    {_BOLD}{i}){_RESET} {display_name:<22}{note_str}{marker}")
    print(f"    {_BOLD}c){_RESET} {_DIM}Custom model ID...{_RESET}")
    print()

    # Find default: current model or first in list
    default_model = models[0][0]
    default_idx = 1
    if current:
        for i, (m, _, _) in enumerate(models, 1):
            if m == current:
                default_model = m
                default_idx = i
                break

    # Show default hint in the prompt label, not inline
    default_name = models[default_idx - 1][1]
    print(f"  {_DIM}(Enter for default: {default_name}){_RESET}")

    while True:
        try:
            choice = input("  > ").strip()

            if not choice:
                print(f"  {_GREEN}✓{_RESET} {default_model}")
                return default_model

            if choice.lower() == "c":
                print(f"\n  Enter custom model ID:")
                custom = input("  > ").strip()
                if custom:
                    return custom
                continue

            num = int(choice)
            if 1 <= num <= len(models):
                selected = models[num - 1][0]
                print(f"  {_GREEN}✓{_RESET} {selected}")
                return selected
            print(
                f"  {_RED}1-{len(models)}"
                f" 사이의 숫자 또는 'c'를"
                f" 입력하세요.{_RESET}"
            )
        except ValueError:
            print(
                f"  {_RED}1-{len(models)}"
                f" 사이의 숫자 또는 'c'를"
                f" 입력하세요.{_RESET}"
            )
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)


def _select_lightweight_model(
    provider: str, main_model: str | None, existing: dict | None
) -> str | None:
    """Prompt user to select a lightweight model for auxiliary tasks.

    Used for Auto-Dream memory extraction, Context Compaction, and
    Catbus planner. If the user skips (Enter), the main model is used.

    Args:
        provider: Provider key string.
        main_model: The selected main model ID.
        existing: Existing settings dict or None.

    Returns:
        Selected lightweight model ID, or None to use the main model.
    """
    models = _PROVIDER_MODELS.get(provider, [])
    current = (existing or {}).get("fallback_model")
    main_display = main_model or "none"

    print(
        f"\n  Select lightweight model"
        f" {_DIM}(Auto-Dream, Compaction){_RESET}:"
    )
    print(f"  {_DIM}Enter to use main model ({main_display}){_RESET}")
    print()

    if models:
        for i, (model_id, display_name, note) in enumerate(models, 1):
            marker = (
                f" {_CYAN}← current{_RESET}" if model_id == current else ""
            )
            note_str = f" {_DIM}({note}){_RESET}" if note else ""
            print(
                f"    {_BOLD}{i}){_RESET} {display_name:<22}{note_str}{marker}"
            )
        print(f"    {_BOLD}c){_RESET} {_DIM}Custom model ID...{_RESET}")
        print()

    while True:
        try:
            choice = input("  > ").strip()

            # Enter = use main model
            if not choice:
                print(
                    f"  {_GREEN}✓{_RESET}"
                    f" {_DIM}main model"
                    f" ({main_display}){_RESET}"
                )
                return None

            if models:
                if choice.lower() == "c":
                    print(f"\n  Enter custom lightweight model ID:")
                    custom = input("  > ").strip()
                    if custom:
                        print(f"  {_GREEN}✓{_RESET} {custom}")
                        return custom
                    continue

                try:
                    num = int(choice)
                    if 1 <= num <= len(models):
                        selected = models[num - 1][0]
                        print(f"  {_GREEN}✓{_RESET} {selected}")
                        return selected
                    print(
                        f"  {_RED}1-{len(models)}"
                        f" 사이의 숫자, 'c',"
                        f" 또는 Enter{_RESET}"
                    )
                except ValueError:
                    # Treat as custom model ID
                    print(f"  {_GREEN}✓{_RESET} {choice}")
                    return choice
            else:
                # No preset list (vLLM) — treat input as custom model name
                print(f"  {_GREEN}✓{_RESET} {choice}")
                return choice

        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)


def _configure_extras(existing: dict | None) -> dict:
    """Prompt user to configure optional extras like Tavily API key.

    Args:
        existing: Existing settings dict for showing current extras, or None.

    Returns:
        Dict of configured extras.
    """
    extras = {}
    existing_extras = (existing or {}).get("extras", {})

    # Tavily
    current_tavily = existing_extras.get("tavily_api_key")
    try:
        prompt = (
            f"\n  Configure web search (Tavily API key)? {_DIM}[y/N]{_RESET} "
        )
        if current_tavily:
            prompt = (
                f"\n  Update Tavily API key?"
                f" {_DIM}(current:"
                f" {current_tavily[:8]}...){_RESET}"
                f" {_DIM}[y/N]{_RESET} "
            )
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer in ("y", "yes"):
        try:
            key = getpass.getpass(prompt="  Tavily API key > ")
            if key.strip():
                extras["tavily_api_key"] = key.strip()
        except (EOFError, KeyboardInterrupt):
            pass
    elif current_tavily:
        extras["tavily_api_key"] = current_tavily

    return extras


def save_settings(settings: dict, project_root: Path = None):
    """Save settings to ~/.totoro/settings.json.

    Args:
        settings: Settings dict to persist.
        project_root: Optional project root path (currently unused).
    """
    totoro_dir = Path.home() / ".totoro"
    totoro_dir.mkdir(parents=True, exist_ok=True)

    settings_path = totoro_dir / "settings.json"

    # Merge with existing non-provider settings (permissions, memory, etc.)
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                existing = json.load(f)
            # Keep non-wizard fields
            for key in list(existing.keys()):
                if key not in ("provider", "api_key", "base_url", "extras"):
                    settings.setdefault(key, existing[key])
        except (json.JSONDecodeError, OSError):
            pass

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_provider_settings(project_root: Path = None) -> dict | None:
    """Load settings from ~/.totoro/settings.json.

    Args:
        project_root: Optional project root path (currently unused).

    Returns:
        Settings dict if found and valid, None otherwise.
    """
    settings_path = Path.home() / ".totoro" / "settings.json"
    if not settings_path.exists():
        return None
    try:
        with open(settings_path) as f:
            data = json.load(f)
        # Must have at least provider and api_key (or base_url for vllm)
        if data.get("provider") and (
            data.get("api_key") or data.get("base_url")
        ):
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def inject_env_from_settings(settings: dict):
    """Inject settings into os.environ.

    Ensures existing provider factories work unchanged.

    Args:
        settings: Settings dict with provider, api_key, base_url, and extras.
    """
    provider = settings.get("provider")
    api_key = settings.get("api_key")
    base_url = settings.get("base_url")

    env_mapping = _ENV_MAP.get(provider, {})

    if api_key and "api_key" in env_mapping:
        os.environ[env_mapping["api_key"]] = api_key
    if base_url and "base_url" in env_mapping:
        os.environ[env_mapping["base_url"]] = base_url

    # Set provider preference
    if provider:
        os.environ["TOTORO_PROVIDER"] = provider

    # Extras
    extras = settings.get("extras", {})
    for settings_key, env_key in _EXTRAS_ENV_MAP.items():
        value = extras.get(settings_key)
        if value is not None:
            os.environ[env_key] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )


def ensure_gitignore(project_root: Path):
    """Add .totoro/settings.json to .gitignore if not already present.

    Args:
        project_root: Path to the project root containing .gitignore.
    """
    gitignore_path = project_root / ".gitignore"
    entry = ".totoro/settings.json"

    if gitignore_path.exists():
        content = gitignore_path.read_text()
        if entry in content:
            return
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# Totoro settings (contains API keys)\n{entry}\n"
        gitignore_path.write_text(content)
    else:
        gitignore_path.write_text(
            f"# Totoro settings (contains API keys)\n{entry}\n"
        )
