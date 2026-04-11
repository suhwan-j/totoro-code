import os
import sys
import json
from pathlib import Path
from totoro.config.schema import AgentConfig


def load_config(
    cli_overrides: dict | None = None, project_root: str | None = None
) -> AgentConfig:
    """Load configuration with 5-level priority.

    Args:
        cli_overrides: Dict of CLI-provided config overrides.
        project_root: Project root path for locating project-level settings.

    Returns:
        Fully resolved AgentConfig instance.
    """
    config_dict = {}

    # User global
    user_path = Path.home() / ".totoro" / "settings.json"
    if user_path.exists():
        with open(user_path) as f:
            config_dict.update(json.load(f))

    # Project
    root = Path(project_root or os.getcwd())
    proj_path = root / ".totoro" / "settings.json"
    if proj_path.exists():
        with open(proj_path) as f:
            proj_data = json.load(f)
            # Filter out setup-wizard-only keys
            for k, v in proj_data.items():
                if k in ("api_key", "base_url", "extras"):
                    continue
                config_dict[k] = v

    # Env overrides
    if v := os.environ.get("TOTORO_MODEL"):
        config_dict["model"] = v
    if v := os.environ.get("TOTORO_FALLBACK_MODEL"):
        config_dict["fallback_model"] = v
    if v := os.environ.get("TOTORO_SANDBOX_MODE"):
        config_dict.setdefault("sandbox", {})["mode"] = v

    # CLI overrides
    if cli_overrides:
        config_dict.update(cli_overrides)

    config_dict.setdefault("project_root", str(root))
    return AgentConfig(**config_dict)


def ensure_api_keys(force_setup: bool = False):
    """Verify required API keys exist. Runs setup wizard if needed.

    Settings are stored at ~/.totoro/settings.json (user home, not project).

    Args:
        force_setup: When True, always run setup.
    """
    from totoro.config.setup import (
        load_provider_settings,
        inject_env_from_settings,
        run_setup_wizard,
    )

    # 1. Force setup via --setup flag
    if force_setup:
        settings = run_setup_wizard()
        inject_env_from_settings(settings)
        return

    # 2. Try ~/.totoro/settings.json
    settings = load_provider_settings()
    if settings:
        inject_env_from_settings(settings)
        return

    # 3. No settings.json — run wizard (if interactive)
    if sys.stdin.isatty():
        settings = run_setup_wizard()
        inject_env_from_settings(settings)
        return

    print("Error: No API key configured.")
    print(
        "Run `totoro --setup` interactively to"
        " configure, or create"
        " ~/.totoro/settings.json."
    )
    raise SystemExit(1)
