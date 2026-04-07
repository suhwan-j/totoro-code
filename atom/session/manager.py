"""Session management — create, list, restore sessions."""
import time
from dataclasses import dataclass, field


@dataclass
class SessionInfo:
    """Metadata for a session."""
    session_id: str
    created_at: float
    last_active: float
    turn_count: int = 0
    description: str = ""


class SessionManager:
    """Manages session lifecycle with checkpointer-backed persistence.

    In-memory index that tracks session metadata.
    Actual state is stored by LangGraph's checkpointer (MemorySaver / PostgresSaver).
    """

    def __init__(self, checkpointer=None):
        self._checkpointer = checkpointer
        self._sessions: dict[str, SessionInfo] = {}

    def create_session(self, session_id: str | None = None, description: str = "") -> SessionInfo:
        """Create a new session and return its info."""
        if session_id is None:
            session_id = f"session-{int(time.time())}"
        now = time.time()
        info = SessionInfo(
            session_id=session_id,
            created_at=now,
            last_active=now,
            description=description,
        )
        self._sessions[session_id] = info
        return info

    def get_session(self, session_id: str) -> SessionInfo | None:
        """Get session info by ID."""
        return self._sessions.get(session_id)

    def update_activity(self, session_id: str) -> None:
        """Update last_active timestamp and increment turn count."""
        info = self._sessions.get(session_id)
        if info:
            info.last_active = time.time()
            info.turn_count += 1

    def list_sessions(self) -> list[SessionInfo]:
        """List all sessions, most recent first."""
        return sorted(
            self._sessions.values(),
            key=lambda s: s.last_active,
            reverse=True,
        )

    def get_invoke_config(self, session_id: str) -> dict:
        """Build LangGraph invoke config for a session."""
        return {"configurable": {"thread_id": session_id}}

    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists in the index."""
        return session_id in self._sessions

    def try_restore_from_checkpointer(self, agent, session_id: str) -> bool:
        """Try to restore a session from the checkpointer.

        Returns True if the session state exists in the checkpointer.
        """
        try:
            config = self.get_invoke_config(session_id)
            state = agent.get_state(config)
            if state and state.values:
                # Session exists in checkpointer — register it locally
                if session_id not in self._sessions:
                    self.create_session(session_id, description="(restored)")
                return True
        except Exception:
            pass
        return False

    def get_pending_interrupts(self, agent, session_id: str) -> list | None:
        """Check if a session has pending interrupts (HITL approvals)."""
        try:
            config = self.get_invoke_config(session_id)
            state = agent.get_state(config)
            if state and state.next:
                if hasattr(state, "tasks") and state.tasks:
                    return list(state.tasks)
        except Exception:
            pass
        return None

    def format_session_list(self) -> str:
        """Format session list for display."""
        sessions = self.list_sessions()
        if not sessions:
            return "No sessions found."

        lines = ["\033[1mSessions:\033[0m"]
        for s in sessions:
            age = _format_age(time.time() - s.created_at)
            active = _format_age(time.time() - s.last_active)
            desc = f" — {s.description}" if s.description else ""
            lines.append(
                f"  {s.session_id}  ({s.turn_count} turns, created {age} ago, "
                f"active {active} ago){desc}"
            )
        return "\n".join(lines)


def _format_age(seconds: float) -> str:
    """Format seconds into human-readable age."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"
