"""Parallel sub-agent orchestrator.

Runs multiple sub-agents concurrently using ThreadPoolExecutor.
Each sub-agent streams events back to the StatusTracker in real-time,
so the CLI dashboard shows what every agent is doing simultaneously.

Architecture:
  Main agent (orchestrator) → calls orchestrate tool
    → ThreadPoolExecutor spawns N workers
      → Each worker runs subagent.stream() with unique thread_id
      → Worker pushes tool-call events to StatusTracker
    → All workers complete → combined results returned to main agent

  Meanwhile, a background RenderThread refreshes the dashboard every 0.5s.
"""
import time
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.tools import tool

from atom.utils import sanitize_text

# ─── Module-level state (set by create_atom_agent / CLI) ───
_subagent_registry: dict = {}   # name → compiled subagent graph
_tracker = None                  # StatusTracker instance


def register_subagents(subagents: dict):
    """Register pre-built subagent graphs for the orchestrator."""
    global _subagent_registry
    _subagent_registry = subagents


def set_tracker(tracker):
    """Set the StatusTracker for real-time event streaming."""
    global _tracker
    _tracker = tracker


# ─── Orchestrate tool ───

@tool
def orchestrate_tool(tasks_json: str) -> str:
    """Run multiple sub-agent tasks in PARALLEL. Use this instead of sequential task calls.

    Each task runs as an independent sub-agent with its own context.
    All tasks execute concurrently and their results are combined.

    Args:
        tasks_json: JSON array of task objects. Each object has:
            - "type": sub-agent type ("coder", "researcher", "explorer", "reviewer", "planner")
            - "task": detailed description of what the sub-agent should do

    Example:
        orchestrate_tool('[
            {"type": "coder", "task": "Create index.html with React setup and Vite config"},
            {"type": "coder", "task": "Create src/App.tsx with fibonacci dashboard component"},
            {"type": "coder", "task": "Create api/fibonacci.ts serverless function"}
        ]')
    """
    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"Error parsing tasks JSON: {e}"

    if not isinstance(tasks, list) or not tasks:
        return "Error: tasks must be a non-empty JSON array."

    results = _run_parallel(tasks)

    # Format combined results
    parts = []
    for name, result in results.items():
        result_text = sanitize_text(str(result))
        if len(result_text) > 3000:
            result_text = result_text[:3000] + "\n... (truncated)"
        parts.append(f"=== [{name}] ===\n{result_text}")

    return "\n\n".join(parts) or "(no results)"


# ─── Parallel execution engine ───

def _run_parallel(tasks: list[dict]) -> dict[str, str]:
    """Execute tasks in parallel and return {label: result} dict."""
    max_workers = min(len(tasks), 4)
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for i, task in enumerate(tasks):
            agent_type = task.get("type", "coder")
            description = task.get("task", task.get("description", ""))
            label = f"{agent_type}-{i}"

            subagent = _subagent_registry.get(agent_type)
            if subagent is None:
                results[label] = f"Unknown sub-agent type: {agent_type}"
                continue

            if _tracker:
                _tracker.on_subagent_start(label, description)
                # Mark corresponding plan item as in_progress
                # (offset by 1 because first todo is usually "setup" done by main agent)
                _tracker.set_plan_item_active(i + 1)

            future = pool.submit(
                _stream_subagent, subagent, label, description
            )
            futures[future] = label

        for future in as_completed(futures):
            label = futures[future]
            try:
                results[label] = future.result(timeout=600)
            except Exception as e:
                results[label] = f"Error: {e}"
            if _tracker:
                _tracker.on_subagent_end(label)
                _tracker.advance_plan()  # Mark next todo as completed

    return results


def _stream_subagent(subagent, label: str, description: str) -> str:
    """Run a single subagent with streaming, reporting events to tracker."""
    thread_id = f"sub-{label}-{int(time.time() * 1000)}"
    config = {"configurable": {"thread_id": thread_id}}
    input_payload = {"messages": [{"role": "user", "content": description}]}

    last_content = ""

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
                        # Report tool calls to tracker
                        for tc in getattr(msg, "tool_calls", []):
                            tc_name = tc.get("name", "?")
                            tc_args = tc.get("args", {})
                            if _tracker:
                                _tracker.on_subagent_tool(label, tc_name, tc_args)

                        content = getattr(msg, "content", "")
                        if content:
                            text = content if isinstance(content, str) else str(content)
                            if text.strip():
                                last_content = text

    except Exception as e:
        last_content = f"Sub-agent error: {sanitize_text(str(e))}"

    return last_content


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
        """Signal the thread to stop."""
        self._halt.set()
