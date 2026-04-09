"""Parallel sub-agent orchestrator using multiprocessing.

Architecture:
  Main agent → calls orchestrate_tool
    → multiprocessing.Process spawns N workers
      → Each worker rebuilds its own subagent graph in the child process
      → Worker streams events → multiprocessing.Queue
    → Event collector thread reads Queue → updates PaneManager + StatusTracker
    → All workers complete → combined results returned to main agent

  The curses SplitPaneTUI handles rendering during orchestration.
"""
import os
import time
import json
import queue
import threading
import multiprocessing as mp
from dataclasses import dataclass, field

from langchain_core.tools import tool

from atom.utils import sanitize_text
from atom.pane import SubagentEvent, SubagentResult, PaneManager

# ─── Module-level state (set by create_atom_agent / CLI) ───
_subagent_configs: list[dict] = []   # serializable subagent configs
_model_config: dict = {}             # {model_name, provider} for rebuilding in child process
_project_root: str = "."
_tracker = None
_pane_manager: PaneManager | None = None


def register_subagent_configs(configs: list[dict], model_name: str, provider: str, project_root: str):
    """Register serializable subagent configs for multiprocessing."""
    global _subagent_configs, _model_config, _project_root
    _subagent_configs = configs
    _model_config = {"model_name": model_name, "provider": provider}
    _project_root = project_root


def set_tracker(tracker):
    global _tracker
    _tracker = tracker


def set_pane_manager(pane_manager: PaneManager | None):
    global _pane_manager
    _pane_manager = pane_manager


# ─── Orchestrate tool ───

@tool
def orchestrate_tool(tasks_json: str) -> str:
    """Run multiple Atom task agents in PARALLEL. Use this instead of sequential task calls.

    Each task spawns an independent Atom agent with full tool access and skills.
    All tasks execute concurrently and their results are combined.

    Args:
        tasks_json: JSON array of task objects. Each object has:
            - "task": detailed description of what the Atom agent should do

    Example:
        orchestrate_tool('[
            {"task": "Create index.html with React setup and Vite config"},
            {"task": "Create src/App.tsx with fibonacci dashboard component"},
            {"task": "Create api/fibonacci.ts serverless function"}
        ]')
    """
    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"Error parsing tasks JSON: {e}"

    if not isinstance(tasks, list) or not tasks:
        return "Error: tasks must be a non-empty JSON array."

    results = _run_parallel(tasks)

    parts = []
    for name, result in results.items():
        if isinstance(result, SubagentResult):
            result_text = sanitize_text(result.final_text)
            files = ", ".join(result.files_modified[:5]) if result.files_modified else "none"
            parts.append(
                f"=== [{name}] ({len(result.tools_used)} tools, files: {files}) ===\n"
                f"{result_text[:3000]}"
            )
        else:
            result_text = sanitize_text(str(result))
            if len(result_text) > 3000:
                result_text = result_text[:3000] + "\n... (truncated)"
            parts.append(f"=== [{name}] ===\n{result_text}")

    return "\n\n".join(parts) or "(no results)"


# ─── Parallel execution engine (multiprocessing) ───

def _run_parallel(tasks: list[dict]) -> dict[str, SubagentResult | str]:
    """Execute tasks in parallel using multiprocessing + curses split-pane."""
    import curses as _curses
    from atom.tui import SplitPaneTUI

    results: dict[str, SubagentResult | str] = {}
    event_queue: mp.Queue = mp.Queue(maxsize=2000)
    config_map = {cfg["name"]: cfg for cfg in _subagent_configs}
    use_curses = _pane_manager is not None and _tracker is not None and len(tasks) > 0

    # Start event collector thread
    collector_halt = threading.Event()
    collector = threading.Thread(
        target=_event_collector,
        args=(event_queue, collector_halt),
        daemon=True,
    )
    collector.start()

    # Register subagents and start processes
    processes: dict[str, mp.Process] = {}
    result_holders: dict[str, mp.Queue] = {}

    for i, task in enumerate(tasks):
        agent_type = task.get("type", "coder")  # type hint preserved for backwards compat
        description = task.get("task", task.get("description", ""))
        label = f"atom-{i}"

        # All tasks use the unified atom task agent config
        cfg = config_map.get(agent_type) or config_map.get("coder")
        if cfg is None:
            cfg = {"name": "atom", "system_prompt": TASK_AGENT_PROMPT, "description": ""}

        if _tracker:
            _tracker.on_subagent_start(label, description)
            _tracker.set_plan_item_active(i + 1)
        if _pane_manager:
            _pane_manager.add_subagent(label, description)

        result_q: mp.Queue = mp.Queue(maxsize=1)
        result_holders[label] = result_q

        p = mp.Process(
            target=_worker_process,
            args=(cfg, description, label, _model_config, _project_root, event_queue, result_q),
            daemon=True,
        )
        p.start()
        processes[label] = p
        if _pane_manager:
            _pane_manager.set_pid(label, p.pid)

    # Process monitor thread — detects child exit and marks panes "done"
    # This breaks the deadlock: TUI waits for is_active=False, monitor sets it.
    monitor_halt = threading.Event()
    monitor = threading.Thread(
        target=_process_monitor,
        args=(processes, _pane_manager, _tracker, monitor_halt),
        daemon=True,
    )
    monitor.start()

    # Run curses TUI while processes execute
    if use_curses:
        # Suppress ANSI status panel rendering during curses mode
        _tracker._panel_enabled = False
        _tracker._clear_previous()
        _tracker._last_panel_lines = 0

        tui = SplitPaneTUI(tracker=_tracker, pane_manager=_pane_manager)
        try:
            _curses.wrapper(tui.run)
        except Exception:
            pass  # Ensure curses exits cleanly
        finally:
            _tracker._panel_enabled = True

    # Stop monitor
    monitor_halt.set()
    monitor.join(timeout=2)

    # Reap all processes — they should already be dead since TUI waited for is_active=False
    for label, p in processes.items():
        # Join with generous timeout
        p.join(timeout=30)

        # Force kill if still alive
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)
        if p.is_alive():
            p.kill()
            p.join(timeout=2)

        # Collect result
        try:
            result = result_holders[label].get_nowait()
            results[label] = result
        except Exception:
            if label not in results:
                results[label] = f"Process {label} did not return a result"

        if _tracker:
            _tracker.on_subagent_end(label)
            _tracker.advance_plan()

        # Release process handle
        try:
            p.close()
        except (ValueError, OSError):
            pass

    # Stop collector and drain queue
    collector_halt.set()
    collector.join(timeout=3)
    try:
        while not event_queue.empty():
            event_queue.get_nowait()
    except Exception:
        pass

    # Print summary after curses exits
    if _pane_manager:
        summary = _pane_manager.get_summary()
        if summary:
            from atom.diff import safe_print
            safe_print(summary)
        _pane_manager.clear()

    return results


def _process_monitor(
    processes: dict[str, mp.Process],
    pane_manager: PaneManager | None,
    tracker,
    halt: threading.Event,
):
    """Monitor thread: detect child process exit → mark panes done → TUI can exit.

    This solves the deadlock where TUI waits for is_active=False but
    complete_subagent() was only called after TUI exit.
    """
    reaped: set[str] = set()
    while not halt.is_set():
        for label, p in processes.items():
            if label in reaped:
                continue
            if not p.is_alive():
                reaped.add(label)
                if pane_manager:
                    pane_manager.complete_subagent(label)
        # All done? Stop polling.
        if len(reaped) == len(processes):
            break
        halt.wait(0.3)


# ─── Worker process ───

def _worker_process(
    subagent_cfg: dict,
    description: str,
    label: str,
    model_config: dict,
    project_root: str,
    event_queue: mp.Queue,
    result_queue: mp.Queue,
):
    """Run in a child process. Rebuilds the subagent graph and streams."""
    try:
        result = _run_subagent_in_process(
            subagent_cfg, description, label,
            model_config, project_root, event_queue,
        )
        result_queue.put(result)
    except Exception as e:
        result_queue.put(SubagentResult(final_text=f"Process error: {e}"))
    finally:
        # Flush result_queue so parent can read the result
        try:
            result_queue.close()
            result_queue.join_thread()
        except Exception:
            pass
        # Prevent event_queue from blocking exit
        try:
            event_queue.cancel_join_thread()
        except Exception:
            pass
        # Force immediate exit — LangGraph internal threads can hang on normal exit
        os._exit(0)


TASK_AGENT_PROMPT = """You are Atom, a task-focused coding agent. You execute a single assigned task directly and efficiently.

## Rules
- Execute the given task immediately — do NOT plan, do NOT create todos, do NOT delegate.
- Write files directly. Do NOT read existing files unless you absolutely must edit them.
- Use write_file to create new files, edit_file for targeted modifications.
- Use execute to run shell commands only when necessary (install packages, build).
- Be concise — report what you did in one sentence when done.
- Do NOT verify, review, or double-check your own work. Just write the files and finish.
- Do NOT read files you just created. Do NOT list directories after creating files.
- STOP as soon as your assigned task is complete. Do not do extra work.
"""


def _run_subagent_in_process(
    subagent_cfg: dict,
    description: str,
    label: str,
    model_config: dict,
    project_root: str,
    event_queue: mp.Queue,
) -> SubagentResult:
    """Rebuild graph and stream subagent in child process.

    Each subagent is a full Atom task agent (create_deep_agent) with skills,
    but without the orchestration layer (no planning, no sub-delegation).
    """
    from atom.core.agent import _resolve_model
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from atom.layers.sanitize import SanitizeMiddleware
    from atom.layers.stall_detector import StallDetectorMiddleware
    from langgraph.checkpoint.memory import MemorySaver

    model = _resolve_model(model_config["model_name"], model_config["provider"])

    subagent = create_deep_agent(
        name="atom-task",
        model=model,
        system_prompt=TASK_AGENT_PROMPT,
        tools=[],  # backend provides file I/O + shell; no extra tools needed
        backend=LocalShellBackend(
            root_dir=project_root,
            virtual_mode=False,
            inherit_env=True,
        ),
        # Auto-approve everything — main agent already approved the orchestration
        interrupt_on=None,
        checkpointer=MemorySaver(),
        middleware=[
            SanitizeMiddleware(),
            StallDetectorMiddleware(max_empty_turns=2),
        ],
    )

    # Stream the subagent
    thread_id = f"sub-{label}-{os.getpid()}-{int(time.time() * 1000)}"
    config = {"configurable": {"thread_id": thread_id}}
    input_payload = {"messages": [{"role": "user", "content": description}]}

    result = SubagentResult()
    pending_ops: dict[str, dict] = {}
    empty_turns = 0
    max_empty_turns = 3
    start_time = time.time()
    max_wall_time = 120

    def emit(event_type: str, **data):
        try:
            event_queue.put_nowait(SubagentEvent(
                label=label, event_type=event_type, data=data,
            ))
        except Exception:
            pass

    try:
        for event in subagent.stream(input_payload, config=config, stream_mode="updates"):
            if time.time() - start_time > max_wall_time:
                result.final_text += "\n[Sub-agent timed out]"
                break

            for node_name, node_output in event.items():
                if not isinstance(node_output, dict):
                    continue

                messages = node_output.get("messages")
                if messages is None:
                    continue
                if hasattr(messages, "value"):
                    messages = messages.value
                if not isinstance(messages, list):
                    continue

                for msg in messages:
                    msg_type = getattr(msg, "type", None)

                    if msg_type == "ai":
                        tool_calls = getattr(msg, "tool_calls", [])
                        for tc in tool_calls:
                            tc_name = tc.get("name", "?")
                            tc_args = tc.get("args", {})
                            # Emit verbose tool info: name + key arg summary
                            tool_summary = _format_tool_brief(tc_name, tc_args)
                            # Pass key args so status tracker can show file names
                            emit("tool_start", name=tc_name, summary=tool_summary,
                                 args=_extract_key_args(tc_name, tc_args))
                            result.tools_used.append({"name": tc_name})

                            if tc_name in ("edit_file", "write_file"):
                                tc_id = tc.get("id")
                                if tc_id:
                                    pending_ops[tc_id] = {
                                        "name": tc_name,
                                        "file_path": tc_args.get("file_path", ""),
                                    }

                        if tool_calls:
                            empty_turns = 0
                        else:
                            empty_turns += 1

                        content = getattr(msg, "content", "")
                        if content:
                            text = content if isinstance(content, str) else str(content)
                            if text.strip():
                                result.final_text = text
                                emit("ai_text", text=text[:500])

                    elif msg_type == "tool":
                        tool_call_id = getattr(msg, "tool_call_id", None)
                        tool_content = str(getattr(msg, "content", ""))
                        tool_name = getattr(msg, "name", "tool")
                        is_error = "error" in tool_content.lower()[:100]

                        # Emit verbose result preview
                        result_preview = tool_content[:200].replace("\n", " ")
                        emit("tool_end", name=tool_name, is_error=is_error,
                             result=result_preview)

                        if tool_name in ("write_file", "edit_file") and not is_error:
                            if tool_call_id and tool_call_id in pending_ops:
                                op = pending_ops.pop(tool_call_id)
                                file_path = op.get("file_path", "")
                                if file_path:
                                    result.files_modified.append(file_path)
                                emit("diff", text=f"● {op['name']}({os.path.basename(file_path)})")

                if empty_turns >= max_empty_turns:
                    result.final_text += "\n[Sub-agent stalled]"
                    break

    except Exception as e:
        result.final_text = f"Sub-agent error: {e}"
        emit("error", text=str(e)[:100])

    return result


def _extract_key_args(name: str, args: dict) -> dict:
    """Extract only the essential args for status tracking (avoids serializing large content)."""
    if name in ("write_file", "edit_file", "read_file"):
        return {"file_path": args.get("file_path", args.get("path", ""))}
    if name == "execute":
        return {"command": args.get("command", "")[:80]}
    return {}


def _format_tool_brief(name: str, args: dict) -> str:
    """Format a short summary of tool call for verbose display."""
    if name in ("write_file", "edit_file", "read_file"):
        path = args.get("file_path", args.get("path", ""))
        short = os.path.basename(path) if path else "?"
        return f"{name}({short})"
    if name == "execute":
        cmd = args.get("command", "")[:60]
        return f"$ {cmd}"
    if name in ("ls", "glob"):
        return f"{name}({args.get('path', args.get('pattern', ''))[:40]})"
    if name == "grep":
        return f"grep({args.get('pattern', '')[:30]})"
    if name in ("web_search_tool", "fetch_url_tool"):
        return f"{name}({args.get('query', args.get('url', ''))[:40]})"
    return name


# ─── Event collector (runs in parent process, main thread) ───

def _event_collector(event_queue: mp.Queue, halt: threading.Event):
    """Single thread that consumes events from child processes."""
    while not halt.is_set():
        try:
            event = event_queue.get(timeout=0.02)
        except (queue.Empty, EOFError):
            continue

        if _tracker:
            if event.event_type == "tool_start":
                _tracker.on_subagent_tool(
                    event.label,
                    event.data.get("name", "?"),
                    event.data.get("args", {}),
                )

        if _pane_manager:
            _pane_manager.update_subagent(event)
            # Mark tracker dirty so it re-renders with updated pane data
            if _tracker:
                _tracker._mark_dirty()


# ─── Background render thread ───

class RenderThread(threading.Thread):
    """Daemon thread that periodically refreshes the status dashboard."""

    def __init__(self, tracker, interval: float = 0.5):
        super().__init__(daemon=True)
        self._tracker = tracker
        self._interval = interval
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            self._tracker.render()
            self._halt.wait(self._interval)

    def shutdown(self):
        self._halt.set()
