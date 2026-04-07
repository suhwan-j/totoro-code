import subprocess
import shlex
from langchain_core.tools import tool
from langgraph.types import interrupt

GIT_READ_ONLY = frozenset({"status", "diff", "log", "blame", "show", "branch --list", "remote -v", "tag --list", "stash list", "rev-parse"})
GIT_DESTRUCTIVE = frozenset({"add", "commit", "checkout", "switch", "merge", "stash", "stash pop", "stash drop", "branch -d", "branch -m", "tag", "restore", "reset --soft", "reset --mixed"})
GIT_DANGEROUS = frozenset({"push", "push --force", "push --force-with-lease", "reset --hard", "clean -f", "clean -fd", "branch -D", "rebase", "rebase -i"})
GIT_FORBIDDEN = frozenset({"config"})
SENSITIVE_PATTERNS = {".env", "credentials", "secret", ".pem", ".key", "token"}


@tool
def git_tool(command: str) -> str:
    """Execute a git command with built-in safety rules.
    The command should be everything after 'git', e.g. 'status', 'diff --staged', 'commit -m "msg"'.

    Args:
        command: Git command to execute (e.g., "status", "diff --staged", "log --oneline -5")
    """
    command = command.strip()
    parts = command.split()
    if not parts:
        return "Error: empty git command"

    subcmd = parts[0]

    if subcmd in GIT_FORBIDDEN:
        return f"Blocked: 'git {subcmd}' is not allowed."

    if "--no-verify" in command:
        return "Blocked: --no-verify is not allowed without explicit user approval."

    # Force push to main/master blocked
    if subcmd == "push" and ("--force" in command or "--force-with-lease" in command):
        target = _extract_push_target(command)
        if target in ("main", "master"):
            return f"Blocked: force push to '{target}' is not allowed."

    danger_level = _classify_git_command(command)

    if danger_level == "dangerous":
        approval = interrupt({
            "type": "permission_request",
            "tool": "git_tool",
            "input": f"git {command}",
            "message": f"Dangerous git command: 'git {command}'. Allow?",
        })
        if not approval:
            return f"User denied: git {command}"

    if subcmd == "add":
        rest = command[len("add"):].strip()
        if rest:
            sensitive = _detect_sensitive_files(rest)
            if sensitive:
                approval = interrupt({
                    "type": "permission_request",
                    "tool": "git_tool",
                    "input": f"git add {rest}",
                    "message": f"Staging potentially sensitive files: {sensitive}. Allow?",
                })
                if not approval:
                    return f"User denied staging sensitive files: {sensitive}"

    try:
        result = subprocess.run(
            f"git {command}",
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        output = result.stdout + result.stderr
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Git command timed out after 60s"
    except Exception as e:
        return f"Git error: {e}"


def _classify_git_command(command: str) -> str:
    for pattern in GIT_DANGEROUS:
        if command.startswith(pattern):
            return "dangerous"
    subcmd = command.split()[0] if command.split() else ""
    for pattern in GIT_DESTRUCTIVE:
        if command.startswith(pattern) or subcmd == pattern:
            return "destructive"
    return "read_only"


def _detect_sensitive_files(file_args: str) -> list[str]:
    if file_args.strip() in ("-A", "--all", "."):
        return [f"'{file_args.strip()}' stages all files - review before committing"]
    try:
        files = shlex.split(file_args)
    except ValueError:
        files = file_args.split()
    return [f for f in files if any(p in f.lower() for p in SENSITIVE_PATTERNS)]


def _extract_push_target(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    non_flag = [p for p in parts[1:] if not p.startswith("-")]
    if len(non_flag) >= 2:
        return non_flag[1]
    return ""
