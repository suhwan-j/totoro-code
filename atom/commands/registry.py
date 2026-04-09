"""Slash command registry and handler."""
import time

# These are injected by CLI after agent creation
_session_manager = None
_auto_dream = None
_agent_config = None  # AgentConfig instance for /model
_skill_manager = None  # SkillManager instance


def set_session_manager(manager):
    """Inject session manager for /session commands."""
    global _session_manager
    _session_manager = manager


def set_auto_dream(extractor):
    """Inject auto-dream extractor for /memory commands."""
    global _auto_dream
    _auto_dream = extractor


def set_agent_config(config):
    """Inject AgentConfig for /model command."""
    global _agent_config
    _agent_config = config


def set_skill_manager(manager):
    """Inject SkillManager for /skill commands."""
    global _skill_manager
    _skill_manager = manager


# Command metadata for autocomplete and menu
COMMAND_LIST = [
    ("/help",     "Show help message"),
    ("/model",    "Show or switch model"),
    ("/mode",     "Cycle mode (default → auto-approve → plan-only)"),
    ("/new",      "Start a new session (e.g. /new fix login bug)"),
    ("/clear",    "Same as /new"),
    ("/session",  "Show/switch session (e.g. /session 2)"),
    ("/sessions", "List all sessions with numbers"),
    ("/compact",  "Force context compaction"),
    ("/memory",   "Show/clear memories"),
    ("/skill",    "Manage skills (list/add/install/remove/reload)"),
    ("/tasks",    "Show active sub-agent tasks"),
    ("/status",   "Show agent status"),
    ("/exit",     "Exit the CLI"),
]


def get_command_names() -> list[str]:
    """Return list of command names for autocomplete."""
    return [cmd for cmd, _ in COMMAND_LIST]


def handle_slash_command(user_input: str, agent, invoke_config: dict) -> str | None:
    """Parse and execute a slash command. Returns output string, '__exit__', or None."""
    parts = user_input.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    handlers = {
        "/help": _cmd_help,
        "/exit": _cmd_exit,
        "/quit": _cmd_exit,
        "/new": _cmd_new,
        "/clear": _cmd_new,
        "/model": _cmd_model,
        "/session": _cmd_session,
        "/sessions": _cmd_sessions,
        "/compact": _cmd_compact,
        "/memory": _cmd_memory,
        "/skill": _cmd_skill,
        "/tasks": _cmd_tasks,
        "/status": _cmd_status,
    }

    handler = handlers.get(cmd)
    if handler is None:
        return f"Unknown command: {cmd}. Type /help for available commands."

    return handler(args, agent, invoke_config)


def _cmd_help(args, agent, config) -> str:
    from atom.input import format_mode_help
    mode_help = format_mode_help()
    return f"""\033[1mAvailable commands:\033[0m
  /help              Show this help message
  /exit              Exit the CLI
  /mode              Cycle mode (default → auto-approve → plan-only)
  /new [description] Start a new session (e.g. /new fix login bug)
  /clear             Same as /new
  /model             Show available models & switch interactively
  /model <name>      Switch to a specific model (e.g. /model claude-haiku-4-5)
  /session           Show current session info
  /session <id|#>    Switch to another session (e.g. /session 2)
  /sessions          List all sessions with numbers
  /compact           Force context compaction
  /memory            Show extracted memories
  /memory clear      Clear all memories
  /tasks             Show active sub-agent tasks
  /status            Show agent status (turns, tokens, memories)

{mode_help}"""


def _cmd_exit(args, agent, config) -> str:
    return "__exit__"


def _cmd_new(args, agent, config) -> str:
    description = args.strip()
    new_session_id = f"session-{int(time.time())}"
    config["configurable"]["thread_id"] = new_session_id
    if _session_manager:
        _session_manager.create_session(new_session_id, description=description)
    desc = f" — {description}" if description else ""
    return f"\033[1;32mNew session:\033[0m {new_session_id}{desc}"


def _cmd_model(args, agent, config) -> str:
    from atom.config.setup import _PROVIDER_MODELS
    model_name = _agent_config.model if _agent_config else "unknown"
    provider = _agent_config.provider if _agent_config else "unknown"

    if not args.strip():
        # No argument — show interactive model selector
        models = _PROVIDER_MODELS.get(provider, [])
        if not models:
            # No predefined list (vllm etc.) — show info + usage
            lines = [
                f"\033[1mCurrent model:\033[0m {model_name}",
                f"\033[1mProvider:\033[0m {provider}",
                "",
                "\033[0;90mUsage: /model <model-name>\033[0m",
            ]
            return "\n".join(lines)

        # Show numbered list
        print(f"\033[1mCurrent model:\033[0m {model_name}")
        print(f"\033[1mProvider:\033[0m {provider}")
        print()
        print(f"  Select model:")
        print()
        for i, (mid, display, note) in enumerate(models, 1):
            marker = " \033[1;36m← current\033[0m" if mid == model_name else ""
            note_str = f" \033[0;90m({note})\033[0m" if note else ""
            print(f"    \033[1m{i})\033[0m {display:<22}{note_str}{marker}")
        print(f"    \033[1mc)\033[0m \033[0;90mCustom model ID...\033[0m")
        print()

        while True:
            try:
                choice = input("  > ").strip()
                if not choice:
                    return f"Keeping current model: {model_name}"

                if choice.lower() == "c":
                    print(f"\n  Enter custom model ID:")
                    custom = input("  > ").strip()
                    if custom:
                        return f"__model_change__:{custom}"
                    return f"Keeping current model: {model_name}"

                num = int(choice)
                if 1 <= num <= len(models):
                    selected = models[num - 1][0]
                    if selected == model_name:
                        return f"Already using: {model_name}"
                    return f"__model_change__:{selected}"
                print(f"  \033[1;31m1-{len(models)} 사이의 숫자 또는 'c'를 입력하세요.\033[0m")
            except ValueError:
                print(f"  \033[1;31m1-{len(models)} 사이의 숫자 또는 'c'를 입력하세요.\033[0m")
            except (EOFError, KeyboardInterrupt):
                return f"Keeping current model: {model_name}"

    # Parse: /model <model_name> [provider]
    parts = args.strip().split()
    new_model = parts[0]
    new_provider = parts[1] if len(parts) > 1 else None

    # Return sentinel for interactive loop to handle agent rebuild
    if new_provider:
        return f"__model_change__:{new_model}:{new_provider}"
    return f"__model_change__:{new_model}"


def _cmd_session(args, agent, config) -> str:
    arg = args.strip()

    # /session <id_or_number> → switch session
    if arg:
        return _switch_session(arg, agent, config)

    # /session (no args) → show current session info
    session_id = config["configurable"]["thread_id"]
    info_lines = [f"Session ID: {session_id}"]

    if _session_manager:
        session = _session_manager.get_session(session_id)
        if session:
            info_lines.append(f"  Turns: {session.turn_count}")
            age = time.time() - session.created_at
            info_lines.append(f"  Age: {_format_age(age)}")

    # Check for pending interrupts
    try:
        state = agent.get_state(config)
        if state and state.next:
            info_lines.append(f"  \033[1;33mPending interrupt at: {state.next}\033[0m")
    except Exception:
        pass

    info_lines.append("")
    info_lines.append("\033[0;90mUsage: /session <id_or_number> to switch\033[0m")
    info_lines.append("\033[0;90m       /sessions to list all sessions\033[0m")

    return "\n".join(info_lines)


def _switch_session(target: str, agent, config) -> str:
    """Switch to another session by ID or list number."""
    if _session_manager is None:
        return "Session manager not available."

    current_id = config["configurable"]["thread_id"]
    sessions = _session_manager.list_sessions()

    target_session = None

    # Try as a list number (1-based)
    try:
        idx = int(target) - 1
        if 0 <= idx < len(sessions):
            target_session = sessions[idx]
    except ValueError:
        pass

    # Try as session ID (exact or prefix match)
    if target_session is None:
        for s in sessions:
            if s.session_id == target:
                target_session = s
                break
        if target_session is None:
            matches = [s for s in sessions if target in s.session_id]
            if len(matches) == 1:
                target_session = matches[0]
            elif len(matches) > 1:
                ids = ", ".join(s.session_id for s in matches[:5])
                return f"Ambiguous — multiple matches: {ids}"

    if target_session is None:
        return f"Session not found: {target}. Use /sessions to list available sessions."

    if target_session.session_id == current_id:
        return f"Already on session: {current_id}"

    # Verify state exists in checkpointer
    target_config = _session_manager.get_invoke_config(target_session.session_id)
    try:
        state = agent.get_state(target_config)
        msg_count = len(state.values.get("messages", [])) if state and state.values else 0
    except Exception:
        msg_count = 0

    # Switch
    config["configurable"]["thread_id"] = target_session.session_id

    desc = f" — {target_session.description}" if target_session.description else ""
    return (
        f"\033[1;32mSwitched to session:\033[0m {target_session.session_id}{desc}\n"
        f"  Turns: {target_session.turn_count} · Messages: {msg_count}"
    )


def _cmd_sessions(args, agent, config) -> str:
    if _session_manager is None:
        return "Session manager not available."

    sessions = _session_manager.list_sessions()
    if not sessions:
        return "No sessions found."

    current_id = config["configurable"]["thread_id"]
    lines = ["\033[1mSessions:\033[0m  \033[0;90m(use /session <number> to switch)\033[0m"]
    for i, s in enumerate(sessions, 1):
        age = _format_age(time.time() - s.created_at)
        active = _format_age(time.time() - s.last_active)
        desc = f" — {s.description}" if s.description else ""
        marker = " \033[1;36m◀ current\033[0m" if s.session_id == current_id else ""
        lines.append(
            f"  \033[1;33m{i:>2}\033[0m) {s.session_id}  "
            f"({s.turn_count} turns, {age} ago){desc}{marker}"
        )
    return "\n".join(lines)


def _cmd_compact(args, agent, config) -> str:
    """Force context compaction on current session."""
    try:
        state = agent.get_state(config)
        if state and state.values:
            messages = state.values.get("messages", [])
            from atom.layers.context_compaction import ContextCompactor
            compactor = ContextCompactor()
            # Force compaction by using a low threshold
            total_chars = sum(len(getattr(m, "content", str(m)) or "") for m in messages)
            token_est = total_chars // 4
            result = compactor.check_and_compact(messages, model_context_window=max(token_est + 1, 1000))
            if result:
                return f"Compacted {len(messages)} messages → {len(result)} messages (~{token_est} tokens)"
            return f"No compaction needed ({len(messages)} messages, ~{token_est} tokens)"
    except Exception as e:
        return f"Compaction error: {e}"


def _cmd_memory(args, agent, config) -> str:
    if _auto_dream is None:
        return "Memory extraction not available (Auto-Dream not configured)."

    if args.strip() == "clear":
        _auto_dream._memories.clear()
        return "All memories cleared."

    return _auto_dream.format_memories_display()


def _cmd_skill(args, agent, config) -> str:
    """Handle /skill subcommands: list, add, install, remove, reload."""
    if _skill_manager is None:
        return "Skill manager not available."

    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else "list"
    subargs = parts[1] if len(parts) > 1 else ""

    if subcmd == "list" or subcmd == "ls":
        return f"\033[1mSkills:\033[0m\n{_skill_manager.format_list()}"

    if subcmd == "add":
        return _skill_add_interactive(subargs)

    if subcmd == "install":
        if not subargs:
            return (
                "Usage:\n"
                "  /skill install <url>                      Install single SKILL.md\n"
                "  /skill install <repo-url> --skill <name>  Install skill from GitHub repo"
            )
        # Parse --skill flag
        skill_name = ""
        source = subargs
        if "--skill" in subargs:
            parts_list = subargs.split("--skill")
            source = parts_list[0].strip()
            skill_name = parts_list[1].strip() if len(parts_list) > 1 else ""
        if not source:
            return "Missing source URL."
        msg, path = _skill_manager.install_skill(source, skill_name=skill_name)
        if path:
            return f"\033[0;32m✓\033[0m {msg} → {path}"
        return f"\033[1;31m✗\033[0m {msg}"

    if subcmd == "remove" or subcmd == "rm":
        if not subargs:
            return "Usage: /skill remove <name>"
        return _skill_manager.remove_skill(subargs)

    if subcmd == "reload":
        return "__skill_reload__"

    return (
        "\033[1mSkill commands:\033[0m\n"
        "  /skill list                                Show installed skills\n"
        "  /skill add <name>                          Create a new skill interactively\n"
        "  /skill install <url>                       Install single SKILL.md\n"
        "  /skill install <repo-url> --skill <name>   Install skill from GitHub repo\n"
        "  /skill remove <name>                       Remove a skill\n"
        "  /skill reload                              Reload skills into current session"
    )


def _skill_add_interactive(name: str) -> str:
    """Interactive skill creation."""
    if _skill_manager is None:
        return "Skill manager not available."

    if not name:
        try:
            name = input("  Skill name: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "Cancelled."
    if not name:
        return "Cancelled."

    try:
        description = input("  Description: ").strip()
        tools = input("  Allowed tools (comma-separated, Enter to skip): ").strip()
        scope = input("  Scope (project/global) [project]: ").strip().lower() or "project"

        print("  Instructions (end with empty line):")
        lines = []
        while True:
            line = input("  > ")
            if not line:
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        return "Cancelled."

    if not lines:
        return "No instructions provided. Cancelled."

    content = "\n".join(lines) + "\n"
    path = _skill_manager.add_skill(name, description or name, content, tools, scope)
    return f"\033[0;32m✓\033[0m Saved to {path}"


def _cmd_tasks(args, agent, config) -> str:
    """Show active sub-agent tasks from the current state."""
    try:
        state = agent.get_state(config)
        if state is None:
            return "No active session."

        if hasattr(state, "tasks") and state.tasks:
            lines = ["\033[1mActive tasks:\033[0m"]
            for i, task in enumerate(state.tasks, 1):
                name = getattr(task, "name", "unknown")
                status = "pending"
                if hasattr(task, "interrupts") and task.interrupts:
                    status = "waiting for approval"
                lines.append(f"  {i}. [{status}] {name}")
            return "\n".join(lines)
        return "No active sub-agent tasks."
    except Exception as e:
        return f"Error checking tasks: {e}"


def _cmd_status(args, agent, config) -> str:
    """Show agent status summary."""
    lines = ["\033[1mAgent Status:\033[0m"]

    # Session info
    session_id = config["configurable"]["thread_id"]
    lines.append(f"  Session: {session_id}")

    # Message/token count
    try:
        state = agent.get_state(config)
        if state and state.values:
            messages = state.values.get("messages", [])
            total_chars = sum(len(getattr(m, "content", str(m)) or "") for m in messages)
            token_est = total_chars // 4
            human_msgs = sum(1 for m in messages if getattr(m, "type", None) == "human")
            ai_msgs = sum(1 for m in messages if getattr(m, "type", None) == "ai")
            tool_msgs = sum(1 for m in messages if getattr(m, "type", None) == "tool")
            lines.append(f"  Messages: {len(messages)} (human: {human_msgs}, ai: {ai_msgs}, tool: {tool_msgs})")
            lines.append(f"  Est. tokens: ~{token_est:,}")
    except Exception:
        lines.append("  Messages: (unable to read state)")

    # Memory count
    if _auto_dream:
        mem_count = len(_auto_dream.get_memories())
        lines.append(f"  Memories: {mem_count}")

    # Session manager info
    if _session_manager:
        session = _session_manager.get_session(session_id)
        if session:
            lines.append(f"  Turns: {session.turn_count}")

    return "\n".join(lines)


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"
