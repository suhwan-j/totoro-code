import os
import json
from pathlib import Path
from atom.config.schema import AgentConfig
from dotenv import load_dotenv


def load_config(cli_overrides: dict | None = None, project_root: str | None = None) -> AgentConfig:
    """5-level config priority: CLI > env > project > user > defaults"""
    config_dict = {}

    # User global
    user_path = Path.home() / ".atom" / "settings.json"
    if user_path.exists():
        with open(user_path) as f:
            config_dict.update(json.load(f))

    # Project
    root = Path(project_root or os.getcwd())
    proj_path = root / ".atom" / "settings.json"
    if proj_path.exists():
        with open(proj_path) as f:
            config_dict.update(json.load(f))

    # Env overrides
    load_dotenv()
    if v := os.environ.get("ATOM_MODEL"):
        config_dict["model"] = v
    if v := os.environ.get("ATOM_FALLBACK_MODEL"):
        config_dict["fallback_model"] = v
    if v := os.environ.get("ATOM_SANDBOX_MODE"):
        config_dict.setdefault("sandbox", {})["mode"] = v

    # CLI overrides
    if cli_overrides:
        config_dict.update(cli_overrides)

    config_dict.setdefault("project_root", str(root))
    return AgentConfig(**config_dict)


def ensure_api_keys():
    """Verify required API keys exist."""
    load_dotenv()
    has_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )
    if not has_key:
        print("Error: Missing API key.")
        print("Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY in .env")
        raise SystemExit(1)
