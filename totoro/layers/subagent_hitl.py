"""Queue-based HITL middleware for subagent processes.

Replaces LangGraph's interrupt() mechanism with IPC queues so that
subagent child processes can request approval from the parent process.
"""

import fnmatch
import queue
import uuid
import multiprocessing as mp
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage


def _matches_allow(tool_name: str, tool_args: dict, patterns: list[str]) -> bool:
    """Check if a tool call matches any allow pattern.

    Patterns:
      "*"           → matches everything
      "write_file"  → matches write_file tool
      "mkdir"       → matches execute commands starting with "mkdir"
      "npm *"       → matches execute commands starting with "npm "
      "*.py"        → matches write_file/edit_file to .py files
    """
    for pat in patterns:
        if pat == "*":
            return True
        # Direct tool name match
        if fnmatch.fnmatch(tool_name, pat):
            return True
        # For execute: match against command string
        if tool_name == "execute":
            cmd = tool_args.get("command", "")
            if fnmatch.fnmatch(cmd, pat) or fnmatch.fnmatch(cmd, f"{pat}*"):
                return True
            # Also match first word (e.g. "mkdir" matches "mkdir -p /foo")
            first_word = cmd.split()[0] if cmd.split() else ""
            if fnmatch.fnmatch(first_word, pat):
                return True
        # For file ops: match against file path
        if tool_name in ("write_file", "edit_file", "read_file"):
            fpath = tool_args.get("file_path", tool_args.get("path", ""))
            if fnmatch.fnmatch(fpath, pat):
                return True
            import os
            if fnmatch.fnmatch(os.path.basename(fpath), pat):
                return True
    return False


class SubagentHITLMiddleware(AgentMiddleware):
    """Intercepts dangerous tool calls and requests approval via IPC queues.

    Instead of calling interrupt() (which requires LangGraph's Command/resume),
    this middleware blocks on a response queue waiting for the parent process
    to relay the user's decision.
    """

    def __init__(
        self,
        interrupt_on: dict[str, bool],
        event_queue: mp.Queue,
        response_queue: mp.Queue,
        label: str,
        allow_patterns: list[str] | None = None,
    ):
        super().__init__()
        self.interrupt_on = {k for k, v in interrupt_on.items() if v}
        self.event_queue = event_queue
        self.response_queue = response_queue
        self.label = label
        self.allow_patterns = allow_patterns or []
        self._auto_approve = False

    def after_model(self, state, runtime) -> dict[str, Any] | None:
        """Check tool calls and request approval for dangerous ones."""
        if self._auto_approve:
            return None

        messages = state["messages"]
        if not messages:
            return None

        last_ai_msg = next(
            (msg for msg in reversed(messages) if isinstance(msg, AIMessage)),
            None,
        )
        if not last_ai_msg or not last_ai_msg.tool_calls:
            return None

        # Find tool calls that need approval (skip if allowed by pattern)
        needs_approval = []
        for idx, tc in enumerate(last_ai_msg.tool_calls):
            if tc["name"] in self.interrupt_on:
                if _matches_allow(tc["name"], tc["args"], self.allow_patterns):
                    continue  # Auto-approved by permission pattern
                needs_approval.append((idx, tc))

        if not needs_approval:
            return None

        # Send approval request to parent for each tool call
        # Process one batch at a time (all pending tools in this AI message)
        request_id = f"{self.label}-{uuid.uuid4().hex[:8]}"

        # Serialize tool info (must be pickle-safe)
        tool_requests = []
        for idx, tc in needs_approval:
            # Truncate large args for display (content can be huge)
            display_args = {}
            for k, v in tc["args"].items():
                if isinstance(v, str) and len(v) > 500:
                    display_args[k] = v[:500] + "..."
                else:
                    display_args[k] = v
            tool_requests.append({
                "name": tc["name"],
                "args": display_args,
                "full_args": tc["args"],
                "id": tc.get("id", ""),
            })

        from totoro.pane import SubagentEvent
        try:
            self.event_queue.put_nowait(SubagentEvent(
                label=self.label,
                event_type="hitl_request",
                data={
                    "request_id": request_id,
                    "tool_requests": tool_requests,
                },
            ))
        except Exception:
            # Queue full — auto-approve to avoid deadlock
            return None

        # Block waiting for parent's decision
        try:
            response = self.response_queue.get(timeout=300)
        except queue.Empty:
            # Timeout — reject all
            return self._reject_all(last_ai_msg, needs_approval,
                                    "HITL timeout: tool execution rejected")

        decisions = response.get("decisions", [])

        # Handle auto-approve-all signal
        if any(d.get("type") == "approve_all" for d in decisions):
            self._auto_approve = True
            return None

        # Process decisions
        revised_tool_calls = []
        artificial_messages = []
        decision_idx = 0

        for idx, tc in enumerate(last_ai_msg.tool_calls):
            matched = any(i == idx for i, _ in needs_approval)
            if matched and decision_idx < len(decisions):
                decision = decisions[decision_idx]
                decision_idx += 1
                dtype = decision.get("type", "approve")

                if dtype == "approve":
                    revised_tool_calls.append(tc)
                elif dtype == "edit":
                    edited = decision.get("edited_action", {})
                    revised_tool_calls.append({
                        "name": edited.get("name", tc["name"]),
                        "args": edited.get("args", tc["args"]),
                        "id": tc.get("id"),
                        "type": "tool_call",
                    })
                elif dtype == "reject":
                    msg = decision.get("message", f"User rejected {tc['name']}")
                    artificial_messages.append(ToolMessage(
                        content=msg,
                        name=tc["name"],
                        tool_call_id=tc.get("id", ""),
                        status="error",
                    ))
            else:
                # Not intercepted — keep as-is
                revised_tool_calls.append(tc)

        last_ai_msg.tool_calls = revised_tool_calls
        return {"messages": [last_ai_msg, *artificial_messages]}

    def _reject_all(self, ai_msg, needs_approval, reason):
        """Reject all pending tool calls with an error message."""
        artificial = []
        kept = []
        rejected_indices = {idx for idx, _ in needs_approval}
        for idx, tc in enumerate(ai_msg.tool_calls):
            if idx in rejected_indices:
                artificial.append(ToolMessage(
                    content=reason,
                    name=tc["name"],
                    tool_call_id=tc.get("id", ""),
                    status="error",
                ))
            else:
                kept.append(tc)
        ai_msg.tool_calls = kept
        return {"messages": [ai_msg, *artificial]}
