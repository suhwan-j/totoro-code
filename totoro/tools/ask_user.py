from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
def ask_user_tool(question: str) -> str:
    """Ask the user a question and wait for their response. Use when you need clarification or approval.

    Args:
        question: Question to show the user
    """
    response = interrupt({"type": "ask_user", "question": question})
    return str(response)
