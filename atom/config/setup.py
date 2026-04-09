"""Interactive setup wizard for Atom CLI."""

import getpass
import json
import os
import sys
from pathlib import Path

# ANSI colors
_CYAN = "\033[1;36m"
_YELLOW = "\033[1;33m"
_GREEN = "\033[0;32m"
_DIM = "\033[0;90m"
_BOLD = "\033[1m"
_RED = "\033[1;31m"
_RESET = "\033[0m"

PROVIDERS = [
    ("openrouter", "OpenRouter", "recommended, multi-model"),
    ("anthropic", "Anthropic", "Claude direct"),
    ("openai", "OpenAI", "GPT models"),
    ("vllm", "vLLM", "self-hosted"),
]

# Provider → available models
_PROVIDER_MODELS = {
    "openrouter": [
        ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5", "fast & cheap"),
        ("openai/gpt-5.4-mini", "GPT-5.4 Mini", "fast"),
        ("google/gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash", "fast & cheap"),
        ("z-ai/glm-5v-turbo", "GLM 5v Turbo", "fast"),
        ("qwen/qwen3.5-35b-a3b", "Qwen3.5 35B", ""),
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
    "openrouter": {"api_key": "OPENROUTER_API_KEY", "base_url": "OPENROUTER_BASE_URL"},
    "anthropic": {"api_key": "ANTHROPIC_API_KEY"},
    "openai": {"api_key": "OPENAI_API_KEY"},
    "vllm": {"api_key": "VLLM_API_KEY", "base_url": "VLLM_BASE_URL"},
}

_EXTRAS_ENV_MAP = {
    "tavily_api_key": "TAVILY_API_KEY",
    "langsmith_api_key": "LANGSMITH_API_KEY",
    "langsmith_tracing": "LANGSMITH_TRACING",
    "langsmith_project": "LANGSMITH_PROJECT",
}


def run_setup_wizard(project_root: Path) -> dict:
    """Run interactive setup wizard. Returns settings dict."""
    print()
    print(f"  {_CYAN}╭─────────────────────────────────────╮{_RESET}")
    print(f"  {_CYAN}│  {_YELLOW}Atom Setup{_CYAN}                         │{_RESET}")
    print(f"  {_CYAN}╰─────────────────────────────────────╯{_RESET}")
    print()

    # Load existing settings for defaults
    existing = load_provider_settings(project_root)

    # 1. Select provider
    provider = _select_provider(existing)

    # 2. Enter API key
    api_key = _enter_api_key(provider, existing)

    # 3. Base URL (for openrouter / vllm)
    base_url = _enter_base_url(provider, existing)

    # 4. Select model
    model = _select_model(provider, existing)

    settings = {"provider": provider, "api_key": api_key}
    if model:
        settings["model"] = model
    if base_url:
        settings["base_url"] = base_url

    # 5. Optional extras
    extras = _configure_extras(existing)
    if extras:
        settings["extras"] = extras

    # Save
    save_settings(settings, project_root)

    print()
    print(f"  {_GREEN}✓{_RESET} Saved to {_BOLD}.atom/settings.json{_RESET}")
    print()

    return settings


def _select_provider(existing: dict | None) -> str:
    current = existing.get("provider") if existing else None
    print(f"  Select your LLM provider:")
    print()
    for i, (key, name, desc) in enumerate(PROVIDERS, 1):
        marker = f" {_DIM}(current){_RESET}" if key == current else ""
        print(f"    {_BOLD}{i}){_RESET} {name:<12} {_DIM}({desc}){_RESET}{marker}")
    print()

    while True:
        try:
            default_hint = ""
            if current:
                idx = next((i for i, (k, _, _) in enumerate(PROVIDERS, 1) if k == current), None)
                if idx:
                    default_hint = f" [{idx}]"
            choice = input(f"  > {default_hint and f'{_DIM}{default_hint}{_RESET} '}")
            choice = choice.strip()

            if not choice and current:
                return current

            num = int(choice)
            if 1 <= num <= len(PROVIDERS):
                return PROVIDERS[num - 1][0]
            print(f"  {_RED}1-{len(PROVIDERS)} 사이의 숫자를 입력하세요.{_RESET}")
        except (ValueError, EOFError):
            print(f"  {_RED}1-{len(PROVIDERS)} 사이의 숫자를 입력하세요.{_RESET}")
        except KeyboardInterrupt:
            print()
            raise SystemExit(0)


def _enter_api_key(provider: str, existing: dict | None) -> str:
    current_key = existing.get("api_key") if existing else None
    masked = f"{current_key[:8]}...{current_key[-4:]}" if current_key and len(current_key) > 12 else None

    provider_name = next((name for k, name, _ in PROVIDERS if k == provider), provider)
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
    if provider == "openrouter":
        default = "https://openrouter.ai/api/v1"
        current = (existing or {}).get("base_url", default)
        print(f"\n  OpenRouter Base URL {_DIM}(Enter for default: {current}){_RESET}")
        try:
            url = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)
        return url if url else current

    if provider == "vllm":
        current = (existing or {}).get("base_url")
        hint = f" {_DIM}(Enter to keep: {current}){_RESET}" if current else ""
        print(f"\n  Enter your vLLM base URL (e.g. http://localhost:8000/v1):{hint}")
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
    """Show available models for the selected provider and let user pick one."""
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

    while True:
        try:
            default_hint = ""
            if current:
                idx = next((i for i, (m, _, _) in enumerate(models, 1) if m == current), None)
                if idx:
                    default_hint = f" [{idx}]"
            choice = input(f"  > {default_hint and f'{_DIM}{default_hint}{_RESET} '}").strip()

            if not choice and current:
                return current

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
            print(f"  {_RED}1-{len(models)} 사이의 숫자 또는 'c'를 입력하세요.{_RESET}")
        except ValueError:
            print(f"  {_RED}1-{len(models)} 사이의 숫자 또는 'c'를 입력하세요.{_RESET}")
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)


def _configure_extras(existing: dict | None) -> dict:
    extras = {}
    existing_extras = (existing or {}).get("extras", {})

    # Tavily
    current_tavily = existing_extras.get("tavily_api_key")
    try:
        prompt = f"\n  Configure web search (Tavily API key)? {_DIM}[y/N]{_RESET} "
        if current_tavily:
            prompt = f"\n  Update Tavily API key? {_DIM}(current: {current_tavily[:8]}...){_RESET} {_DIM}[y/N]{_RESET} "
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

    # LangSmith
    current_ls = existing_extras.get("langsmith_api_key")
    try:
        prompt = f"\n  Configure LangSmith tracing? {_DIM}[y/N]{_RESET} "
        if current_ls:
            prompt = f"\n  Update LangSmith config? {_DIM}(current: {current_ls[:8]}...){_RESET} {_DIM}[y/N]{_RESET} "
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer in ("y", "yes"):
        try:
            key = getpass.getpass(prompt="  LangSmith API key > ")
            if key.strip():
                extras["langsmith_api_key"] = key.strip()
                extras["langsmith_tracing"] = True
                project = input(f"  LangSmith project name {_DIM}[ATOM-CODE]{_RESET} > ").strip()
                extras["langsmith_project"] = project if project else "ATOM-CODE"
        except (EOFError, KeyboardInterrupt):
            pass
    elif current_ls:
        extras["langsmith_api_key"] = current_ls
        extras["langsmith_tracing"] = existing_extras.get("langsmith_tracing", True)
        extras["langsmith_project"] = existing_extras.get("langsmith_project", "ATOM-CODE")

    return extras


def save_settings(settings: dict, project_root: Path):
    """Save settings to .atom/settings.json."""
    atom_dir = project_root / ".atom"
    atom_dir.mkdir(parents=True, exist_ok=True)

    settings_path = atom_dir / "settings.json"

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


def load_provider_settings(project_root: Path) -> dict | None:
    """Load settings from .atom/settings.json. Returns None if not found."""
    settings_path = project_root / ".atom" / "settings.json"
    if not settings_path.exists():
        return None
    try:
        with open(settings_path) as f:
            data = json.load(f)
        # Must have at least provider and api_key (or base_url for vllm)
        if data.get("provider") and (data.get("api_key") or data.get("base_url")):
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def inject_env_from_settings(settings: dict):
    """Inject settings into os.environ so existing provider factories work unchanged."""
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
        os.environ["ATOM_PROVIDER"] = provider

    # Extras
    extras = settings.get("extras", {})
    for settings_key, env_key in _EXTRAS_ENV_MAP.items():
        value = extras.get(settings_key)
        if value is not None:
            os.environ[env_key] = str(value).lower() if isinstance(value, bool) else str(value)


def ensure_gitignore(project_root: Path):
    """Add .atom/settings.json to .gitignore if not already present."""
    gitignore_path = project_root / ".gitignore"
    entry = ".atom/settings.json"

    if gitignore_path.exists():
        content = gitignore_path.read_text()
        if entry in content:
            return
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# Atom settings (contains API keys)\n{entry}\n"
        gitignore_path.write_text(content)
    else:
        gitignore_path.write_text(f"# Atom settings (contains API keys)\n{entry}\n")
