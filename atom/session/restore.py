"""Session restore — handles resuming from checkpointed state."""
from atom.colors import AMBER_LT, AMBER, COPPER, RESET


def restore_session(agent, session_id: str, session_manager=None) -> dict | None:
    """Restore a session from checkpoint and report its state.

    Returns:
        invoke_config dict if session was restored, None if not found.
    """
    config = {"configurable": {"thread_id": session_id}}

    try:
        state = agent.get_state(config)
    except Exception as e:
        print(f"{COPPER}Error restoring session '{session_id}': {e}{RESET}")
        return None

    if state is None or not state.values:
        print(f"Session '{session_id}' not found in checkpointer.")
        return None

    # Register in session manager if provided
    if session_manager:
        if not session_manager.session_exists(session_id):
            session_manager.create_session(session_id, description="(restored)")

    # Report state
    messages = state.values.get("messages", [])
    turn_count = sum(1 for m in messages if getattr(m, "type", None) == "human")
    print(f"{AMBER_LT}Session '{session_id}' restored.{RESET}")
    print(f"  Messages: {len(messages)}, User turns: {turn_count}")

    # Check for pending interrupts
    if state.next:
        print(f"  {AMBER}Pending interrupt at: {state.next}{RESET}")
        if hasattr(state, "tasks") and state.tasks:
            for task in state.tasks:
                interrupt_val = None
                if hasattr(task, "interrupts") and task.interrupts:
                    interrupt_val = task.interrupts[0].value if hasattr(task.interrupts[0], "value") else task.interrupts[0]
                if interrupt_val:
                    print(f"    Waiting for approval: {interrupt_val}")
        print("  The agent will resume from the interrupted point.")

    return config
