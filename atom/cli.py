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


def _is_slash_command(text: str) -> bool:
    """Check if input is a slash command (not a file path like /home/...)."""
    from atom.commands.registry import get_command_names
    first_word = text.strip().split()[0].lower() if text.strip() else ""
    return any(first_word == cmd or first_word.startswith(cmd + " ") for cmd in get_command_names())


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
  atom --provider vllm --model meta-llama/Llama-3.1-70B  # Use vLLM
  atom --resume <session-id>       # Resume a previous session
  atom --list-sessions             # List all sessions
""",
    )
    parser.add_argument("-n", "--non-interactive", type=str, metavar="TASK", help="Run single task non-interactively")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all tool executions (no HITL)")
    parser.add_argument("--model", type=str, help="Override model name")
    parser.add_argument("--provider", type=str, choices=["auto", "openrouter", "anthropic", "openai", "vllm"],
                        help="LLM provider (default: auto-detect from env)")
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
    if args.provider:
        cli_overrides["provider"] = args.provider

    config = load_config(cli_overrides=cli_overrides)

    from atom.core.agent import create_atom_agent
    agent, checkpointer, store, auto_dream = create_atom_agent(config)

    # Initialize session manager
    from atom.session.manager import SessionManager
    session_manager = SessionManager(checkpointer=checkpointer)

    # Inject into command registry
    from atom.commands.registry import set_session_manager, set_auto_dream, set_agent_config
    set_session_manager(session_manager)
    set_auto_dream(auto_dream)
    set_agent_config(config)

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
                        auto_approve=args.auto_approve, verbose=verbose, config=config)
    else:
        session_info = session_manager.create_session()
        invoke_config = session_manager.get_invoke_config(session_info.session_id)
        _run_interactive(agent, invoke_config, session_manager=session_manager,
                        auto_approve=args.auto_approve, verbose=verbose, config=config)


def _run_interactive(agent, invoke_config: dict, session_manager=None,
                     auto_approve: bool = False, verbose: bool = False,
                     config=None):
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

        if _is_slash_command(user_input):
            result = handle_slash_command(user_input, agent, invoke_config)
            if result == "__exit__":
                print("Bye!")
                break

            # Handle /model hot-swap
            if result and result.startswith("__model_change__:"):
                agent = _handle_model_change(result, config, session_manager)
                if agent is None:
                    continue
                continue

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


def _handle_model_change(sentinel: str, config, session_manager):
    """Parse model change sentinel and rebuild agent. Returns new agent or None on error."""
    parts = sentinel.split(":", maxsplit=2)
    # parts = ["__model_change__", model_name] or ["__model_change__", model_name, provider]
    new_model = parts[1] if len(parts) > 1 else None
    new_provider = parts[2] if len(parts) > 2 else None

    if not new_model:
        print(f"{_RED}  Error: no model name provided.{_RESET}")
        return None

    if config is None:
        print(f"{_RED}  Error: config not available for model switching.{_RESET}")
        return None

    old_model = config.model
    old_provider = config.provider
    config.model = new_model
    if new_provider:
        config.provider = new_provider

    print(f"{_DIM}  Switching model: {old_model} → {new_model}...{_RESET}", flush=True)

    try:
        from atom.core.agent import create_atom_agent
        agent, _, _, auto_dream = create_atom_agent(config)

        from atom.commands.registry import set_auto_dream, set_agent_config
        set_auto_dream(auto_dream)
        set_agent_config(config)

        provider_display = new_provider or config.provider
        print(f"  {_BOLD}Model switched to: {new_model}{_RESET} (provider: {provider_display})")
        return agent
    except Exception as e:
        # Rollback
        config.model = old_model
        config.provider = old_provider
        print(f"{_RED}  Failed to switch model: {e}{_RESET}")
        print(f"  Keeping current model: {old_model}")
        return None


def _stream_with_hitl(agent, user_input: str, config: dict, auto_approve: bool = False, verbose: bool = False):
    """Stream agent response with HITL interrupt handling and live status dashboard."""
    from atom.orchestrator import set_tracker, RenderThread

    tracker = StatusTracker()
    set_tracker(tracker)

    # Start background render thread for real-time dashboard updates
    render_thread = RenderThread(tracker, interval=2.0)
    render_thread.start()

    input_payload = {"messages": [{"role": "user", "content": user_input}]}

    max_hitl_rounds = 20  # Safety limit to prevent infinite loops
    try:
        for _round in range(max_hitl_rounds):
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
            render_thread = RenderThread(tracker, interval=2.0)
            render_thread.start()

    finally:
        render_thread.shutdown()
        render_thread.join(timeout=1)

    # Show final summary
    tracker.render_final_summary()


def _do_stream(agent, input_payload, config: dict, tracker: StatusTracker, verbose: bool = False) -> list | None:
    """Stream agent response. Dashboard is rendered by background RenderThread."""

    # Collect IDs of messages already in state to avoid reprinting them
    seen_msg_ids: set[str] = set()
    try:
        state = agent.get_state(config)
        for msg in state.values.get("messages", []):
            msg_id = getattr(msg, "id", None)
            if msg_id:
                seen_msg_ids.add(msg_id)
    except Exception:
        pass

    got_ai_response = False
    event_count = 0
    had_error = False

    try:
        for event in agent.stream(input_payload, config=config, stream_mode="updates"):
            event_count += 1
            for node_name, node_output in event.items():
                if verbose:
                    out_keys = list(node_output.keys()) if isinstance(node_output, dict) else type(node_output).__name__
                    _safe_print(f"{_DIM}  [debug] node={node_name} keys={out_keys}{_RESET}", flush=True)

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
                        tool_calls = getattr(msg, "tool_calls", [])
                        for tc in tool_calls:
                            tracker.on_tool_start(tc.get("name", "unknown"), tc.get("args", {}))

                        # Model is working if it produces tool calls OR text
                        if tool_calls:
                            got_ai_response = True

                        content = msg.content
                        if content:
                            text = _extract_text(content)
                            if text:
                                got_ai_response = True
                                with tracker._lock:
                                    tracker._clear_previous()
                                    tracker._last_panel_lines = 0
                                    _safe_print(text, flush=True)
                                    tracker._mark_dirty()

                    elif msg_type == "tool":
                        name = getattr(msg, "name", "tool")
                        tool_content = sanitize_text(str(msg.content))
                        tracker.on_tool_end(name, tool_content[:200])

                        is_error = "error" in tool_content.lower()[:100]
                        if verbose or is_error:
                            display = tool_content[:500 if verbose else 300]
                            color = _MAGENTA if verbose else _RED
                            prefix = f"  <- {name}:" if verbose else f"  [error] {name}:"
                            with tracker._lock:
                                tracker._clear_previous()
                                tracker._last_panel_lines = 0
                                _safe_print(f"{color}{prefix} {display}{_RESET}", flush=True)
                                tracker._mark_dirty()

    except Exception as e:
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0
        had_error = True
        _safe_print(f"\n{_RED}[Stream error] {sanitize_text(str(e))}{_RESET}", flush=True)

        # Fallback: try non-streaming invoke
        try:
            result = agent.invoke(input_payload, config=config)
            messages = result.get("messages", [])
            for msg in reversed(messages):
                if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                    text = _extract_text(msg.content)
                    if text:
                        got_ai_response = True
                        _safe_print(text, flush=True)
                    break
        except Exception as e2:
            _safe_print(f"{_RED}[Fallback error] {sanitize_text(str(e2))}{_RESET}", flush=True)

    if not got_ai_response:
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0
        if event_count == 0:
            _safe_print(f"{_YELLOW}(No events from agent — model may not be responding. Try --verbose){_RESET}", flush=True)
        else:
            _safe_print(f"{_YELLOW}(Got {event_count} events but no AI text — model may not support tool calling. Try --verbose){_RESET}", flush=True)

    # Check for pending interrupts — but NOT if we hit an error
    # (retrying after an error like context-too-long causes infinite loops)
    if not had_error:
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
                user_edit = input("  How to change? > ").strip()
                if not user_edit:
                    decisions.append({"type": "approve"})
                    continue
                edited_args = _apply_natural_language_edit(tool_name, tool_args, user_edit)
                if edited_args is not None:
                    print(f"  {_DIM}Edited args:{_RESET}")
                    for k, v in edited_args.items():
                        v_str = sanitize_text(str(v))
                        if len(v_str) > 200:
                            v_str = v_str[:200] + "..."
                        print(f"    {k}: {v_str}")
                    decisions.append({
                        "type": "edit",
                        "edited_action": {"name": tool_name, "args": edited_args},
                    })
                else:
                    print("  Edit failed, rejecting.")
                    decisions.append({"type": "reject"})
            else:
                decisions.append({"type": "reject"})

    return decisions


def _apply_natural_language_edit(tool_name: str, original_args: dict, user_instruction: str) -> dict | None:
    """Use a lightweight LLM to apply a natural language edit to tool args."""
    from atom.core.models import create_lightweight_model

    model = create_lightweight_model()
    if model is None:
        # Fallback: try parsing as raw JSON
        try:
            return json.loads(user_instruction)
        except json.JSONDecodeError:
            return None

    prompt = (
        f"You are editing the arguments for a tool call.\n"
        f"Tool: {tool_name}\n"
        f"Current args (JSON):\n{json.dumps(original_args, ensure_ascii=False, indent=2)}\n\n"
        f"User's edit instruction: {user_instruction}\n\n"
        f"Return ONLY the updated args as a single JSON object. No explanation, no markdown fences."
    )

    try:
        response = model.invoke(prompt)
        text = response.content.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return json.loads(text)
    except Exception:
        return None
