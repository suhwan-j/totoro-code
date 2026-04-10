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

from totoro.utils import sanitize_text
from totoro.pane import SubagentEvent, SubagentResult, PaneManager

# ─── Module-level state (set by create_totoro_agent / CLI) ───
_subagent_configs: list[dict] = []   # serializable subagent configs
_model_config: dict = {}             # {model_name, provider} for rebuilding in child process
_project_root: str = "."
_tracker = None
_pane_manager: PaneManager | None = None
_plan_only: bool = False             # When True, catbus plans but does NOT auto-dispatch


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


def set_plan_only(enabled: bool):
    global _plan_only
    _plan_only = enabled


# ─── Orchestrate tool ───

@tool
def orchestrate_tool(tasks_json: str) -> str:
    """Run sub-agents in parallel. Input: JSON array of {"type": "<agent>", "task": "<description>"}.
    Types: catbus (plan), satsuki (code), mei (research), susuwatari (micro), tatsuo (review).

    If the only task is a catbus (planner), the plan is automatically executed:
    catbus returns a plan → execution agents are dispatched → results are returned.

    Args:
        tasks_json: JSON array, e.g. '[{"type":"satsuki","task":"Create index.html"}]'
    """
    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"Error parsing tasks JSON: {e}"

    if not isinstance(tasks, list) or not tasks:
        return "Error: tasks must be a non-empty JSON array."

    # ── Auto-dispatch: if only catbus tasks, run plan → execute automatically ──
    # Skip auto-dispatch in plan-only mode (user only wants the plan)
    catbus_only = all(t.get("type", "") == "catbus" for t in tasks)
    if catbus_only and not _plan_only:
        return _orchestrate_with_auto_dispatch(tasks)

    return _run_and_format(tasks)


def _run_and_format(tasks: list[dict]) -> str:
    """Run tasks in parallel and format results."""
    results = _run_parallel(tasks)

    MAX_RESULT_CHARS = 1500
    parts = []
    for name, result in results.items():
        if isinstance(result, SubagentResult):
            result_text = sanitize_text(result.final_text)
            if len(result_text) > MAX_RESULT_CHARS:
                result_text = result_text[:MAX_RESULT_CHARS] + "\n...(truncated)"
            files = ", ".join(result.files_modified[:5]) if result.files_modified else "none"
            parts.append(
                f"[{name}] {len(result.tools_used)} tools, files: {files}\n"
                f"{result_text}"
            )
        else:
            result_text = sanitize_text(str(result))
            if len(result_text) > MAX_RESULT_CHARS:
                result_text = result_text[:MAX_RESULT_CHARS] + "\n...(truncated)"
            parts.append(f"[{name}]\n{result_text}")

    return "\n\n".join(parts) or "(no results)"


def _inject_context_into_tasks(
    tasks: list[dict],
    original_request: str,
    plan_context: str,
) -> list[dict]:
    """Prepend original user request and plan context to each task description.

    Sub-agents run in separate processes with no conversation history.
    Without this context injection, they only see their individual task
    and can't understand the broader goal or how their work fits in.
    """
    if not original_request and not plan_context:
        return tasks

    context_header = ""
    if original_request:
        context_header += f"## Original User Request\n{original_request}\n\n"
    if plan_context:
        # Keep plan context brief to avoid overwhelming the sub-agent
        trimmed_plan = plan_context[:1000]
        if len(plan_context) > 1000:
            trimmed_plan += "\n...(plan truncated)"
        context_header += f"## Plan Context\n{trimmed_plan}\n\n"
    context_header += "## Your Task\n"

    enriched = []
    for task in tasks:
        task_copy = dict(task)
        desc = task_copy.get("task", task_copy.get("description", ""))
        task_copy["task"] = context_header + desc
        enriched.append(task_copy)
    return enriched


def _parse_plan_json(text: str) -> list[dict] | None:
    """Extract JSON task array from catbus plan output.

    Tries multiple strategies in order:
    1. Fenced code blocks: ```plan or ```json
    2. Any fenced code block containing a JSON array
    3. Raw JSON array anywhere in text
    4. Individual JSON objects on separate lines
    """
    import re

    # Strategy 1: ```plan or ```json fenced blocks
    fence_pattern = re.compile(r'```(?:plan|json)\s*\n(.*?)```', re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, list) and parsed:
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 2: Any fenced code block containing [
    any_fence = re.compile(r'```\w*\s*\n(.*?)```', re.DOTALL)
    for m in any_fence.finditer(text):
        content = m.group(1).strip()
        if content.startswith("["):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                    return parsed
            except json.JSONDecodeError:
                pass

    # Strategy 3: Raw JSON array (last occurrence of [{...},...])
    bracket_pattern = re.compile(r'\[\s*\{[\s\S]*?\}\s*\]')
    matches = list(bracket_pattern.finditer(text))
    for m in reversed(matches):
        try:
            parsed = json.loads(m.group())
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed
        except json.JSONDecodeError:
            continue

    # Strategy 4: Individual {"type":...,"task":...} objects on lines
    obj_pattern = re.compile(r'\{\s*"type"\s*:\s*"[^"]+"\s*,\s*"task"\s*:\s*"[^"]*"[^}]*\}')
    obj_matches = obj_pattern.findall(text)
    if obj_matches:
        tasks = []
        for m in obj_matches:
            try:
                tasks.append(json.loads(m))
            except json.JSONDecodeError:
                pass
        if tasks:
            return tasks

    return None


def _orchestrate_with_auto_dispatch(catbus_tasks: list[dict]) -> str:
    """Run catbus planner, parse plan, then auto-dispatch execution agents.

    Flow: catbus → parse plan JSON → run execution agents → return all results.
    """
    # Phase 1: Run catbus (suppress summary — will show combined at end)
    plan_results = _run_parallel(catbus_tasks, suppress_summary=True)

    # Collect plan text and parse execution tasks
    execution_tasks = []
    plan_summary_parts = []

    for name, result in plan_results.items():
        plan_text = result.final_text if isinstance(result, SubagentResult) else str(result)
        plan_summary_parts.append(f"[{name}] Plan:\n{sanitize_text(plan_text[:800])}")

        parsed = _parse_plan_json(plan_text)
        if parsed:
            # Validate each task has type and task/description
            for t in parsed:
                if isinstance(t, dict) and t.get("type") and (t.get("task") or t.get("description")):
                    # Don't allow catbus to spawn more catbus
                    if t.get("type") != "catbus":
                        execution_tasks.append(t)

    # Cap task count to prevent spawning too many subagents
    MAX_PARALLEL_TASKS = 5
    if len(execution_tasks) > MAX_PARALLEL_TASKS:
        import sys as _sys
        print(
            f"  [info] Plan has {len(execution_tasks)} tasks, capping to {MAX_PARALLEL_TASKS}",
            file=_sys.stderr, flush=True,
        )
        execution_tasks = execution_tasks[:MAX_PARALLEL_TASKS]

    if not execution_tasks:
        # Parsing failed — fall back to delegating original task to satsuki
        # Extract original task description from catbus tasks
        original_desc = catbus_tasks[0].get("task", catbus_tasks[0].get("description", ""))
        if original_desc:
            import sys as _sys
            print(f"  [info] Plan parsing failed, delegating to satsuki directly", file=_sys.stderr, flush=True)
            execution_tasks = [{"type": "satsuki", "task": original_desc}]
        else:
            hint = (
                "[Auto-dispatch failed] Could not parse plan and no original task found.\n"
                "Call orchestrate_tool with specific tasks."
            )
            return "\n\n".join(plan_summary_parts) + "\n\n" + hint

    # ── Inject context into execution tasks ──
    # Sub-agents have NO conversation history — make their tasks self-contained
    # by prepending the original user request and plan summary.
    original_request = catbus_tasks[0].get("task", catbus_tasks[0].get("description", ""))
    plan_context = "\n".join(plan_summary_parts)
    execution_tasks = _inject_context_into_tasks(
        execution_tasks, original_request, plan_context,
    )

    # Phase 2: Run execution agents
    exec_results = _run_parallel(execution_tasks)

    # Combine results
    MAX_RESULT_CHARS = 1500
    parts = []

    # Plan summary (brief)
    parts.append("── Plan ──\n" + "\n".join(plan_summary_parts))

    # Execution results
    parts.append("── Execution ──")
    for name, result in exec_results.items():
        if isinstance(result, SubagentResult):
            result_text = sanitize_text(result.final_text)
            if len(result_text) > MAX_RESULT_CHARS:
                result_text = result_text[:MAX_RESULT_CHARS] + "\n...(truncated)"
            files = ", ".join(result.files_modified[:5]) if result.files_modified else "none"
            parts.append(
                f"[{name}] {len(result.tools_used)} tools, files: {files}\n"
                f"{result_text}"
            )
        else:
            result_text = sanitize_text(str(result))
            if len(result_text) > MAX_RESULT_CHARS:
                result_text = result_text[:MAX_RESULT_CHARS] + "\n...(truncated)"
            parts.append(f"[{name}]\n{result_text}")

    return "\n\n".join(parts)


# ─── Parallel execution engine (multiprocessing) ───

def _run_parallel(tasks: list[dict], suppress_summary: bool = False) -> dict[str, SubagentResult | str]:
    """Execute tasks in parallel using multiprocessing + curses split-pane."""
    import curses as _curses
    from totoro.tui import SplitPaneTUI

    results: dict[str, SubagentResult | str] = {}
    event_queue: mp.Queue = mp.Queue(maxsize=2000)
    config_map = {cfg["name"]: cfg for cfg in _subagent_configs}
    import sys as _sys
    use_curses = (
        _pane_manager is not None
        and _tracker is not None
        and len(tasks) > 0
        and _sys.stdout.isatty()
    )

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
        agent_type = task.get("type", "satsuki")  # default to satsuki (senior agent)
        description = task.get("task", task.get("description", ""))
        label = f"{agent_type}-{i}"

        # Route to character-specific config
        cfg = config_map.get(agent_type)
        if cfg is None:
            import sys as _sys
            print(f"  [warn] Unknown agent type '{agent_type}', falling back to satsuki", file=_sys.stderr, flush=True)
            cfg = config_map.get("satsuki")
        if cfg is None:
            cfg = {"name": "susuwatari", "system_prompt": "You are Susuwatari, a micro agent. Execute the task directly.", "description": ""}

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
        except Exception as e:
            # curses.wrapper already calls endwin(), don't call it again
            err_msg = str(e)
            if "nocbreak" not in err_msg and "endwin" not in err_msg:
                print(f"  [warn] TUI error: {e}", file=_sys.stderr, flush=True)
        # Panel stays disabled — render_final_summary in cli.py will handle cleanup

    # Stop monitor
    monitor_halt.set()
    monitor.join(timeout=2)

    # Reap all processes — they should already be dead since TUI waited for is_active=False
    for label, p in processes.items():
        # Join with generous timeout
        p.join(timeout=30)

        # Force kill if still alive
        if p.is_alive():
            import sys as _sys
            print(f"\n  [warn] {label}: timed out, terminating...", file=_sys.stderr, flush=True)
            p.terminate()
            p.join(timeout=5)
        if p.is_alive():
            print(f"  [warn] {label}: force killing", file=_sys.stderr, flush=True)
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

    # Print summary after curses exits (skip if auto-dispatch will call again)
    if _pane_manager:
        if not suppress_summary:
            summary = _pane_manager.get_summary()
            if summary:
                from totoro.diff import safe_print
                safe_print(summary)
        _pane_manager.clear()

    # Keep panel disabled — the main agent will continue processing
    # and the render thread would show stale plan data otherwise.
    # Panel re-enables naturally when _stream_with_hitl restarts the render thread.
    if _tracker:
        _tracker._panel_enabled = False
        _tracker._clear_previous()
        _tracker._last_panel_lines = 0

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
    """Run in a child process. Routes to lightweight LLM call or full agent."""
    try:
        agent_name = subagent_cfg.get("name", "")
        if agent_name == "catbus":
            result = _run_lightweight_llm(
                subagent_cfg, description, label,
                model_config, project_root, event_queue,
            )
        else:
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


def _run_lightweight_llm(
    subagent_cfg: dict,
    description: str,
    label: str,
    model_config: dict,
    project_root: str,
    event_queue: mp.Queue,
) -> SubagentResult:
    """Single LLM call — no agent loop, no tools. Fast path for catbus (planner).

    Just sends system_prompt + user message and returns the response.
    """
    from totoro.core.agent import _resolve_model
    from langchain_core.messages import SystemMessage, HumanMessage

    def emit(event_type: str, **data):
        try:
            event_queue.put_nowait(SubagentEvent(
                label=label, event_type=event_type, data=data,
            ))
        except Exception:
            pass

    emit("tool_start", name="planning", summary="analyzing request")

    model = _resolve_model(model_config["model_name"], model_config["provider"])
    system_prompt = subagent_cfg.get("system_prompt", "")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Working directory: {project_root}\n\n{description}"),
    ]

    try:
        response = model.invoke(messages)
        text = response.content if isinstance(response.content, str) else str(response.content)
        # Capture token usage from response metadata
        usage = getattr(response, "usage_metadata", None) or {}
        if not usage:
            meta = getattr(response, "response_metadata", {})
            usage = meta.get("token_usage", meta.get("usage", {}))
        if usage:
            emit("tokens",
                 input=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                 output=usage.get("output_tokens", usage.get("completion_tokens", 0)))
    except Exception as e:
        text = f"Planning error: {e}"

    emit("ai_text", text=text[:500])
    emit("tool_end", name="planning", is_error=False, result=text[:200])

    return SubagentResult(final_text=text)


TASK_AGENT_RULES = """
## Rules
- Execute the given task immediately — do NOT plan, do NOT create todos, do NOT delegate.
- NEVER use the "task" tool. You do NOT have sub-agents. Do all work yourself directly.
- Be concise — report what you did in one sentence when done.
- Do NOT verify, review, or double-check your own work unless that IS your task.
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

    Uses create_agent() directly with a minimal middleware stack:
    - FilesystemMiddleware (file I/O + shell)
    - SanitizeMiddleware (strip surrogates)
    - StallDetectorMiddleware (detect loops)
    - PatchToolCallsMiddleware (fix dangling tool calls)

    Excludes TodoList, SubAgent, Skills, Summarization middleware
    that create_deep_agent() would auto-add (~3,000+ tokens saved per subagent).
    """
    from totoro.core.agent import _resolve_model
    from langchain.agents import create_agent
    from deepagents.backends import LocalShellBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
    from totoro.layers.sanitize import SanitizeMiddleware
    from totoro.layers.stall_detector import StallDetectorMiddleware
    from langgraph.checkpoint.memory import MemorySaver

    model = _resolve_model(model_config["model_name"], model_config["provider"])

    # Use character-specific system prompt + shared task rules
    character_prompt = subagent_cfg.get("system_prompt", "") + TASK_AGENT_RULES
    character_name = subagent_cfg.get("name", "totoro-task")

    backend = LocalShellBackend(
        root_dir=project_root,
        virtual_mode=False,
        inherit_env=True,
    )

    # Minimal middleware — subagents do one focused task, no planning/delegation
    middleware = [
        FilesystemMiddleware(backend=backend),
        PatchToolCallsMiddleware(),
        SanitizeMiddleware(),
        StallDetectorMiddleware(max_empty_turns=2),
    ]

    subagent = create_agent(
        model=model,
        system_prompt=character_prompt,
        tools=[],
        middleware=middleware,
        checkpointer=MemorySaver(),
        name=character_name,
    ).with_config({"recursion_limit": 9_999})

    # Stream the subagent
    thread_id = f"sub-{label}-{os.getpid()}-{int(time.time() * 1000)}"
    config = {"configurable": {"thread_id": thread_id}}
    input_payload = {"messages": [{"role": "user", "content": description}]}

    result = SubagentResult()
    pending_ops: dict[str, dict] = {}
    empty_turns = 0
    max_empty_turns = 3

    def emit(event_type: str, **data):
        try:
            event_queue.put_nowait(SubagentEvent(
                label=label, event_type=event_type, data=data,
            ))
        except Exception:
            pass

    try:
        for event in subagent.stream(input_payload, config=config, stream_mode="updates"):

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
                        # Capture token usage from AI messages
                        usage = getattr(msg, "usage_metadata", None) or {}
                        if not usage:
                            meta = getattr(msg, "response_metadata", {})
                            usage = meta.get("token_usage", meta.get("usage", {}))
                        if usage:
                            emit("tokens",
                                 input=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                                 output=usage.get("output_tokens", usage.get("completion_tokens", 0)))

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
            try:
                self._tracker.render()
            except Exception:
                pass  # Don't crash the render thread on display errors
            self._halt.wait(self._interval)

    def shutdown(self):
        self._halt.set()
