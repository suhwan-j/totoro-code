import os
import sys
import json
from pathlib import Path
from atom.config.schema import AgentConfig


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
            proj_data = json.load(f)
            # Filter out setup-wizard-only keys that don't belong in AgentConfig
            for k, v in proj_data.items():
                if k not in ("api_key", "base_url", "extras"):
                    config_dict[k] = v

    # Env overrides
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


def ensure_api_keys(force_setup: bool = False):
    """Verify required API keys exist. Runs setup wizard if needed."""
    from atom.config.setup import load_provider_settings, inject_env_from_settings, run_setup_wizard

    project_root = Path(os.getcwd())

    # 1. Force setup via --setup flag
    if force_setup:
        settings = run_setup_wizard(project_root)
        inject_env_from_settings(settings)
        return

    # 2. Try .atom/settings.json
    settings = load_provider_settings(project_root)
    if settings:
        inject_env_from_settings(settings)
        return

    # 3. No settings.json — run wizard (if interactive)
    if sys.stdin.isatty():
        settings = run_setup_wizard(project_root)
        inject_env_from_settings(settings)
        return

    print("Error: No API key configured.")
    print("Run `atom --setup` interactively to configure, or create .atom/settings.json.")
    raise SystemExit(1)
