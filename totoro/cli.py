"""Totoro CLI entry point."""

import os
import sys
import time
import json
import argparse

from totoro.colors import (
    RESET as _RESET,
    BOLD as _BOLD,
    DIM as _DIM,
    BLUE as _BLUE,
    BODY as _BODY,
    SECONDARY as _SECONDARY,
    AMBER as _AMBER,
    AMBER_LT as _AMBER_LT,
    COPPER as _RED,
    WARN as _WARN,
    ACCENT as _ACCENT,
)

_YELLOW = _AMBER
_MAGENTA = _SECONDARY

# Lazy-loaded at first use (saves ~500-800ms startup):
#   from langgraph.types import Command
#   from totoro.utils import sanitize_text
#   from totoro.status import StatusTracker
#   from totoro.diff import (
#       format_file_diff, find_line_number,
#       safe_print as _safe_print
#   )
Command = None
sanitize_text = None
StatusTracker = None
format_file_diff = None
find_line_number = None
_safe_print = None


def _ensure_imports():
    """Lazy-load heavy dependencies on first actual use."""
    global \
        Command, \
        sanitize_text, \
        StatusTracker, \
        format_file_diff, \
        find_line_number, \
        _safe_print
    if Command is not None:
        return
    from langgraph.types import Command as _Command
    from totoro.utils import sanitize_text as _sanitize
    from totoro.status import StatusTracker as _Tracker
    from totoro.diff import (
        format_file_diff as _ffd,
        find_line_number as _fln,
        safe_print as _sp,
    )

    Command = _Command
    sanitize_text = _sanitize
    StatusTracker = _Tracker
    format_file_diff = _ffd
    find_line_number = _fln
    _safe_print = _sp


# ─── Pending tool calls for diff display ───
def _banner(config=None, session_id: str = "") -> str:
    """Build the ASCII art welcome banner with model and session info.

    Args:
        config: Agent configuration object with model/provider info.
        session_id: Current session identifier to display.

    Returns:
        Multi-line ANSI-formatted banner string.
    """
    import shutil
    from totoro.colors import ACCENT, BODY, SECONDARY, DIM, RESET

    width = shutil.get_terminal_size().columns

    model_name = config.model if config else "unknown"
    provider = config.provider if config else "auto"

    A, R = ACCENT, RESET

    # TOTORO block letters
    _T = [
        "████████╗",
        "╚══██╔══╝",
        "   ██║   ",
        "   ██║   ",
        "   ██║   ",
        "   ╚═╝   ",
    ]
    _O = [
        " ██████╗ ",
        "██╔═══██╗",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ]
    _Rv = [
        "██████╗  ",
        "██╔══██╗ ",
        "██████╔╝ ",
        "██╔══██╗ ",
        "██║  ██║ ",
        "╚═╝  ╚═╝ ",
    ]
    logo_raw = [
        "".join(ch[i] for ch in [_T, _O, _T, _O, _Rv, _O]) for i in range(6)
    ]

    # Mascot — Totoro (raw for width calc)
    mascot_raw = [
        "        ███                   ███",
        "       █████                 █████",
        "       █████                 █████",
        "        ███                   ███",
        "         ███████████████████████",
        "       ███████████████████████████",
        "      █████╭ ╮█████████████╭ ╮█████",
        "     ╲█████╰●╯████ ⊙ ⊙ ████╰●╯█████╱",
        "   ───█████████████████████████████───",
        "    ╱▐█████▥▥▥▥▥▥▥▥▥▥▥▥▥▥▥▥▥▥▥█████▌╲",
        "    ▐████████████▥▥▥▥▥▥▥████████████▌",
        "    █████████████████████████████████",
        "   ▐██████░░░░░░░░░░░░░░░░░░░░░░██████▌",
        "   ███░░░░░░░▄▀▀▀▄░░░░░░░▄▀▀▀▄░░░░░░███",
        "  |██░░░░░▄▀▀▀▄░░░░▄▀▀▀▄░░░░▄▀▀▀▄░░░░██|",
        " ▐|█░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░█|▌",
        " █|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|█",
        "▐█|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|█▌",
        " █|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|█",
        " ▐|░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░|▌",
        "   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░",
        "    ░░░ ▄██▄ ░░░░░░░░░░░░░░░ ▄██▄ ░░░",
        "══════ ██████ ░░░░░░░░░░░░░ ██████ ═════",
        "        ▀███▀               ▀███▀",
    ]
    mascot_w = max(len(line) for line in mascot_raw)
    mascot_h = len(mascot_raw)

    # Right panel content — logo on top, then info
    # Build right-side lines to match mascot height
    right = [""] * mascot_h

    # Vertically center logo + info block within mascot height
    # Logo(6) + blank(1) + info(2) + blank(1) + info(2) = 12 rows
    content_h = 12
    offset = max(0, (mascot_h - content_h) // 2)

    for i, line in enumerate(logo_raw):
        right[offset + i] = f"{A}{line}{R}"

    right[offset + 7] = (
        f"{SECONDARY}Model{R}     {DIM}│{R}  {BODY}{model_name}{R}"
    )
    right[offset + 8] = (
        f"{SECONDARY}Provider{R}  {DIM}│{R}  {BODY}{provider}{R}"
    )
    right[offset + 10] = (
        f"{SECONDARY}Session{R}   {DIM}│{R}  {BODY}{session_id}{R}"
    )
    right[offset + 11] = (
        f"{SECONDARY}Commands{R}  {DIM}│{R}"
        f"  {BODY}/help{R}  {DIM}·{R}"
        f"  {BODY}Shift+Tab{R}  {DIM}·{R}"
        f"  {BODY}/exit{R}"
    )

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
    """Check if input is a slash command (not a file path like /home/...).

    Args:
        text: Raw user input string.

    Returns:
        True if the input matches a registered slash command.
    """
    from totoro.commands.registry import get_command_names

    first_word = text.strip().split()[0].lower() if text.strip() else ""
    return any(
        first_word == cmd or first_word.startswith(cmd + " ")
        for cmd in get_command_names()
    )


def main():
    parser = argparse.ArgumentParser(
        description="Totoro: Advanced CLI Coding Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  totoro                              # Interactive mode
  totoro -n "fix the login bug"      # Non-interactive single task
  totoro --auto-approve              # Skip all approval prompts
  totoro --model anthropic/claude-sonnet-4-5  # Use specific model
  totoro --provider vllm --model meta-llama/Llama-3.1-70B  # Use vLLM
  totoro --resume <session-id>       # Resume a previous session
  totoro --list-sessions             # List all sessions
""",
    )
    parser.add_argument(
        "-n",
        "--non-interactive",
        type=str,
        metavar="TASK",
        help="Run single task non-interactively",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-approve all tool executions (no HITL)",
    )
    parser.add_argument("--model", type=str, help="Override model name")
    parser.add_argument(
        "--provider",
        type=str,
        choices=["auto", "openrouter", "anthropic", "openai", "vllm"],
        help="LLM provider (default: auto-detect from env)",
    )
    parser.add_argument(
        "--resume",
        type=str,
        metavar="SESSION_ID",
        help="Resume a previous session",
    )
    parser.add_argument(
        "--list-sessions", action="store_true", help="List all sessions"
    )
    parser.add_argument(
        "--setup", action="store_true", help="Re-run the setup wizard"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show detailed tool results"
    )
    parser.add_argument(
        "task", nargs="*", help="Task to run (alternative to -n)"
    )
    args = parser.parse_args()

    from totoro.config.settings import load_config, ensure_api_keys

    ensure_api_keys(force_setup=args.setup)

    if args.setup:
        print("  Setup complete. Starting Totoro...\n")

    cli_overrides = {}
    if args.auto_approve:
        cli_overrides["permissions"] = {"mode": "auto_approve"}
    if args.model:
        cli_overrides["model"] = args.model
    if args.provider:
        cli_overrides["provider"] = args.provider

    config = load_config(cli_overrides=cli_overrides)

    # Propagate permission allow patterns to orchestrator
    from totoro.orchestrator import set_allow_patterns

    set_allow_patterns(config.permissions.allow)

    from totoro.core.agent import create_totoro_agent

    agent, checkpointer, store, auto_dream = create_totoro_agent(config)

    # Initialize session manager
    from totoro.session.manager import SessionManager

    session_manager = SessionManager(checkpointer=checkpointer)

    # Inject into command registry
    from totoro.commands.registry import (
        set_session_manager,
        set_auto_dream,
        set_agent_config,
        set_skill_manager,
    )

    set_session_manager(session_manager)
    set_auto_dream(auto_dream)
    set_agent_config(config)

    task = args.non_interactive or (" ".join(args.task) if args.task else None)
    verbose = args.verbose

    if args.list_sessions:
        print(session_manager.format_session_list())
        return

    # Initialize skill manager lazily (only needed for interactive/task mode)
    from totoro.skills import SkillManager

    skill_manager = SkillManager(config.project_root)
    set_skill_manager(skill_manager)

    if task:
        session_info = session_manager.create_session(description=task[:50])
        invoke_config = session_manager.get_invoke_config(
            session_info.session_id
        )
        success = _stream_with_hitl(
            agent,
            task,
            invoke_config,
            auto_approve=args.auto_approve,
            verbose=verbose,
        )
        if not success:
            sys.exit(1)
    elif args.resume:
        from totoro.session.restore import restore_session

        invoke_config = restore_session(agent, args.resume, session_manager)
        if invoke_config is None:
            print("Could not restore session. Starting new session.")
            session_info = session_manager.create_session()
            invoke_config = session_manager.get_invoke_config(
                session_info.session_id
            )
        _run_interactive(
            agent,
            invoke_config,
            session_manager=session_manager,
            auto_approve=args.auto_approve,
            verbose=verbose,
            config=config,
        )
    else:
        session_info = session_manager.create_session()
        invoke_config = session_manager.get_invoke_config(
            session_info.session_id
        )
        _run_interactive(
            agent,
            invoke_config,
            session_manager=session_manager,
            auto_approve=args.auto_approve,
            verbose=verbose,
            config=config,
        )


def _run_interactive(
    agent,
    invoke_config: dict,
    session_manager=None,
    auto_approve: bool = False,
    verbose: bool = False,
    config=None,
):
    """Interactive mode main loop.

    Args:
        agent: Compiled LangGraph agent.
        invoke_config: LangGraph invocation config with thread_id.
        session_manager: Optional session manager for persistence.
        auto_approve: Whether to skip all approval prompts.
        verbose: Whether to show detailed tool results.
        config: Agent configuration for banner and model switching.
    """
    from totoro.commands.registry import handle_slash_command
    from totoro.input import InputHandler

    handler = InputHandler(
        initial_mode="auto-approve" if auto_approve else "default"
    )

    session_id = invoke_config["configurable"]["thread_id"]
    print(_banner(config, session_id=session_id))
    print()

    while True:
        user_input = handler.read_input()

        if user_input is None:
            print("\nBye!")
            break

        if not user_input:
            continue

        # Echo user input (prompt bar was erased by erase_when_done)
        print(f"{handler.prompt}{user_input}")

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
                from totoro.core.agent import create_totoro_agent

                try:
                    agent, _, _, auto_dream = create_totoro_agent(config)
                    from totoro.commands.registry import set_auto_dream

                    set_auto_dream(auto_dream)
                    print(f"  {_BOLD}Skills reloaded.{_RESET}")
                except Exception as e:
                    print(f"{_RED}  Reload failed: {e}{_RESET}")
                continue

            # Handle __agent_message__ — inject as user
        # message to agent (e.g. /init)
            if result and result.startswith("__agent_message__:"):
                user_input = result[len("__agent_message__:") :]
                # Fall through to normal message processing below
            else:
                if result:
                    print(result)
                continue

        # Plan-only mode: inject planning constraint + disable auto-dispatch
        from totoro.orchestrator import set_plan_only

        if handler.is_plan_only:
            set_plan_only(True)
            user_input = (
                f"{user_input}\n\n"
                "[SYSTEM: Plan-only mode is active. "
                "Use write_todos to create a plan. "
                "Do NOT execute any file operations "
                "or shell commands. Only plan.]"
            )
        else:
            set_plan_only(False)

        # Track turn + analyze user message for Auto-Dream memory
        from totoro.commands.registry import _auto_dream as _ad

        if _ad:
            _ad.on_turn(user_input)

        # Track turn in session manager
        if session_manager:
            session_id = invoke_config["configurable"]["thread_id"]
            session_manager.update_activity(session_id)

        print()  # spacing after user input
        _stream_with_hitl(
            agent,
            user_input,
            invoke_config,
            auto_approve=handler.is_auto_approve,
            verbose=verbose,
            handler=handler,
        )
        print()  # spacing before next prompt

    # ── Session exit: final memory extraction ──
    from totoro.commands.registry import _auto_dream as _ad_exit

    if _ad_exit and _ad_exit._turn_count > 0:
        try:
            _ad_exit.extract_on_exit(agent, invoke_config)
        except Exception:
            pass


def _handle_model_change(sentinel: str, config, session_manager):
    """Parse model change sentinel and rebuild agent.

    Args:
        sentinel: Sentinel string like
            "__model_change__:model[:provider]".
        config: Current agent configuration to update.
        session_manager: Session manager for re-injection.

    Returns:
        New agent on success, or None on error.
    """
    parts = sentinel.split(":", maxsplit=2)
    # parts = ["__model_change__", model_name]
    # or ["__model_change__", model_name, provider]
    new_model = parts[1] if len(parts) > 1 else None
    new_provider = parts[2] if len(parts) > 2 else None

    if not new_model:
        print(f"{_RED}  Error: no model name provided.{_RESET}")
        return None

    if config is None:
        print(
            f"{_RED}  Error: config not available for model switching.{_RESET}"
        )
        return None

    old_model = config.model
    old_provider = config.provider
    config.model = new_model
    if new_provider:
        config.provider = new_provider

    print(
        f"{_DIM}  Switching model: {old_model} → {new_model}...{_RESET}",
        flush=True,
    )

    try:
        from totoro.core.agent import create_totoro_agent

        agent, _, _, auto_dream = create_totoro_agent(config)

        from totoro.commands.registry import set_auto_dream, set_agent_config

        set_auto_dream(auto_dream)
        set_agent_config(config)

        provider_display = new_provider or config.provider
        print(
            f"  {_BOLD}Model switched to: {new_model}"
            f"{_RESET} (provider: {provider_display})"
        )

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


def _persist_model_to_settings(model_name: str, project_root: str = ""):
    """Save selected model to settings.json.

    Persists to ~/.totoro/settings.json across sessions.

    Args:
        model_name: Model identifier to persist.
        project_root: Project root path (unused,
            kept for signature compatibility).
    """
    import json
    from pathlib import Path

    settings_path = Path.home() / ".totoro" / "settings.json"
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


def _stream_with_hitl(
    agent,
    user_input: str,
    config: dict,
    auto_approve: bool = False,
    verbose: bool = False,
    handler=None,
) -> bool:
    """Stream agent response with HITL and live dashboard.

    Args:
        agent: Compiled LangGraph agent.
        user_input: User's message text.
        config: LangGraph invocation config with thread_id.
        auto_approve: Whether to auto-approve all tool executions.
        verbose: Whether to show detailed tool results.
        handler: Optional InputHandler for mode switching during streaming.

    Returns:
        True if the agent produced a response, False if errors occurred.
    """
    _ensure_imports()
    from totoro.orchestrator import (
        set_tracker,
        set_pane_manager,
        set_auto_approve,
        RenderThread,
    )
    from totoro.pane import PaneManager
    from totoro.hotkey import HotkeyListener

    tracker = StatusTracker()
    set_tracker(tracker)
    set_auto_approve(auto_approve)

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

            interrupt_info = _do_stream(
                agent, input_payload, config, tracker=tracker, verbose=verbose
            )

            # Deactivate hotkey before HITL or exit
            if hotkey:
                hotkey.deactivate()

            # Check if mode changed during streaming
            if handler and handler.is_auto_approve:
                auto_approve = True
                set_auto_approve(True)

            if interrupt_info is None:
                break

            # Clear dashboard before HITL prompt
            render_thread.shutdown()
            render_thread.join(timeout=1)
            with tracker._lock:
                tracker._clear_previous()
                tracker._last_panel_lines = 0

            if auto_approve:
                decisions = [
                    {"type": "approve"}
                    for _ in _flatten_decisions(interrupt_info)
                ]
                signal = _HITL_CONTINUE
            else:
                decisions, signal = _collect_hitl_decisions(interrupt_info)

            # Handle abort — stop the turn immediately
            if signal == _HITL_ABORT:
                resume_value = _build_resume_payload(interrupt_info, decisions)
                try:
                    for _ in agent.stream(
                        Command(resume=resume_value),
                        config=config,
                        stream_mode="updates",
                    ):
                        pass
                except Exception:
                    pass
                break

            # Handle approve-all — auto-approve for rest of this turn
            if signal == _HITL_APPROVE_ALL:
                auto_approve = True
                if handler:
                    handler.mode = "auto-approve"

            input_payload = Command(
                resume=_build_resume_payload(interrupt_info, decisions)
            )

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
    return tracker.tool_count > 0 or tracker._got_ai_text


def _do_stream(
    agent,
    input_payload,
    config: dict,
    tracker: StatusTracker,
    verbose: bool = False,
) -> list | None:
    """Stream agent response with token-level AI text streaming.

    Uses stream_mode=["messages", "updates"]:
    "messages" provides token-by-token AI text and
    tool call chunks for real-time output, while
    "updates" provides node-level outputs for tool
    results, todos, and diffs.

    Args:
        agent: Compiled LangGraph agent.
        input_payload: Message dict or Command resume payload.
        config: LangGraph invocation config with thread_id.
        tracker: StatusTracker for live dashboard updates.
        verbose: Whether to show detailed tool results.

    Returns:
        List of pending interrupt tasks if HITL is
        needed, or None if complete.
    """

    got_ai_response = False
    event_count = 0
    had_error = False
    ai_header_printed = False
    streaming_ai_id: str | None = (
        None  # Track which AI message is being streamed
    )
    _text_buffer: list[
        str
    ] = []  # Buffer text until we know no tool call follows
    _tool_call_seen = (
        False  # True once tool_call_chunks appear in current AI msg
    )

    def _clear_and_print(text: str, **kwargs):
        """Clear dashboard and print below it."""
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0
            _safe_print(text, flush=True, **kwargs)
            tracker._mark_dirty()

    def _flush_text_buffer(buf, trk, print_fn, dim, reset, header=True):
        """Flush buffered AI text to screen with markdown rendering.

        Acquires the tracker lock, clears any active status panel, then
        prints the buffered text with optional ``● >`` header prefix.
        Text is rendered through the markdown-to-ANSI converter.

        Args:
            buf: List of text chunks accumulated from AI stream.
            trk: StatusTracker instance (used for lock and panel clearing).
            print_fn: Print function (typically ``_safe_print``).
            dim: ANSI escape code for dim styling.
            reset: ANSI escape code to reset styling.
            header: If True, print blank line and ``● >`` prefix before text.
                Set to False for subsequent flushes of the same response.
        """
        if not buf:
            return
        from totoro.markdown import render as _render_md

        with trk._lock:
            trk._got_ai_text = True
            trk._clear_previous()
            trk._last_panel_lines = 0
            if header:
                # Blank line for visual separation from subagent summary
                print_fn("", flush=True)
                print_fn(f"{dim}● > {reset}", end="", flush=True)
            # Render markdown to ANSI-styled text
            raw = "".join(buf)
            rendered = _render_md(raw)
            print_fn(rendered, end="", flush=True)
            trk._mark_dirty()

    import queue as _queue

    _stream_q: _queue.Queue = _queue.Queue(maxsize=200)
    _stream_err: list = []
    _stream_start = time.time()
    _FIRST_EVENT_TIMEOUT = 180  # 3 min to get first response from API
    _IDLE_TIMEOUT = 300  # 5 min without any event = stuck

    def _main_stream_worker():
        try:
            for ev in agent.stream(
                input_payload,
                config=config,
                stream_mode=["messages", "updates"],
            ):
                _stream_q.put(ev)
            _stream_q.put(None)
        except Exception as exc:
            _stream_err.append(exc)
            _stream_q.put(None)

    import threading as _threading

    _stream_t = _threading.Thread(target=_main_stream_worker, daemon=True)
    _stream_t.start()

    _got_first = False

    try:
        while True:
            wait = (
                5.0
                if _got_first
                else min(
                    5.0,
                    max(
                        0.1,
                        _FIRST_EVENT_TIMEOUT - (time.time() - _stream_start),
                    ),
                )
            )
            try:
                event = _stream_q.get(timeout=wait)
            except _queue.Empty:
                elapsed = time.time() - _stream_start
                if not _got_first and elapsed > _FIRST_EVENT_TIMEOUT:
                    _safe_print(
                        f"\n{_RED}[Timeout] API not responding"
                        f" after {int(elapsed)}s{_RESET}",
                        flush=True,
                    )
                    had_error = True
                    break
                continue

            if event is None:
                if _stream_err:
                    raise _stream_err[0]
                break

            _got_first = True
            _stream_start = time.time()  # Reset idle timer on each event
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
                    # Reset per-message state when a new AI message starts
                    chunk_id = getattr(msg_chunk, "id", None)
                    if (
                        chunk_id
                        and chunk_id != streaming_ai_id
                        and streaming_ai_id is not None
                    ):
                        # New AI message — flush buffered
                        # text from previous message
                        if _text_buffer and not _tool_call_seen:
                            _flush_text_buffer(
                                _text_buffer,
                                tracker,
                                _safe_print,
                                _DIM,
                                _RESET,
                                header=not ai_header_printed,
                            )
                            ai_header_printed = True
                            got_ai_response = True
                        _text_buffer.clear()
                        _tool_call_seen = False
                        ai_header_printed = False

                    content = getattr(msg_chunk, "content", "")
                    tool_call_chunks = getattr(
                        msg_chunk, "tool_call_chunks", []
                    )

                    # Text content — buffer (don't print
                    # yet, tool call might follow)
                    if content and not _tool_call_seen:
                        text = content if isinstance(content, str) else ""
                        if isinstance(content, list):
                            text = "".join(
                                b.get("text", "")
                                if isinstance(b, dict)
                                else str(b)
                                for b in content
                            )
                        if text:
                            _text_buffer.append(text)
                            streaming_ai_id = getattr(msg_chunk, "id", None)

                    # Tool call chunks — discard buffered
                    # text (it was "thinking")
                    if tool_call_chunks:
                        got_ai_response = True
                        # Capture message ID even for tool-call-only messages
                        # so subsequent new messages can be detected
                        streaming_ai_id = (
                            getattr(msg_chunk, "id", None) or streaming_ai_id
                        )
                        if not _tool_call_seen:
                            # Discard pre-tool-call text
                            _text_buffer.clear()
                            _tool_call_seen = True
                        for tc_chunk in tool_call_chunks:
                            tc_name = tc_chunk.get("name", "")
                            if tc_name:
                                tracker.on_tool_start(
                                    tc_name, tc_chunk.get("args", {})
                                )

                # Tool result messages — handle diffs and errors
                elif msg_type == "tool":
                    name = getattr(msg_chunk, "name", "tool")
                    tool_content = sanitize_text(
                        str(getattr(msg_chunk, "content", ""))
                    )
                    tool_call_id = getattr(msg_chunk, "tool_call_id", None)
                    tracker.on_tool_end(name, tool_content[:200])

                    # End previous AI streaming line
                    if ai_header_printed and streaming_ai_id:
                        ai_header_printed = False
                        streaming_ai_id = None
                        _safe_print("", flush=True)  # newline

                    # Reset so next AI text after tool results can be buffered
                    _tool_call_seen = False

                    is_error = "error" in tool_content.lower()[:100]

                    if (
                        tool_call_id
                        and tool_call_id in _pending_file_ops
                        and not is_error
                    ):
                        op = _pending_file_ops.pop(tool_call_id)
                        diff_text = format_file_diff(
                            op["name"],
                            op.get("args", {}),
                            op.get("start_line"),
                        )
                        if diff_text:
                            _clear_and_print(diff_text)
                    elif verbose or is_error:
                        display = tool_content[: 500 if verbose else 300]
                        color = _MAGENTA if verbose else _RED
                        prefix = (
                            f"  <- {name}:"
                            if verbose
                            else f"  [error] {name}:"
                        )
                        _clear_and_print(f"{color}{prefix} {display}{_RESET}")

                continue

            # ── "updates" mode: node-level outputs (todos, full tool args) ──
            # Flush any buffered text — updates mean the AI message is complete
            if _text_buffer and not _tool_call_seen:
                _flush_text_buffer(
                    _text_buffer,
                    tracker,
                    _safe_print,
                    _DIM,
                    _RESET,
                    header=not ai_header_printed,
                )
                ai_header_printed = True
                got_ai_response = True
                _text_buffer.clear()

            if mode == "updates" and isinstance(data, dict):
                for node_name, node_output in data.items():
                    if verbose:
                        out_keys = (
                            list(node_output.keys())
                            if isinstance(node_output, dict)
                            else type(node_output).__name__
                        )
                        _safe_print(
                            f"{_DIM}  [debug] node={node_name}"
                        f" keys={out_keys}{_RESET}",
                            flush=True,
                        )

                    if not isinstance(node_output, dict):
                        continue

                    # Track todo updates from state
                    todos_in_state = node_output.get("todos")
                    if todos_in_state is not None:
                        tracker.on_todos_updated(
                            [
                                t
                                if isinstance(t, dict)
                                else {"content": str(t), "status": "pending"}
                                for t in todos_in_state
                            ]
                        )

                    # Process complete messages from updates
                    # (tool calls with full args for diffs)
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
                            # Track token usage from main agent
                            usage = getattr(msg, "usage_metadata", None) or {}
                            if not usage:
                                meta = getattr(msg, "response_metadata", {})
                                usage = meta.get(
                                    "token_usage", meta.get("usage", {})
                                )
                            if usage:
                                tracker.token_input += usage.get(
                                    "input_tokens",
                                    usage.get("prompt_tokens", 0),
                                )
                                tracker.token_output += usage.get(
                                    "output_tokens",
                                    usage.get("completion_tokens", 0),
                                )
                                # Extract cached tokens from
                            # prompt_tokens_details or cache_read
                                cached = usage.get(
                                    "cache_read_input_tokens", 0
                                )
                                if not cached:
                                    details = usage.get(
                                        "prompt_tokens_details", {}
                                    )
                                    if isinstance(details, dict):
                                        cached = details.get(
                                            "cached_tokens", 0
                                        )
                                tracker.token_cached += cached

                            tool_calls = getattr(msg, "tool_calls", [])
                            for tc in tool_calls:
                                tc_name = tc.get("name", "")
                                tc_id = tc.get("id")
                                # Store full args for diff
                                # (messages mode has chunks only)
                                if (
                                    tc_name in ("edit_file", "write_file")
                                    and tc_id
                                ):
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

    except KeyboardInterrupt:
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0
        _safe_print(
            f"\n{_YELLOW}[Interrupted] Stopped by user{_RESET}", flush=True
        )
        had_error = True

    except Exception as e:
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0

        # Some LangGraph internal errors are recoverable
        # — skip silently if we already got a response
        err_detail = str(e)
        is_internal = (
            "'str' object has no attribute" in err_detail
            or "'NoneType' object" in err_detail
        )
        if is_internal and got_ai_response:
            # LangGraph internal error but agent
            # already responded — not fatal
            pass
        else:
            had_error = True
            err_type = type(e).__name__
            _safe_print(
                f"\n{_RED}[Stream error] {err_type}: "
                f"{sanitize_text(err_detail)[:500]}"
                f"{_RESET}",
                flush=True,
            )
        # Show HTTP status if available (e.g., OpenRouter/OpenAI API errors)
        if hasattr(e, "status_code"):
            _safe_print(f"{_DIM}  HTTP {e.status_code}{_RESET}", flush=True)
        if hasattr(e, "response") and hasattr(e.response, "text"):
            resp_text = sanitize_text(str(e.response.text))[:300]
            _safe_print(f"{_DIM}  Response: {resp_text}{_RESET}", flush=True)

    # Flush remaining buffered text
    # (runs after both normal and exception paths)
    if _text_buffer:
        _flush_text_buffer(
            _text_buffer,
            tracker,
            _safe_print,
            _DIM,
            _RESET,
            header=not ai_header_printed,
        )
        ai_header_printed = True
        got_ai_response = True
        _text_buffer.clear()

    # Fallback: if tool calls ran but no AI text was displayed (e.g. text was
    # suppressed before orchestrate_tool and model didn't generate a summary),
    # recover text from the final agent state.
    if not tracker._got_ai_text:
        try:
            state = agent.get_state(config)
            if state and state.values:
                messages = state.values.get("messages", [])
                # Find the last user message index
                last_user_idx = -1
                for i in range(len(messages) - 1, -1, -1):
                    if (
                        hasattr(messages[i], "type")
                        and messages[i].type == "human"
                    ):
                        last_user_idx = i
                        break
                # Only search AI messages after the last user message
                recent = (
                    messages[last_user_idx + 1 :] if last_user_idx >= 0 else []
                )
                for msg in reversed(recent):
                    if not hasattr(msg, "type") or msg.type != "ai":
                        continue
                    content = getattr(msg, "content", "")
                    has_tools = bool(getattr(msg, "tool_calls", []))
                    text = _extract_text(content) if content else ""
                    if has_tools and not text:
                        continue
                    if text:
                        from totoro.markdown import render as _render_md

                        _safe_print(
                            f"\n{_DIM}● > {_RESET}", end="", flush=True
                        )
                        _safe_print(_render_md(text), flush=True)
                        ai_header_printed = True
                        tracker._got_ai_text = True
                        got_ai_response = True
                    break
        except Exception:
            pass

    # End streaming line if still open
    if ai_header_printed:
        _safe_print("", flush=True)

    if not got_ai_response:
        with tracker._lock:
            tracker._clear_previous()
            tracker._last_panel_lines = 0
        if event_count == 0:
            _safe_print(
                f"{_YELLOW}(No response from model. Possible causes:\n"
                f"  • API key invalid or expired —"
                f" check with `totoro --setup`\n"
                f"  • Network timeout — check internet connection\n"
                f"  • Model unavailable — try a different model with /model\n"
                f"  Use --verbose for debug details){_RESET}",
                flush=True,
            )
        else:
            _safe_print(
                f"{_YELLOW}(Got {event_count} events but no AI text.\n"
                f"  • Model may not support tool calling\n"
                f"  • Try a different model with /model\n"
                f"  Use --verbose for debug details){_RESET}",
                flush=True,
            )

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
    """Extract text from various content formats.

    Args:
        content: Message content as string,
            list of blocks, or other format.

    Returns:
        Sanitized plain text extracted from the content.
    """
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

    For a single interrupt the payload is {"decisions": decisions}. For
    multiple interrupts each decision is mapped to its interrupt ID.

    Args:
        interrupts: List of LangGraph interrupt task objects.
        decisions: List of decision dicts
            (approve/reject/edit) per interrupt.

    Returns:
        Resume payload dict suitable for Command(resume=...).
    """
    # Check if interrupts have IDs (newer LangGraph API)
    interrupt_ids = []
    for task in interrupts:
        if hasattr(task, "interrupts") and task.interrupts:
            for intr in task.interrupts:
                intr_id = getattr(intr, "id", None) or getattr(
                    intr, "interrupt_id", None
                )
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
    """Flatten interrupt list into individual action items needing decisions.

    Args:
        interrupts: List of LangGraph interrupt task objects.

    Returns:
        Flat list of action items requiring a decision.
    """
    count = []
    for task in interrupts:
        interrupt_value = None
        if hasattr(task, "interrupts") and task.interrupts:
            interrupt_value = (
                task.interrupts[0].value
                if hasattr(task.interrupts[0], "value")
                else task.interrupts[0]
            )
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


_HITL_CONTINUE = "continue"  # normal flow
_HITL_APPROVE_ALL = "approve_all"  # auto-approve remaining
_HITL_ABORT = "abort"  # stop the turn


def _collect_hitl_decisions(interrupts) -> tuple[list[dict], str]:
    """Prompt user for HITL decisions on pending tool executions.

    Args:
        interrupts: List of LangGraph interrupt task objects to review.

    Returns:
        Tuple of (decisions, signal) where decisions is a list of
        approve/reject/edit dicts and signal is one of _HITL_CONTINUE,
        _HITL_APPROVE_ALL, or _HITL_ABORT.
    """
    decisions = []

    for task in interrupts:
        interrupt_value = None
        if hasattr(task, "interrupts") and task.interrupts:
            interrupt_value = (
                task.interrupts[0].value
                if hasattr(task.interrupts[0], "value")
                else task.interrupts[0]
            )
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
            if tool_name in ("edit_file", "write_file") and isinstance(
                tool_args, dict
            ):
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
                    print(
                        f"\n{_YELLOW}[APPROVAL REQUIRED]"
                        f"{_RESET} {_BOLD}"
                        f"{tool_name}{_RESET}"
                    )
            else:
                print(
                    f"\n{_YELLOW}[APPROVAL REQUIRED]"
                    f"{_RESET} {_BOLD}"
                    f"{tool_name}{_RESET}"
                )
                if tool_args:
                    if isinstance(tool_args, dict):
                        for k, v in tool_args.items():
                            v_str = sanitize_text(str(v))
                            if len(v_str) > 200:
                                v_str = v_str[:200] + "..."
                            print(f"  {k}: {v_str}")
                    else:
                        print(f"  {sanitize_text(str(tool_args))}")
            print(
                f"  {_BOLD}(a){_RESET}pprove / "
                f"{_BOLD}(A){_RESET}pprove all / "
                f"{_BOLD}(r){_RESET}eject / "
                f"{_BOLD}(x){_RESET} abort / "
                f"{_BOLD}(e){_RESET}dit ?"
            )

            try:
                choice = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n  {_RED}✗ Aborted{_RESET}")
                decisions.append({"type": "reject"})
                return decisions, _HITL_ABORT

            # Approve all remaining
            if choice in ("A", "approve all", "aa"):
                print(
                    f"  {_YELLOW}⚡ Auto-approve enabled for this turn{_RESET}"
                )
                decisions.append({"type": "approve"})
                remaining = len(action_requests) - (
                    action_requests.index(action) + 1
                )
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
                edited_args = _apply_natural_language_edit(
                    tool_name, tool_args, user_edit
                )
                if edited_args is not None:
                    print(f"  {_DIM}Edited args:{_RESET}")
                    for k, v in edited_args.items():
                        v_str = sanitize_text(str(v))
                        if len(v_str) > 200:
                            v_str = v_str[:200] + "..."
                        print(f"    {k}: {v_str}")
                    decisions.append(
                        {
                            "type": "edit",
                            "edited_action": {
                                "name": tool_name,
                                "args": edited_args,
                            },
                        }
                    )
                else:
                    print("  Edit failed, rejecting.")
                    decisions.append({"type": "reject"})

            # Explicit reject
            elif choice.lower() in ("r", "reject", "n", "no"):
                print(f"  {_RED}✗ Rejected{_RESET}")
                decisions.append({"type": "reject"})

            # Free text → treat as edit instruction
            else:
                edited_args = _apply_natural_language_edit(
                    tool_name, tool_args, choice
                )
                if edited_args is not None:
                    print(f"  {_DIM}Edited args:{_RESET}")
                    for k, v in edited_args.items():
                        v_str = sanitize_text(str(v))
                        if len(v_str) > 200:
                            v_str = v_str[:200] + "..."
                        print(f"    {k}: {v_str}")
                    decisions.append(
                        {
                            "type": "edit",
                            "edited_action": {
                                "name": tool_name,
                                "args": edited_args,
                            },
                        }
                    )
                else:
                    print(f"  {_RED}✗ Could not parse edit, rejecting{_RESET}")
                    decisions.append({"type": "reject"})

    return decisions, _HITL_CONTINUE


def _apply_natural_language_edit(
    tool_name: str, original_args: dict, user_instruction: str
) -> dict | None:
    """Use a lightweight LLM to apply a natural language edit to tool args.

    Args:
        tool_name: Name of the tool being edited.
        original_args: Current tool arguments dict.
        user_instruction: Natural language description of desired changes.

    Returns:
        Updated args dict on success, or None if parsing/LLM call failed.
    """
    from totoro.core.models import create_lightweight_model

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
        f"Current args (JSON):\n"
        f"{json.dumps(original_args, ensure_ascii=False, indent=2)}"
        f"\n\n"
        f"User's edit instruction: {user_instruction}"
        f"\n\n"
        f"Return ONLY the updated args as a single "
        f"JSON object. No explanation, no markdown "
        f"fences."
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
