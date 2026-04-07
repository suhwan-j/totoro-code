"""Atom CLI entry point."""
import sys
import time
import json
import argparse

from langgraph.types import Command

from atom.utils import sanitize_text
from atom.status import StatusTracker


def _banner() -> str:
    C = "\033[1;36m"   # cyan
    Y = "\033[1;33m"   # yellow
    W = "\033[0;37m"   # white
    D = "\033[0;90m"   # dim
    R = "\033[0m"      # reset
    lines = [
        f"",
        f"{C}        .     *    .        .   *     .        ",
        f"{C}    .        ___         .        .            ",
        f"{C}        .  /     \\  *        .          .      ",
        f"{C}   *     ./  {Y}@ @{C}  \\.    .        *       .    ",
        f"{C}    .   /  \\  {Y}'{C}  /  \\        .               ",
        f"{C}       |    '---'    |  .    {Y} ____  ______  ____  __  __{C}",
        f"{C}    .  |  \\       /  |       {Y}|    ||__  __||    ||  \\/  |{C}",
        f"{C}       \\   '.___.'   /    .  {Y}| || |  |  |  | || || |\\/| |{C}",
        f"{C}    .   \\___________/        {Y}|_||_|  |  |  |_||_||_|  |_|{C}",
        f"{C}         /  | | |  \\    *                              ",
        f"{C}    *   /   | | |   \\       {W} Advanced CLI Coding Agent{C}",
        f"{C}       '----' ' '----'  .   {D} Powered by suhwan-j{R}",
        f"",
    ]
    return "\n".join(lines)


# ─── ANSI formatting helpers ───
_DIM = "\033[0;90m"
_BLUE = "\033[0;34m"
_MAGENTA = "\033[0;35m"
_YELLOW = "\033[1;33m"
_BOLD = "\033[1m"
_RED = "\033[1;31m"
_RESET = "\033[0m"


def main():
    parser = argparse.ArgumentParser(
        description="Atom: Advanced CLI Coding Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  atom                              # Interactive mode
  atom -n "fix the login bug"      # Non-interactive single task
  atom --auto-approve              # Skip all approval prompts
  atom --model anthropic/claude-sonnet-4-5  # Use specific model
  atom --resume <session-id>       # Resume a previous session
  atom --list-sessions             # List all sessions
""",
    )
    parser.add_argument("-n", "--non-interactive", type=str, metavar="TASK", help="Run single task non-interactively")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all tool executions (no HITL)")
    parser.add_argument("--model", type=str, help="Override model name")
    parser.add_argument("--resume", type=str, metavar="SESSION_ID", help="Resume a previous session")
    parser.add_argument("--list-sessions", action="store_true", help="List all sessions")
    parser.add_argument("--verbose", action="store_true", help="Show detailed tool results")
    parser.add_argument("task", nargs="*", help="Task to run (alternative to -n)")
    args = parser.parse_args()

    from atom.config.settings import load_config, ensure_api_keys
    ensure_api_keys()

    cli_overrides = {}
    if args.auto_approve:
        cli_overrides["permissions"] = {"mode": "auto_approve"}
    if args.model:
        cli_overrides["model"] = args.model

    config = load_config(cli_overrides=cli_overrides)

    from atom.core.agent import create_atom_agent
    agent, checkpointer, store, auto_dream = create_atom_agent(config)

    # Initialize session manager
    from atom.session.manager import SessionManager
    session_manager = SessionManager(checkpointer=checkpointer)

    # Inject into command registry
    from atom.commands.registry import set_session_manager, set_auto_dream
    set_session_manager(session_manager)
    set_auto_dream(auto_dream)

    task = args.non_interactive or (" ".join(args.task) if args.task else None)
    verbose = args.verbose

    if args.list_sessions:
        print(session_manager.format_session_list())
        return

    if task:
        session_info = session_manager.create_session(description=task[:50])
        invoke_config = session_manager.get_invoke_config(session_info.session_id)
        _stream_with_hitl(agent, task, invoke_config, auto_approve=args.auto_approve, verbose=verbose)
    elif args.resume:
        from atom.session.restore import restore_session
        invoke_config = restore_session(agent, args.resume, session_manager)
        if invoke_config is None:
            print("Could not restore session. Starting new session.")
            session_info = session_manager.create_session()
            invoke_config = session_manager.get_invoke_config(session_info.session_id)
        _run_interactive(agent, invoke_config, session_manager=session_manager,
                        auto_approve=args.auto_approve, verbose=verbose)
    else:
        session_info = session_manager.create_session()
        invoke_config = session_manager.get_invoke_config(session_info.session_id)
        _run_interactive(agent, invoke_config, session_manager=session_manager,
                        auto_approve=args.auto_approve, verbose=verbose)


def _run_interactive(agent, invoke_config: dict, session_manager=None,
                     auto_approve: bool = False, verbose: bool = False):
    """Interactive mode main loop."""
    from atom.commands.registry import handle_slash_command
    from atom.input import InputHandler

    handler = InputHandler(initial_mode="auto-approve" if auto_approve else "default")

    print(_banner())
    print(f"  Session: {invoke_config['configurable']['thread_id']}")
    print(f"  Type \033[1m/help\033[0m for commands, \033[1mShift+Tab\033[0m to change mode, \033[1m/exit\033[0m to quit.\n")

    while True:
        user_input = handler.read_input()

        if user_input is None:
            print("\nBye!")
            break

        if not user_input:
            continue

        # Handle /mode command
        if user_input.strip().lower() == "/mode":
            new_mode = handler.cycle_mode()
            from atom.input import MODE_LABELS
            label = MODE_LABELS.get(new_mode, new_mode)
            print(f"  Mode: {label}")
            continue

        if user_input.startswith("/"):
            result = handle_slash_command(user_input, agent, invoke_config)
            if result == "__exit__":
                print("Bye!")
                break
            if result:
                print(result)
            continue

        # Plan-only mode: inject planning constraint
        if handler.is_plan_only:
            user_input = (
                f"{user_input}\n\n"
                "[SYSTEM: Plan-only mode is active. Use write_todos to create a plan. "
                "Do NOT execute any file operations or shell commands. Only plan.]"
            )

        # Track turn in session manager
        if session_manager:
            session_id = invoke_config["configurable"]["thread_id"]
            session_manager.update_activity(session_id)

        _stream_with_hitl(
            agent, user_input, invoke_config,
            auto_approve=handler.is_auto_approve,
            verbose=verbose,
        )
        print()


def _stream_with_hitl(agent, user_input: str, config: dict, auto_approve: bool = False, verbose: bool = False):
    """Stream agent response with HITL interrupt handling and live status dashboard."""
    from atom.orchestrator import set_tracker, RenderThread

    tracker = StatusTracker()
    set_tracker(tracker)

    # Start background render thread for real-time dashboard updates
    render_thread = RenderThread(tracker, interval=0.5)
    render_thread.start()

    input_payload = {"messages": [{"role": "user", "content": user_input}]}

    try:
        while True:
            interrupt_info = _do_stream(agent, input_payload, config, tracker=tracker, verbose=verbose)

            if interrupt_info is None:
                break

            # Clear dashboard before HITL prompt
            render_thread.shutdown()
            render_thread.join(timeout=1)
            tracker._clear_previous()
            tracker._last_panel_lines = 0

            if auto_approve:
                decisions = [{"type": "approve"} for _ in _flatten_decisions(interrupt_info)]
            else:
                decisions = _collect_hitl_decisions(interrupt_info)

            input_payload = Command(resume={"decisions": decisions})

            # Restart render thread for next iteration
            render_thread = RenderThread(tracker, interval=0.5)
            render_thread.start()

    finally:
        render_thread.shutdown()
        render_thread.join(timeout=1)

    # Show final summary
    tracker.render_final_summary()


def _do_stream(agent, input_payload, config: dict, tracker: StatusTracker, verbose: bool = False) -> list | None:
    """Stream agent response. Dashboard is rendered by background RenderThread."""

    seen_msg_ids: set[str] = set()

    try:
        for event in agent.stream(input_payload, config=config, stream_mode="updates"):
            for node_name, node_output in event.items():
                if not isinstance(node_output, dict):
                    continue

                # Track todo updates from state
                todos_in_state = node_output.get("todos")
                if todos_in_state is not None:
                    tracker.on_todos_updated(
                        [t if isinstance(t, dict) else {"content": str(t), "status": "pending"} for t in todos_in_state]
                    )

                raw_messages = node_output.get("messages")
                if raw_messages is None:
                    continue

                if hasattr(raw_messages, "value"):
                    messages = raw_messages.value
                elif isinstance(raw_messages, list):
                    messages = raw_messages
                else:
                    continue

                for msg in messages:
                    msg_type = getattr(msg, "type", None)
                    msg_id = getattr(msg, "id", None) or id(msg)
                    if msg_id in seen_msg_ids:
                        continue
                    seen_msg_ids.add(msg_id)

                    if msg_type == "ai":
                        for tc in getattr(msg, "tool_calls", []):
                            tracker.on_tool_start(tc.get("name", "unknown"), tc.get("args", {}))

                        content = msg.content
                        if content:
                            text = _extract_text(content)
                            if text:
                                # Pause render thread, print text, resume
                                with tracker._lock:
                                    tracker._clear_previous()
                                    tracker._last_panel_lines = 0
                                _safe_print(text, flush=True)

                    elif msg_type == "tool":
                        name = getattr(msg, "name", "tool")
                        tool_content = sanitize_text(str(msg.content))
                        tracker.on_tool_end(name, tool_content[:200])

                        is_error = "error" in tool_content.lower()[:100]
                        if verbose or is_error:
                            with tracker._lock:
                                tracker._clear_previous()
                                tracker._last_panel_lines = 0
                            display = tool_content[:500 if verbose else 300]
                            color = _MAGENTA if verbose else _RED
                            prefix = f"  <- {name}:" if verbose else f"  [error] {name}:"
                            _safe_print(f"{color}{prefix} {display}{_RESET}", flush=True)

    except Exception as e:
        with tracker._lock:
            tracker._clear_previous()
        tracker._last_panel_lines = 0
        try:
            result = agent.invoke(input_payload, config=config)
            messages = result.get("messages", [])
            for msg in reversed(messages):
                if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                    text = _extract_text(msg.content)
                    if text:
                        _safe_print(text, flush=True)
                    break
        except Exception as e2:
            _safe_print(f"\n{_RED}Error: {sanitize_text(str(e2))}{_RESET}", file=sys.stderr)

    # Check for pending interrupts
    try:
        state = agent.get_state(config)
        if state and state.next:
            if hasattr(state, "tasks") and state.tasks:
                return list(state.tasks)
    except Exception:
        pass

    return None


def _safe_print(text: str, **kwargs):
    """Print with surrogate-safe encoding."""
    try:
        print(sanitize_text(text), **kwargs)
    except UnicodeEncodeError:
        safe = text.encode("utf-8", errors="replace").decode("utf-8")
        print(safe, **kwargs)


def _extract_text(content) -> str:
    """Extract text from various content formats."""
    if isinstance(content, str):
        return sanitize_text(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return sanitize_text("".join(parts))
    return sanitize_text(str(content))


def _flatten_decisions(interrupts) -> list:
    """Count total number of decisions needed from interrupt list."""
    count = []
    for task in interrupts:
        interrupt_value = None
        if hasattr(task, "interrupts") and task.interrupts:
            interrupt_value = task.interrupts[0].value if hasattr(task.interrupts[0], "value") else task.interrupts[0]
        elif hasattr(task, "value"):
            interrupt_value = task.value
        else:
            interrupt_value = task

        if isinstance(interrupt_value, dict):
            actions = interrupt_value.get("action_requests", [interrupt_value])
            count.extend(actions)
        else:
            count.append(interrupt_value)
    return count


def _collect_hitl_decisions(interrupts) -> list[dict]:
    """Prompt user for HITL decisions."""
    decisions = []

    for task in interrupts:
        interrupt_value = None
        if hasattr(task, "interrupts") and task.interrupts:
            interrupt_value = task.interrupts[0].value if hasattr(task.interrupts[0], "value") else task.interrupts[0]
        elif hasattr(task, "value"):
            interrupt_value = task.value
        else:
            interrupt_value = task

        action_requests = []
        if isinstance(interrupt_value, dict):
            action_requests = interrupt_value.get("action_requests", [])
            if not action_requests:
                action_requests = [interrupt_value]

        if not action_requests:
            decisions.append({"type": "approve"})
            continue

        for action in action_requests:
            if isinstance(action, dict):
                tool_name = action.get("name", action.get("tool", "unknown"))
                tool_args = action.get("args", action.get("input", {}))
            else:
                tool_name = str(action)
                tool_args = {}

            print(f"\n{_YELLOW}[APPROVAL REQUIRED]{_RESET} {_BOLD}{tool_name}{_RESET}")
            if tool_args:
                if isinstance(tool_args, dict):
                    for k, v in tool_args.items():
                        v_str = sanitize_text(str(v))
                        if len(v_str) > 200:
                            v_str = v_str[:200] + "..."
                        print(f"  {k}: {v_str}")
                else:
                    print(f"  {sanitize_text(str(tool_args))}")
            print(f"  {_BOLD}(a){_RESET}pprove / {_BOLD}(r){_RESET}eject / {_BOLD}(e){_RESET}dit ?")

            try:
                choice = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                decisions.append({"type": "reject"})
                continue

            if choice in ("a", "approve", "y", "yes", ""):
                decisions.append({"type": "approve"})
            elif choice in ("e", "edit"):
                edited = input("  Enter edited args (JSON): ").strip()
                try:
                    edited_args = json.loads(edited)
                    decisions.append({
                        "type": "edit",
                        "edited_action": {"name": tool_name, "args": edited_args},
                    })
                except json.JSONDecodeError:
                    print("  Invalid JSON, rejecting.")
                    decisions.append({"type": "reject"})
            else:
                decisions.append({"type": "reject"})

    return decisions
