"""Slash command registry and handler."""
import time

# These are injected by CLI after agent creation
_session_manager = None
_auto_dream = None


def set_session_manager(manager):
    """Inject session manager for /session commands."""
    global _session_manager
    _session_manager = manager


def set_auto_dream(extractor):
    """Inject auto-dream extractor for /memory commands."""
    global _auto_dream
    _auto_dream = extractor


def handle_slash_command(user_input: str, agent, invoke_config: dict) -> str | None:
    """Parse and execute a slash command. Returns output string, '__exit__', or None."""
    parts = user_input.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    handlers = {
        "/help": _cmd_help,
        "/exit": _cmd_exit,
        "/quit": _cmd_exit,
        "/clear": _cmd_clear,
        "/model": _cmd_model,
        "/session": _cmd_session,
        "/sessions": _cmd_sessions,
        "/compact": _cmd_compact,
        "/memory": _cmd_memory,
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
  /clear             Clear conversation (start new session)
  /model             Show current model info
  /session           Show current session ID
  /sessions          List all sessions
  /compact           Force context compaction
  /memory            Show extracted memories
  /memory clear      Clear all memories
  /tasks             Show active sub-agent tasks
  /status            Show agent status (turns, tokens, memories)

{mode_help}"""


def _cmd_exit(args, agent, config) -> str:
    return "__exit__"


def _cmd_clear(args, agent, config) -> str:
    new_session = f"session-{int(time.time())}"
    config["configurable"]["thread_id"] = new_session
    if _session_manager:
        _session_manager.create_session(new_session)
    return f"Conversation cleared. New session: {new_session}"


def _cmd_model(args, agent, config) -> str:
    # Try to extract model info from the agent
    model_info = "unknown"
    try:
        # CompiledStateGraph doesn't directly expose model, but we can check config
        if hasattr(agent, "config") and agent.config:
            model_info = str(agent.config.get("model", "unknown"))
    except Exception:
        pass
    return f"Current model: {model_info}"


def _cmd_session(args, agent, config) -> str:
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

    return "\n".join(info_lines)


def _cmd_sessions(args, agent, config) -> str:
    if _session_manager is None:
        return "Session manager not available."
    return _session_manager.format_session_list()


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
