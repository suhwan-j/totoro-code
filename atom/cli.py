"""Atom CLI entry point."""
import os
import sys
import time
import json
import argparse

# Lazy-loaded at first use (saves ~500-800ms startup):
#   from langgraph.types import Command
#   from atom.utils import sanitize_text
#   from atom.status import StatusTracker
#   from atom.diff import format_file_diff, find_line_number, safe_print as _safe_print
Command = None
sanitize_text = None
StatusTracker = None
format_file_diff = None
find_line_number = None
_safe_print = None


def _ensure_imports():
    """Lazy-load heavy dependencies on first actual use."""
    global Command, sanitize_text, StatusTracker, format_file_diff, find_line_number, _safe_print
    if Command is not None:
        return
    from langgraph.types import Command as _Command
    from atom.utils import sanitize_text as _sanitize
    from atom.status import StatusTracker as _Tracker
    from atom.diff import format_file_diff as _ffd, find_line_number as _fln, safe_print as _sp
    Command = _Command
    sanitize_text = _sanitize
    StatusTracker = _Tracker
    format_file_diff = _ffd
    find_line_number = _fln
    _safe_print = _sp


# ─── Pending tool calls for diff display ───
def _banner(config=None, session_id: str = "") -> str:
    import shutil
    from atom.colors import ACCENT, BODY, SECONDARY, DIM, RESET
    width = shutil.get_terminal_size().columns

    model_name = config.model if config else "unknown"
    provider = config.provider if config else "auto"

    A, R = ACCENT, RESET

    # TOTORO block letters
    _T = ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "]
    _O = [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "]
    _Rv = ["██████╗  ", "██╔══██╗ ", "██████╔╝ ", "██╔══██╗ ", "██║  ██║ ", "╚═╝  ╚═╝ "]
    logo_raw = ["".join(l[i] for l in [_T, _O, _T, _O, _Rv, _O]) for i in range(6)]

    # Mascot — Totoro (raw for width calc)
    mascot_raw = [
        "        ███                     ███",
        "       █████                   ████",
        "       █████                   ████",
        "        ███                    ███",
        "          ███████████████████████",
        "       ███████████████████████████",
        "      █████ ▄██▄ ████████ ▄██▄ █████",
        "     ╲████ █▀▀▀█ ████████ █▀▀▀█ ████╱",
        "   ───████ █●░░█ ████████ █●░░█ ████───",
        "      ╱███ ▀██▀ ████▼████ ▀██▀ ████ ╲",
        "        ███████████░▄▄░██████████",
        "      ▐███░░░░░░░░░░░░░░░░░░░░░░███▌",
        "     ███░░░░░▄▀▀▀▄░░░░░░░▄▀▀▀▄░░░░░███",
        "  |██░░░░░▄▀▀▀▄░░░░▄▀▀▀▄░░░░▄▀▀▀▄░░░░██|",
        " ▐|█░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░█|▌",
        " █|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|█",
        "▐█|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|█▌",
        " █|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|█",
        " ▐|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|▌",
        "   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░",
        "    ░░░ ▄██▄ ░░░░░░░░░░░░░░░ ▄██▄ ░░",
        "══════ ██████ ░░░░░░░░░░░░░ ██████ ═════",
        "        ▀███▀               ▀███▀",
    ]
    mascot_w = max(len(l) for l in mascot_raw)
    mascot_h = len(mascot_raw)


    # Right panel content — logo on top, then info
    # Build right-side lines to match mascot height
    right = [""] * mascot_h

    # Vertically center logo + info block within mascot height
    # Logo(6) + blank(1) + info(2) + blank(1) + info(2) = 12 rows
    content_h = 12
    offset = max(0, (mascot_h - content_h) // 2)

    for i, l in enumerate(logo_raw):
        right[offset + i] = f"{A}{l}{R}"

    right[offset + 7]  = f"{SECONDARY}Model{R}     {DIM}│{R}  {BODY}{model_name}{R}"
    right[offset + 8]  = f"{SECONDARY}Provider{R}  {DIM}│{R}  {BODY}{provider}{R}"
    right[offset + 10] = f"{SECONDARY}Session{R}   {DIM}│{R}  {BODY}{session_id}{R}"
    right[offset + 11] = f"{SECONDARY}Commands{R}  {DIM}│{R}  {BODY}/help{R}  {DIM}·{R}  {BODY}Shift+Tab{R}  {DIM}·{R}  {BODY}/exit{R}"

    # Divider character
    div = f" {DIM}│{R} "

    top = f"{DIM}{'━' * width}{R}"
    bot = f"{DIM}{'━' * width}{R}"

    lines = ["", top, ""]
    for i in range(mascot_h):
        ml = mascot_raw[i].ljust(mascot_w)
        lines.append(f" {A}{ml}{R}{div}{right[i]}")
    lines.extend(["", bot, ""])
    return "\n".join(lines)


_pending_file_ops: dict[str, dict] = {}  # tool_call_id -> {name, args}


def _is_slash_command(text: str) -> bool:
    """Check if input is a slash command (not a file path like /home/...)."""
    from atom.commands.registry import get_command_names
    first_word = text.strip().split()[0].lower() if text.strip() else ""
    return any(first_word == cmd or first_word.startswith(cmd + " ") for cmd in get_command_names())


# ─── ANSI formatting helpers (palette-based) ───
from atom.colors import (
    RESET as _RESET, BOLD as _BOLD, DIM as _DIM,
    BLUE as _BLUE, BODY as _BODY, SECONDARY as _SECONDARY,
    AMBER as _AMBER, AMBER_LT as _AMBER_LT,
    COPPER as _RED, WARN as _WARN,
    ACCENT as _ACCENT,
)
_YELLOW = _AMBER
_MAGENTA = _SECONDARY


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
    parser.add_argument("--setup", action="store_true", help="Re-run the setup wizard")
    parser.add_argument("--verbose", action="store_true", help="Show detailed tool results")
    parser.add_argument("task", nargs="*", help="Task to run (alternative to -n)")
    args = parser.parse_args()

    from atom.config.settings import load_config, ensure_api_keys
    ensure_api_keys(force_setup=args.setup)

    if args.setup:
        print("  Setup complete. Starting Atom...\n")

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
    from atom.commands.registry import set_session_manager, set_auto_dream, set_agent_config, set_skill_manager
    set_session_manager(session_manager)
    set_auto_dream(auto_dream)
    set_agent_config(config)

    task = args.non_interactive or (" ".join(args.task) if args.task else None)
    verbose = args.verbose

    if args.list_sessions:
        print(session_manager.format_session_list())
        return

    # Initialize skill manager lazily (only needed for interactive/task mode)
    from atom.skills import SkillManager
    skill_manager = SkillManager(config.project_root)
    set_skill_manager(skill_manager)

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

    session_id = invoke_config['configurable']['thread_id']
    print(_banner(config, session_id=session_id))
    print()

    while True:
        user_input = handler.read_input()

        if user_input is None:
            print("\nBye!")
            break

        if not user_input:
            continue

        # Handle /mode command
        if user_input.strip().lower() == "/mode":
            handler.cycle_mode()
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

            # Handle /skill reload
            if result == "__skill_reload__":
                print(f"{_DIM}  Reloading skills...{_RESET}", flush=True)
                from atom.core.agent import create_atom_agent
                try:
                    agent, _, _, auto_dream = create_atom_agent(config)
                    from atom.commands.registry import set_auto_dream
                    set_auto_dream(auto_dream)
                    print(f"  {_BOLD}Skills reloaded.{_RESET}")
                except Exception as e:
                    print(f"{_RED}  Reload failed: {e}{_RESET}")
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

        print()  # spacing after user input
        _stream_with_hitl(
            agent, user_input, invoke_config,
            auto_approve=handler.is_auto_approve,
            verbose=verbose,
            handler=handler,
        )
        print()  # spacing before next prompt


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

        # Persist model choice to settings.json
        _persist_model_to_settings(new_model, config.project_root)

        return agent
    except Exception as e:
        # Rollback
        config.model = old_model
        config.provider = old_provider
        print(f"{_RED}  Failed to switch model: {e}{_RESET}")
        print(f"  Keeping current model: {old_model}")
        return None


def _persist_model_to_settings(model_name: str, project_root: str):
    """Save selected model to .atom/settings.json so it persists across sessions."""
    import json
    from pathlib import Path
    settings_path = Path(project_root) / ".atom" / "settings.json"
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                data = json.load(f)
            data["model"] = model_name
            with open(settings_path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except (json.JSONDecodeError, OSError):
            pass


def _stream_with_hitl(agent, user_input: str, config: dict, auto_approve: bool = False,
                      verbose: bool = False, handler=None):
    """Stream agent response with HITL interrupt handling and live status dashboard."""
    _ensure_imports()
    from atom.orchestrator import set_tracker, set_pane_manager, RenderThread
    from atom.pane import PaneManager
    from atom.hotkey import HotkeyListener

    tracker = StatusTracker()
    set_tracker(tracker)

    # PaneManager for detailed subagent tracking
    pane_manager = PaneManager()
    set_pane_manager(pane_manager)
    tracker._pane_manager = pane_manager  # Link for rendering

    # Hotkey listener for mode switching during streaming
    hotkey = None
    if handler:
        hotkey = HotkeyListener(handler)

    # Start background render thread for real-time dashboard updates
    render_thread = RenderThread(tracker, interval=0.5)
    render_thread.start()

    input_payload = {"messages": [{"role": "user", "content": user_input}]}

    max_hitl_rounds = 20  # Safety limit to prevent infinite loops
    try:
        for _round in range(max_hitl_rounds):
            # Activate hotkey listener during streaming
            if hotkey:
                hotkey.activate()

            interrupt_info = _do_stream(agent, input_payload, config, tracker=tracker, verbose=verbose)

            # Deactivate hotkey before HITL or exit
            if hotkey:
                hotkey.deactivate()

            # Check if mode changed during streaming
            if handler and handler.is_auto_approve:
                auto_approve = True

            if interrupt_info is None:
                break

            # Clear dashboard before HITL prompt
            render_thread.shutdown()
            render_thread.join(timeout=1)
            tracker._clear_previous()
            tracker._last_panel_lines = 0

            if auto_approve:
                decisions = [{"type": "approve"} for _ in _flatten_decisions(interrupt_info)]
                signal = _HITL_CONTINUE
            else:
                decisions, signal = _collect_hitl_decisions(interrupt_info)

            # Handle abort — stop the turn immediately
            if signal == _HITL_ABORT:
                resume_value = _build_resume_payload(interrupt_info, decisions)
                try:
                    for _ in agent.stream(Command(resume=resume_value), config=config, stream_mode="updates"):
                        pass
                except Exception:
                    pass
                break

            # Handle approve-all — auto-approve for rest of this turn
            if signal == _HITL_APPROVE_ALL:
                auto_approve = True
                if handler:
                    handler.mode = "auto-approve"

            input_payload = Command(resume=_build_resume_payload(interrupt_info, decisions))

            # Restart render thread for next iteration
            render_thread = RenderThread(tracker, interval=2.0)
            render_thread.start()

    finally:
        if hotkey:
            hotkey.shutdown()
        render_thread.shutdown()
        render_thread.join(timeout=1)

    # Show final summary
    tracker.render_final_summary()


def _do_stream(agent, input_payload, config: dict, tracker: StatusTracker, verbose: bool = False) -> list | None:
    """Stream agent response with token-level AI text streaming.

    Uses stream_mode=["messages", "updates"]:
      - "messages": token-by-token AI text + tool call chunks (real-time output)
      - "updates": node-level outputs for tool results, todos, diffs
    """

    got_ai_response = False
    event_count = 0
    had_error = False
    ai_header_printed = False
    streaming_ai_id: str | None = None  # Track which AI message is being streamed

    def _clear_and_print(text: str, **kwargs):
        """Clear dashboard and print below it."""
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0
            _safe_print(text, flush=True, **kwargs)
            tracker._mark_dirty()

    try:
        for event in agent.stream(input_payload, config=config, stream_mode=["messages", "updates"]):
            event_count += 1

            # Multi-mode events are 2-tuples: (mode, data)
            if not isinstance(event, tuple) or len(event) != 2:
                continue
            mode, data = event

            # ── "messages" mode: token-by-token streaming ──
            if mode == "messages":
                msg_chunk, _metadata = data
                msg_type = getattr(msg_chunk, "type", None)

                # AI token streaming
                if msg_type in ("ai", "AIMessageChunk"):
                    content = getattr(msg_chunk, "content", "")
                    tool_call_chunks = getattr(msg_chunk, "tool_call_chunks", [])

                    # Text content — stream token by token
                    if content:
                        text = content if isinstance(content, str) else ""
                        if isinstance(content, list):
                            text = "".join(
                                b.get("text", "") if isinstance(b, dict) else str(b)
                                for b in content
                            )
                        if text:
                            got_ai_response = True
                            with tracker._lock:
                                tracker._got_ai_text = True
                                tracker._clear_previous()
                                tracker._last_panel_lines = 0
                                if not ai_header_printed:
                                    _safe_print(f"{_DIM}● > {_RESET}", end="", flush=True)
                                    ai_header_printed = True
                                    streaming_ai_id = getattr(msg_chunk, "id", None)
                                _safe_print(text, end="", flush=True)
                                tracker._mark_dirty()

                    # Tool call chunks — mark as active (but don't track args here;
                    # messages mode sends args as partial JSON strings, not dicts.
                    # Full args are captured from the "updates" stream below.)
                    if tool_call_chunks:
                        got_ai_response = True

                # Tool result messages — handle diffs and errors
                elif msg_type == "tool":
                    name = getattr(msg_chunk, "name", "tool")
                    tool_content = sanitize_text(str(getattr(msg_chunk, "content", "")))
                    tool_call_id = getattr(msg_chunk, "tool_call_id", None)
                    tracker.on_tool_end(name, tool_content[:200])

                    # End previous AI streaming line
                    if ai_header_printed and streaming_ai_id:
                        ai_header_printed = False
                        streaming_ai_id = None
                        _safe_print("", flush=True)  # newline

                    is_error = "error" in tool_content.lower()[:100]

                    if tool_call_id and tool_call_id in _pending_file_ops and not is_error:
                        op = _pending_file_ops.pop(tool_call_id)
                        diff_text = format_file_diff(op["name"], op.get("args", {}), op.get("start_line"))
                        if diff_text:
                            _clear_and_print(diff_text)
                    elif verbose or is_error:
                        display = tool_content[:500 if verbose else 300]
                        color = _MAGENTA if verbose else _RED
                        prefix = f"  <- {name}:" if verbose else f"  [error] {name}:"
                        _clear_and_print(f"{color}{prefix} {display}{_RESET}")

                continue

            # ── "updates" mode: node-level outputs (todos, full tool args) ──
            if mode == "updates" and isinstance(data, dict):
                for node_name, node_output in data.items():
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

                    # Process complete messages from updates (tool calls with full args for diffs)
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
                        if msg_type == "ai":
                            tool_calls = getattr(msg, "tool_calls", [])
                            for tc in tool_calls:
                                tc_name = tc.get("name", "")
                                tc_id = tc.get("id")
                                # Store full args for diff display (messages mode only has chunks)
                                if tc_name in ("edit_file", "write_file") and tc_id:
                                    op_args = tc.get("args", {})
                                    line_num = None
                                    if tc_name == "edit_file":
                                        line_num = find_line_number(
                                            op_args.get("file_path", ""),
                                            op_args.get("old_string", ""),
                                        )
                                    _pending_file_ops[tc_id] = {
                                        "name": tc_name,
                                        "args": op_args,
                                        "start_line": line_num,
                                    }
                            if tool_calls:
                                got_ai_response = True

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

    # End streaming line if still open
    if ai_header_printed:
        _safe_print("", flush=True)

    if not got_ai_response:
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0
        if event_count == 0:
            _safe_print(f"{_YELLOW}(No events from agent — model may not be responding. Try --verbose){_RESET}", flush=True)
        else:
            _safe_print(f"{_YELLOW}(Got {event_count} events but no AI text — model may not support tool calling. Try --verbose){_RESET}", flush=True)

    # Check for pending interrupts — but NOT if we hit an error
    if not had_error:
        try:
            state = agent.get_state(config)
            if state and state.next:
                if hasattr(state, "tasks") and state.tasks:
                    return list(state.tasks)
        except Exception:
            pass

    return None


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


def _build_resume_payload(interrupts, decisions: list[dict]):
    """Build the resume payload, handling single and multiple interrupts.

    For a single interrupt: resume={"decisions": decisions}
    For multiple interrupts: resume={interrupt_id: {"decisions": [decision]}, ...}
    """
    # Check if interrupts have IDs (newer LangGraph API)
    interrupt_ids = []
    for task in interrupts:
        if hasattr(task, "interrupts") and task.interrupts:
            for intr in task.interrupts:
                intr_id = getattr(intr, "id", None) or getattr(intr, "interrupt_id", None)
                if intr_id:
                    interrupt_ids.append(intr_id)

    if len(interrupt_ids) > 1 and len(decisions) == len(interrupt_ids):
        # Multiple interrupts — map each decision to its interrupt ID
        return {
            iid: {"decisions": [dec]}
            for iid, dec in zip(interrupt_ids, decisions)
        }

    # Single interrupt or no IDs — use flat format
    return {"decisions": decisions}


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


_HITL_CONTINUE = "continue"   # normal flow
_HITL_APPROVE_ALL = "approve_all"  # auto-approve remaining
_HITL_ABORT = "abort"          # stop the turn


def _collect_hitl_decisions(interrupts) -> tuple[list[dict], str]:
    """Prompt user for HITL decisions.

    Returns:
        (decisions, signal) where signal is one of:
        - _HITL_CONTINUE: process normally
        - _HITL_APPROVE_ALL: auto-approve all remaining in this turn
        - _HITL_ABORT: reject all and stop the turn
    """
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

            # Show diff format for file operations, generic format for others
            if tool_name in ("edit_file", "write_file") and isinstance(tool_args, dict):
                line_num = None
                if tool_name == "edit_file":
                    line_num = find_line_number(
                        tool_args.get("file_path", ""),
                        tool_args.get("old_string", ""),
                    )
                diff_text = format_file_diff(tool_name, tool_args, line_num)
                if diff_text:
                    print(f"\n{_YELLOW}[APPROVAL REQUIRED]{_RESET}")
                    _safe_print(diff_text)
                else:
                    print(f"\n{_YELLOW}[APPROVAL REQUIRED]{_RESET} {_BOLD}{tool_name}{_RESET}")
            else:
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
            print(f"  {_BOLD}(a){_RESET}pprove / {_BOLD}(A){_RESET}pprove all / {_BOLD}(r){_RESET}eject / {_BOLD}(x){_RESET} abort / {_BOLD}(e){_RESET}dit ?")

            try:
                choice = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n  {_RED}✗ Aborted{_RESET}")
                decisions.append({"type": "reject"})
                return decisions, _HITL_ABORT

            # Approve all remaining
            if choice in ("A", "approve all", "aa"):
                print(f"  {_YELLOW}⚡ Auto-approve enabled for this turn{_RESET}")
                decisions.append({"type": "approve"})
                remaining = len(action_requests) - (action_requests.index(action) + 1)
                for _ in range(remaining):
                    decisions.append({"type": "approve"})
                return decisions, _HITL_APPROVE_ALL

            # Abort — reject and stop turn
            if choice.lower() in ("x", "abort", "q", "quit", "stop"):
                print(f"  {_RED}✗ Aborted — stopping this turn{_RESET}")
                decisions.append({"type": "reject"})
                return decisions, _HITL_ABORT

            # Approve
            if choice.lower() in ("a", "approve", "y", "yes", ""):
                decisions.append({"type": "approve"})

            # Edit
            elif choice.lower() in ("e", "edit"):
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

            # Reject
            else:
                print(f"  {_RED}✗ Rejected{_RESET}")
                decisions.append({"type": "reject"})

    return decisions, _HITL_CONTINUE


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
