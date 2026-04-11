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
_auto_approve: bool = False          # When True, subagents skip HITL approval
_allow_patterns: list[str] = []     # Permission allow patterns from settings.json
_runtime_auto_approve: bool = False  # Set True when user chooses "Approve All" during session


def register_subagent_configs(configs: list[dict], model_name: str, provider: str, project_root: str):
    """Register serializable subagent configs for multiprocessing.

    Args:
        configs: List of serializable subagent configuration dicts.
        model_name: Name of the model to use in child processes.
        provider: LLM provider identifier.
        project_root: Absolute path to the project root directory.
    """
    global _subagent_configs, _model_config, _project_root
    _subagent_configs = configs
    _model_config = {"model_name": model_name, "provider": provider}
    _project_root = project_root


def set_tracker(tracker):
    """Set the status tracker for subagent monitoring.

    Args:
        tracker: StatusTracker instance for rendering subagent progress.
    """
    global _tracker
    _tracker = tracker


def set_pane_manager(pane_manager: PaneManager | None):
    """Set the pane manager for split-pane TUI rendering.

    Args:
        pane_manager: PaneManager instance or None to disable.
    """
    global _pane_manager
    _pane_manager = pane_manager


def set_plan_only(enabled: bool):
    """Set plan-only mode for catbus orchestration.

    Args:
        enabled: When True, catbus plans but does not auto-dispatch.
    """
    global _plan_only
    _plan_only = enabled


def set_auto_approve(enabled: bool):
    """Set auto-approve mode for subagent HITL.

    Args:
        enabled: When True, subagents skip tool approval prompts.
    """
    global _auto_approve
    _auto_approve = enabled


def set_allow_patterns(patterns: list[str]):
    """Set permission allow patterns for subagent HITL.

    Patterns are matched against tool names and command strings.
    "*" means approve everything. "mkdir" matches execute commands
    starting with "mkdir". "write_file" matches all file writes.

    Args:
        patterns: List of glob-style permission patterns.
    """
    global _allow_patterns
    _allow_patterns = patterns


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
    """Run tasks in parallel and format results.

    Args:
        tasks: List of task dicts with "type" and "task" keys.

    Returns:
        Formatted string combining all subagent results.
    """
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

    Args:
        tasks: List of task dicts to enrich with context.
        original_request: The user's original request text.
        plan_context: Summary of the catbus plan output.

    Returns:
        New list of task dicts with context prepended to descriptions.
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

    Args:
        text: Raw text output from catbus planner.

    Returns:
        Parsed list of task dicts, or None if parsing fails.
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

    Args:
        catbus_tasks: List of catbus planner task dicts.

    Returns:
        Combined formatted string of plan summary and execution results.
    """
    # Phase 1: Run catbus planner (suppress summary — will show combined at end)
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

    # Build plan display (used both before and after execution)
    from totoro.colors import DIM, BOLD, BLUE, AMBER_LT, RESET, IVORY
    task_lines = []
    for i, t in enumerate(execution_tasks):
        desc = t.get("task", t.get("description", ""))
        # Strip injected context headers — show only the actual task
        if "## Your Task\n" in desc:
            desc = desc.split("## Your Task\n")[-1]
        agent_type = t.get("type", "?")
        task_lines.append(f"  {DIM}{i+1}.{RESET} {BLUE}{agent_type}{RESET} {IVORY}{desc[:80]}{RESET}")

    plan_header = f"{DIM}── {AMBER_LT}Plan{DIM} ({len(execution_tasks)} tasks) ──{RESET}"
    plan_display_colored = plan_header + "\n" + "\n".join(task_lines)
    # Plain text version for tool result (no ANSI)
    plan_display_plain = f"Plan ({len(execution_tasks)} tasks):\n" + "\n".join(
        f"  {i+1}. [{t.get('type')}] {(t.get('task','') if '## Your Task' not in t.get('task','') else t.get('task','').split('## Your Task\n')[-1])[:80]}"
        for i, t in enumerate(execution_tasks)
    )

    # Print plan before curses TUI starts (visible in scrollback)
    from totoro.diff import safe_print
    safe_print(f"\n{plan_display_colored}\n")

    # Phase 2: Split tatsuo (verification) from workers — tatsuo runs AFTER workers complete
    worker_tasks = [t for t in execution_tasks if t.get("type") != "tatsuo"]
    verify_tasks = [t for t in execution_tasks if t.get("type") == "tatsuo"]

    # Run workers first
    exec_results = _run_parallel(worker_tasks) if worker_tasks else {}

    # Phase 3: Verify → Fix → Re-verify loop (max 3 rounds)
    MAX_RETRY = 3
    all_verify_results = {}

    for attempt in range(MAX_RETRY):
        if not verify_tasks:
            break

        if _pane_manager:
            _pane_manager.clear()

        verify_results = _run_parallel(verify_tasks)
        all_verify_results.update(verify_results)

        # Check if tatsuo found failures
        failures = []
        for name, result in verify_results.items():
            text = result.final_text if isinstance(result, SubagentResult) else str(result)
            text_lower = text.lower()
            if any(kw in text_lower for kw in ("fail", "error", "broken", "not working", "does not",
                                                 "cannot", "missing", "crash", "exception", "bug")):
                failures.append((name, text))

        if not failures:
            break  # All verification passed

        if attempt >= MAX_RETRY - 1:
            # Last attempt — don't retry, just report
            from totoro.diff import safe_print
            safe_print(f"\n  \033[31m[verify] {len(failures)} issue(s) remain after {MAX_RETRY} retries\033[0m")
            break

        # Build fix tasks from failure descriptions
        from totoro.diff import safe_print
        safe_print(f"\n  \033[33m[verify] {len(failures)} issue(s) found — auto-fix attempt {attempt + 1}/{MAX_RETRY}\033[0m")

        fix_tasks = []
        failure_context = "\n".join(f"- {text[:300]}" for _, text in failures)
        for _, failure_text in failures:
            fix_tasks.append({
                "type": "satsuki",
                "task": (
                    f"Fix the following verification failures:\n{failure_context}\n\n"
                    f"The original request was: {original_request[:200]}"
                ),
            })
        # Deduplicate — one satsuki fix task is enough
        fix_tasks = fix_tasks[:1]
        fix_tasks = _inject_context_into_tasks(fix_tasks, original_request, failure_context)

        if _pane_manager:
            _pane_manager.clear()
        fix_results = _run_parallel(fix_tasks)
        exec_results.update(fix_results)
        # Loop back to re-verify

    exec_results.update(all_verify_results)

    # Combine results (returned to main agent as tool result — no ANSI colors)
    MAX_RESULT_CHARS = 1500
    parts = [plan_display_plain]

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

    parts.append(
        "── IMPORTANT ──\n"
        "All tasks above are complete. You MUST now respond to the user with a summary of what was done. "
        "Include: files created/modified, key decisions made, and how to run/use the result."
    )

    return "\n\n".join(parts)


# ─── Parallel execution engine (multiprocessing) ───

def _run_parallel(tasks: list[dict], suppress_summary: bool = False) -> dict[str, SubagentResult | str]:
    """Execute tasks in parallel using multiprocessing + curses split-pane.

    Args:
        tasks: List of task dicts with "type" and "task" keys.
        suppress_summary: When True, skip printing summary after completion.

    Returns:
        Dict mapping subagent labels to their SubagentResult or error string.
    """
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

    # HITL support: threading queue for parent-side HITL requests
    hitl_pending: queue.Queue = queue.Queue()

    # Start event collector thread
    collector_halt = threading.Event()
    collector = threading.Thread(
        target=_event_collector,
        args=(event_queue, collector_halt, hitl_pending),
        daemon=True,
    )
    collector.start()

    # Register subagents and start processes
    processes: dict[str, mp.Process] = {}
    result_holders: dict[str, mp.Queue] = {}
    response_holders: dict[str, mp.Queue] = {}  # parent→child for HITL responses

    for i, task in enumerate(tasks):
        agent_type = task.get("type", "satsuki")  # default to satsuki (senior agent)
        description = task.get("task", task.get("description", ""))
        label = f"{agent_type}-{i}"

        # Extract display-friendly description (strip injected context headers)
        display_desc = description
        if "## Your Task\n" in display_desc:
            display_desc = display_desc.split("## Your Task\n")[-1]
        display_desc = display_desc.strip()[:120]

        # Route to character-specific config
        cfg = config_map.get(agent_type)
        if cfg is None:
            import sys as _sys
            print(f"  [warn] Unknown agent type '{agent_type}', falling back to satsuki", file=_sys.stderr, flush=True)
            cfg = config_map.get("satsuki")
        if cfg is None:
            cfg = {"name": "susuwatari", "system_prompt": "You are Susuwatari, a micro agent. Execute the task directly.", "description": ""}

        if _tracker:
            _tracker.on_subagent_start(label, display_desc)
            _tracker.set_plan_item_active(i + 1)
        if _pane_manager:
            _pane_manager.add_subagent(label, display_desc)

        result_q: mp.Queue = mp.Queue(maxsize=1)
        response_q: mp.Queue = mp.Queue(maxsize=10)
        result_holders[label] = result_q
        response_holders[label] = response_q

        p = mp.Process(
            target=_worker_process,
            args=(cfg, description, label, _model_config, _project_root,
                  event_queue, result_q, response_q, _auto_approve or _runtime_auto_approve),
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
        # Must hold lock to prevent race with render thread's stdout writes
        with _tracker._lock:
            _tracker._panel_enabled = False
            _tracker._clear_previous()
            _tracker._last_panel_lines = 0

        tui = SplitPaneTUI(
            tracker=_tracker, pane_manager=_pane_manager,
            hitl_pending=hitl_pending, response_queues=response_holders,
        )
        # Inherit runtime auto-approve from previous TUI session (e.g. retry loop)
        if _runtime_auto_approve or _auto_approve:
            tui._global_auto_approve = True
        try:
            _curses.wrapper(tui.run)
        except Exception as e:
            # curses.wrapper already calls endwin(), don't call it again
            err_msg = str(e)
            if "nocbreak" not in err_msg and "endwin" not in err_msg:
                print(f"  [warn] TUI error: {e}", file=_sys.stderr, flush=True)
        # Panel stays disabled — render_final_summary in cli.py will handle cleanup
    else:
        # Non-curses: poll for HITL requests and process completion
        while _pane_manager and _pane_manager.is_active:
            if _runtime_auto_approve or _auto_approve:
                # Auto-approve: drain all pending silently
                try:
                    ev = hitl_pending.get(timeout=0.5)
                    rq = response_holders.get(ev.label)
                    if rq:
                        rq.put({"decisions": [{"type": "approve"}]}, timeout=1)
                    _pane_manager.update_subagent(SubagentEvent(
                        label=ev.label, event_type="hitl_response", data={}))
                except queue.Empty:
                    pass
            else:
                try:
                    hitl_event = hitl_pending.get(timeout=0.5)
                    _handle_hitl_no_curses(hitl_event, response_holders)
                except queue.Empty:
                    pass

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

    # Disable panel BEFORE printing summary to prevent render thread race.
    # Must hold lock so render thread can't write to stdout concurrently.
    if _tracker:
        with _tracker._lock:
            _tracker._panel_enabled = False
            _tracker._clear_previous()
            _tracker._last_panel_lines = 0

    # Enrich pane metadata from collected results so the summary
    # includes per-agent file lists and a one-line description.
    if _pane_manager:
        for label, result in results.items():
            if isinstance(result, SubagentResult):
                with _pane_manager._lock:
                    pane = _pane_manager.panes.get(label)
                    if pane:
                        if result.files_modified:
                            pane.files = list(result.files_modified)
                        # Extract first meaningful line as one-line summary
                        if result.final_text:
                            for line in result.final_text.strip().splitlines():
                                line = line.strip()
                                if line and not line.startswith(('#', '```', '---')):
                                    pane.summary_text = line
                                    break

    # Print summary + file change list after panel is fully disabled.
    if _pane_manager:
        if not suppress_summary:
            summary = _pane_manager.get_summary()
            if summary:
                from totoro.diff import safe_print
                safe_print(summary)
                # Collect and display deduplicated file paths from all subagents
                all_files = []
                for label, result in results.items():
                    if isinstance(result, SubagentResult):
                        for f in result.files_modified:
                            all_files.append(f)
                if all_files:
                    from totoro.colors import BLUE, DIM, RESET
                    import os
                    unique_files = list(dict.fromkeys(all_files))
                    for fp in unique_files[:10]:
                        try:
                            rel = os.path.relpath(fp)
                        except ValueError:
                            rel = fp
                        safe_print(f"  {DIM}⎿{RESET} {BLUE}{rel}{RESET}")
                    if len(unique_files) > 10:
                        safe_print(f"  {DIM}  +{len(unique_files) - 10} more files{RESET}")
        _pane_manager.clear()

    return results


def _process_monitor(
    processes: dict[str, mp.Process],
    pane_manager: PaneManager | None,
    tracker,
    halt: threading.Event,
):
    """Monitor thread: detect child process exit and mark panes done so TUI can exit.

    This solves the deadlock where TUI waits for is_active=False but
    complete_subagent() was only called after TUI exit.

    Args:
        processes: Dict mapping labels to multiprocessing.Process instances.
        pane_manager: PaneManager instance or None.
        tracker: StatusTracker instance or None.
        halt: Threading event to signal this monitor to stop.
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
    response_queue: "mp.Queue | None" = None,
    auto_approve: bool = False,
):
    """Run in a child process. Routes to lightweight LLM call or full agent.

    Args:
        subagent_cfg: Serializable config dict for the subagent character.
        description: Task description to execute.
        label: Unique label for this worker (e.g. "satsuki-0").
        model_config: Dict with "model_name" and "provider" for rebuilding model.
        project_root: Absolute path to the project root directory.
        event_queue: Multiprocessing queue for streaming events to parent.
        result_queue: Multiprocessing queue to return the final SubagentResult.
        response_queue: Multiprocessing queue for HITL responses from parent.
        auto_approve: Whether to skip HITL approval prompts.
    """
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
                response_queue=response_queue,
                auto_approve=auto_approve,
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
    """Single LLM call with no agent loop or tools. Fast path for catbus planner.

    Just sends system_prompt + user message and returns the response.

    Args:
        subagent_cfg: Config dict containing "system_prompt" for the planner.
        description: Task description to plan for.
        label: Unique label for this worker.
        model_config: Dict with "model_name" and "provider" for model resolution.
        project_root: Absolute path to the project root directory.
        event_queue: Multiprocessing queue for streaming events to parent.

    Returns:
        SubagentResult containing the planner's response text.
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
            cached = usage.get("cache_read_input_tokens", 0)
            if not cached:
                details = usage.get("prompt_tokens_details", {})
                if isinstance(details, dict):
                    cached = details.get("cached_tokens", 0)
            emit("tokens",
                 input=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                 output=usage.get("output_tokens", usage.get("completion_tokens", 0)),
                 cached=cached)
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

## Error Handling
- If a command fails, read the error output carefully and fix the root cause.
- You may retry a failed command up to 3 times MAX. After 3 failures, STOP and report the error.
- Do NOT keep retrying the same approach. If it failed twice, try a completely different approach.
- NEVER loop endlessly — if you cannot fix it in 3 attempts, report what went wrong and stop.

## Shell Commands
- The `execute` tool runs commands from the project root directory.
- Each execute call is a separate subprocess — `cd` does NOT persist between calls.
- To run commands in a different directory, chain with `cd`: `cd ~/todo-app && npm test`
- ALWAYS use `cd <target> && <command>` when working outside the project root.
"""


def _run_subagent_in_process(
    subagent_cfg: dict,
    description: str,
    label: str,
    model_config: dict,
    project_root: str,
    event_queue: mp.Queue,
    response_queue: "mp.Queue | None" = None,
    auto_approve: bool = False,
) -> SubagentResult:
    """Rebuild graph and stream subagent in child process.

    Uses create_agent() directly with a minimal middleware stack:
    - FilesystemMiddleware (file I/O + shell)
    - SanitizeMiddleware (strip surrogates)
    - StallDetectorMiddleware (detect loops)
    - PatchToolCallsMiddleware (fix dangling tool calls)

    Excludes TodoList, SubAgent, Skills, Summarization middleware
    that create_deep_agent() would auto-add (~3,000+ tokens saved per subagent).

    Args:
        subagent_cfg: Config dict with "name" and "system_prompt" for the character.
        description: Task description to execute.
        label: Unique label for this worker.
        model_config: Dict with "model_name" and "provider" for model resolution.
        project_root: Absolute path to the project root directory.
        event_queue: Multiprocessing queue for streaming events to parent.

    Returns:
        SubagentResult with final text, tools used, and files modified.
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

    # Use home directory as root so subagents can freely work with any
    # user project (e.g. ~/todo-app) without cd workarounds.
    # virtual_mode=False means absolute paths still work regardless of root_dir.
    from pathlib import Path
    home_dir = str(Path.home())
    backend = LocalShellBackend(
        root_dir=home_dir,
        virtual_mode=False,
        inherit_env=True,
    )

    # Minimal middleware — subagents do one focused task, no planning/delegation
    fs_middleware = FilesystemMiddleware(backend=backend)

    # Filter filesystem tools by agent role to reduce token overhead
    # mei (researcher): read-only — no write, edit, execute (~848 tokens saved)
    # tatsuo (reviewer): read + execute — no write, edit (~156 tokens saved)
    # satsuki, susuwatari: all tools (need full file I/O)
    _TOOL_PROFILES = {
        "mei":     {"ls", "read_file", "glob", "grep"},
        "tatsuo":  {"ls", "read_file", "glob", "grep", "execute"},
    }
    allowed = _TOOL_PROFILES.get(character_name)
    if allowed is not None:
        fs_middleware.tools = [t for t in fs_middleware.tools if t.name in allowed]

    middleware = [
        fs_middleware,
        PatchToolCallsMiddleware(),
        SanitizeMiddleware(),
        StallDetectorMiddleware(max_empty_turns=2),
    ]

    # Add HITL middleware for agents with write/execute tools (not read-only mei)
    if not auto_approve and response_queue is not None and character_name != "mei":
        from totoro.layers.subagent_hitl import SubagentHITLMiddleware
        hitl_tools = {"write_file": True, "edit_file": True, "execute": True}
        # Only intercept tools this agent actually has
        agent_tool_names = {t.name for t in fs_middleware.tools}
        hitl_tools = {k: v for k, v in hitl_tools.items() if k in agent_tool_names}
        if hitl_tools:
            middleware.append(SubagentHITLMiddleware(
                interrupt_on=hitl_tools,
                event_queue=event_queue,
                response_queue=response_queue,
                label=label,
                allow_patterns=_allow_patterns,
            ))

    subagent = create_agent(
        model=model,
        system_prompt=character_prompt,
        tools=[],
        middleware=middleware,
        checkpointer=MemorySaver(),
        name=character_name,
    ).with_config({"recursion_limit": 2000})

    # Stream the subagent
    thread_id = f"sub-{label}-{os.getpid()}-{int(time.time() * 1000)}"
    config = {"configurable": {"thread_id": thread_id}}
    # Provide filesystem context without biasing toward a specific directory.
    # The task description (with injected user request) guides where to work.
    from pathlib import Path as _Path
    user_msg = (
        f"Environment:\n"
        f"- Home: {_Path.home()}\n"
        f"- CLI project: {project_root}\n\n"
        f"{description}"
    )
    input_payload = {"messages": [{"role": "user", "content": user_msg}]}

    result = SubagentResult()
    pending_ops: dict[str, dict] = {}
    empty_turns = 0
    max_empty_turns = 3
    max_execution_seconds = 600  # 10 min absolute safety net
    first_event_timeout = 300    # 5 min to get first response from API (accounts for rate limits)
    start_time = time.time()
    got_first_event = False

    def emit(event_type: str, **data):
        try:
            event_queue.put_nowait(SubagentEvent(
                label=label, event_type=event_type, data=data,
            ))
        except Exception:
            pass

    # Stream in a thread so we can detect API-level hangs
    import queue as _queue
    stream_queue: _queue.Queue = _queue.Queue(maxsize=100)
    stream_error: list = []

    def _stream_worker():
        try:
            for event in subagent.stream(input_payload, config=config, stream_mode="updates"):
                stream_queue.put(event)
            stream_queue.put(None)  # sentinel: stream done
        except Exception as e:
            stream_error.append(e)
            stream_queue.put(None)

    stream_thread = threading.Thread(target=_stream_worker, daemon=True)
    stream_thread.start()

    try:
        while True:
            # Calculate timeout: shorter before first event, longer after
            if not got_first_event:
                wait_timeout = max(0.1, first_event_timeout - (time.time() - start_time))
            else:
                wait_timeout = 5.0

            try:
                event = stream_queue.get(timeout=wait_timeout)
            except _queue.Empty:
                # Check for first-event timeout (API not responding)
                if not got_first_event and (time.time() - start_time) > first_event_timeout:
                    result.final_text += f"\n[API not responding after {first_event_timeout}s]"
                    emit("error", text=f"API not responding after {first_event_timeout}s")
                    break
                # Check absolute timeout
                if time.time() - start_time > max_execution_seconds:
                    result.final_text += f"\n[Subagent timed out after {max_execution_seconds}s]"
                    emit("error", text=f"Timed out after {max_execution_seconds}s")
                    break
                continue

            if event is None:
                # Stream ended (normal or error)
                if stream_error:
                    raise stream_error[0]
                break

            got_first_event = True

            # Absolute safety net
            if time.time() - start_time > max_execution_seconds:
                result.final_text += f"\n[Subagent timed out after {max_execution_seconds}s]"
                emit("error", text=f"Timed out after {max_execution_seconds}s")
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
                        # Capture token usage from AI messages
                        usage = getattr(msg, "usage_metadata", None) or {}
                        if not usage:
                            meta = getattr(msg, "response_metadata", {})
                            usage = meta.get("token_usage", meta.get("usage", {}))
                        if usage:
                            cached = usage.get("cache_read_input_tokens", 0)
                            if not cached:
                                details = usage.get("prompt_tokens_details", {})
                                if isinstance(details, dict):
                                    cached = details.get("cached_tokens", 0)
                            emit("tokens",
                                 input=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                                 output=usage.get("output_tokens", usage.get("completion_tokens", 0)),
                                 cached=cached)

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
        err_str = str(e)
        # Detect common API errors for clear messaging
        if "rate" in err_str.lower() or "429" in err_str:
            err_msg = f"API rate limit: {err_str[:80]}"
        elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
            err_msg = f"API timeout: {err_str[:80]}"
        elif "401" in err_str or "403" in err_str or "auth" in err_str.lower():
            err_msg = f"API auth error: {err_str[:80]}"
        elif "connection" in err_str.lower() or "network" in err_str.lower():
            err_msg = f"Network error: {err_str[:80]}"
        else:
            err_msg = f"Error: {err_str[:100]}"
        result.final_text = f"Sub-agent error: {err_msg}"
        emit("error", text=err_msg)

    return result


def _extract_key_args(name: str, args: dict) -> dict:
    """Extract key args for status tracking and TUI display.

    Includes content preview for write/edit operations so the TUI
    can show Claude Code-style file content output.

    Args:
        name: Tool name.
        args: Full tool call arguments dict.

    Returns:
        Dict with key arguments relevant for display.
    """
    if name == "write_file":
        content = args.get("content", "")
        lines = content.split("\n")
        preview = [line[:100] for line in lines[:12]]
        return {
            "file_path": args.get("file_path", args.get("path", "")),
            "content_preview": preview,
            "line_count": len(lines),
        }
    if name == "edit_file":
        new_str = args.get("new_string", "")
        lines = new_str.split("\n")
        preview = [line[:100] for line in lines[:8]]
        return {
            "file_path": args.get("file_path", args.get("path", "")),
            "content_preview": preview,
            "line_count": len(lines),
        }
    if name == "read_file":
        return {"file_path": args.get("file_path", args.get("path", ""))}
    if name == "execute":
        return {"command": args.get("command", "")[:200]}
    return {}


def _format_tool_brief(name: str, args: dict) -> str:
    """Format a short summary of tool call for verbose display.

    Args:
        name: Tool name.
        args: Tool call arguments dict.

    Returns:
        Short human-readable summary string (e.g. "edit_file(main.py)").
    """
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

def _event_collector(event_queue: mp.Queue, halt: threading.Event,
                     hitl_pending: queue.Queue | None = None):
    """Single thread that consumes events from child processes.

    Args:
        event_queue: Multiprocessing queue receiving SubagentEvents from workers.
        halt: Threading event to signal this collector to stop.
        hitl_pending: Threading queue to relay HITL requests to the TUI thread.
    """
    while not halt.is_set():
        try:
            event = event_queue.get(timeout=0.02)
        except (queue.Empty, EOFError):
            continue

        # Route HITL requests to TUI thread for user interaction
        if event.event_type == "hitl_request" and hitl_pending is not None:
            hitl_pending.put(event)
            # Also update pane state to show "waiting_approval"
            if _pane_manager:
                _pane_manager.update_subagent(event)
                if _tracker:
                    _tracker._mark_dirty()
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


def _handle_hitl_no_curses(event: 'SubagentEvent', response_holders: dict):
    """Handle HITL request without curses (non-tty or fallback).

    Args:
        event: SubagentEvent with hitl_request data.
        response_holders: Dict mapping labels to response mp.Queues.
    """
    label = event.label
    tool_requests = event.data.get("tool_requests", [])
    decisions = []

    for tr in tool_requests:
        tool_name = tr.get("name", "?")
        tool_args = tr.get("args", {})
        print(f"\n  \033[33m[APPROVAL REQUIRED]\033[0m \033[1m{label}\033[0m → \033[1m{tool_name}\033[0m")
        if isinstance(tool_args, dict):
            for k, v in tool_args.items():
                v_str = str(v)
                if len(v_str) > 200:
                    v_str = v_str[:200] + "..."
                print(f"    {k}: {v_str}")
        print(f"  \033[1m(a)\033[0mpprove / \033[1m(A)\033[0mpprove all / \033[1m(r)\033[0meject / \033[1m(e)\033[0mdit / \033[1m(x)\033[0m abort ?")

        try:
            choice = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            decisions.append({"type": "reject", "message": "Aborted"})
            break

        if choice in ("A", "approve all", "aa"):
            decisions.append({"type": "approve_all"})
            break
        elif choice.lower() in ("r", "reject", "n", "no"):
            decisions.append({"type": "reject", "message": f"User rejected {tool_name}"})
        elif choice.lower() in ("e", "edit"):
            try:
                edit_instruction = input("  How to change? > ").strip()
            except (EOFError, KeyboardInterrupt):
                decisions.append({"type": "approve"})
                continue
            if not edit_instruction or not isinstance(tool_args, dict):
                decisions.append({"type": "approve"})
            else:
                edited_args = dict(tool_args)
                if "=" in edit_instruction and " " not in edit_instruction.split("=")[0]:
                    key, val = edit_instruction.split("=", 1)
                    edited_args[key.strip()] = val.strip()
                decisions.append({
                    "type": "edit",
                    "edited_action": {"name": tool_name, "args": edited_args},
                })
        elif choice.lower() in ("x", "abort", "q"):
            decisions.append({"type": "reject", "message": "Aborted"})
            break
        else:
            decisions.append({"type": "approve"})

    response_q = response_holders.get(label)
    if response_q:
        try:
            response_q.put({"decisions": decisions}, timeout=1)
        except Exception:
            pass

    # Update pane status back to running
    if _pane_manager:
        _pane_manager.update_subagent(SubagentEvent(
            label=label, event_type="hitl_response", data={},
        ))


# ─── Background render thread ───

class RenderThread(threading.Thread):
    """Daemon thread that periodically refreshes the status dashboard."""

    def __init__(self, tracker, interval: float = 0.5):
        """Initialize the render thread.

        Args:
            tracker: StatusTracker instance to render.
            interval: Refresh interval in seconds.
        """
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
