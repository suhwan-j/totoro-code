import subprocess
from langchain_core.tools import tool


@tool
def bash_tool(command: str, timeout: int = 120) -> str:
    """Execute a shell command and return its output.

    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default 120)
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        if len(output) > 50000:
            output = (
                output[:50000]
                + "\n... (truncated,"
                f" {len(result.stdout) + len(result.stderr)}"
                " total chars)"
            )
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"
