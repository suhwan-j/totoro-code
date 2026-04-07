"""Atom agent factory — wraps create_deep_agent()"""
import os
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from deepagents import create_deep_agent, SubAgent
from deepagents.backends import LocalShellBackend
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from atom.tools import git_tool, web_search_tool, fetch_url_tool, ask_user_tool
from atom.config.schema import AgentConfig
from atom.core.models import create_lightweight_model
from atom.layers.sanitize import SanitizeMiddleware
from atom.layers.stall_detector import StallDetectorMiddleware
from atom.layers.auto_dream import AutoDreamExtractor, AutoDreamMiddleware


CORE_SYSTEM_PROMPT = """You are Atom, an advanced CLI coding agent. You help users with software development tasks
by reading, writing, and editing code, running commands, searching the web,
and managing git repositories.

## Mandatory Workflow
For EVERY task (except trivial one-liners), you MUST follow these steps in order:

### Step 1: Plan (REQUIRED)
Your FIRST tool call MUST be write_todos. Create a clear, ordered task list.
Example:
  write_todos([
    {"content": "Create project directory structure", "status": "pending"},
    {"content": "Write index.html with Three.js setup", "status": "pending"},
    {"content": "Write script.js with 3D scene", "status": "pending"},
    {"content": "Test and verify", "status": "pending"}
  ])

### Step 2: Execute via PARALLEL Sub-agents (REQUIRED for multi-file tasks)
Use orchestrate_tool to run multiple sub-agents IN PARALLEL:
  orchestrate_tool('[
    {"type": "coder", "task": "Create index.html with React setup"},
    {"type": "coder", "task": "Create src/App.tsx with component"},
    {"type": "coder", "task": "Create api/handler.ts serverless function"}
  ]')
After orchestrate completes, update todos with write_todos to mark completed items.

### Step 3: Verify
Use execute to test the result (open files, run servers, check output).

CRITICAL RULES:
- Your FIRST action MUST be write_todos — no exceptions.
- Use orchestrate_tool (NOT task) to run sub-agents in parallel.
- You are the ORCHESTRATOR. Sub-agents do the actual work.
- Group independent work items into a single orchestrate_tool call for parallelism.

## Tools
- read_file: Read file content (always read before editing)
- write_file: Create new files
- edit_file: Modify existing files (targeted string replacement)
- execute: Run shell commands (build, test, install packages, verify work)
- git_tool: Git operations with built-in safety rules
- web_search_tool: Search the web for documentation and solutions
- fetch_url_tool: Fetch content from URLs
- write_todos: Create and manage a todo list for planning — call this FIRST for complex tasks
- orchestrate_tool: Run multiple sub-agents in PARALLEL — preferred over sequential task calls
- task: Delegate a single sub-task to one sub-agent (use orchestrate_tool for multiple)
- glob: Find files by pattern
- grep: Search file contents
- ls: List directory contents

## Sub-agents (via task tool)
When delegating, provide a detailed description of what the sub-agent should do:
- "coder": Write and modify code, create project files, run tests
- "planner": Analyze complex requests and create structured plans
- "explorer": Read-only codebase exploration and analysis
- "researcher": Web research and documentation lookup
- "reviewer": Code review and quality analysis (read-only)

## Rules
- Never commit without explicit user request
- Never run destructive git commands without user approval
- When creating projects, create all files and verify they work with execute
- Be concise and direct in responses
- Act immediately on clear instructions without unnecessary questions
"""


# ─── Subagent type declarations ───
SUBAGENT_CONFIGS: list[SubAgent] = [
    {
        "name": "explorer",
        "description": "Codebase exploration and structure analysis. Use for finding files, understanding architecture, searching patterns. Read-only — never modifies files.",
        "system_prompt": (
            "You are a codebase explorer. Use ls, read_file, glob, and grep to explore the codebase. "
            "Report findings in a clear, structured format. Never modify files."
        ),
    },
    {
        "name": "coder",
        "description": "Code writing and modification. Use for implementing features, fixing bugs, creating project files, and refactoring code.",
        "system_prompt": (
            "You are a code implementer. Write and edit code based on the given instruction.\n"
            "- Use write_file to create new files\n"
            "- Use read_file before editing existing files\n"
            "- Use edit_file for targeted modifications\n"
            "- Use execute to run shell commands (install packages, build, test)\n"
            "- Follow existing code style and conventions\n"
            "- Verify your work by running the code with execute when possible"
        ),
    },
    {
        "name": "researcher",
        "description": "Web research and information gathering. Use for looking up documentation, finding solutions, researching APIs and libraries.",
        "system_prompt": (
            "You are a researcher. Use web_search_tool and fetch_url_tool to gather information. "
            "Summarize findings clearly with relevant URLs and code examples."
        ),
        "tools": [web_search_tool, fetch_url_tool],
    },
    {
        "name": "reviewer",
        "description": "Code review — read-only analysis. Use for finding bugs, suggesting improvements, checking code quality.",
        "system_prompt": (
            "You are a code reviewer. Read code using read_file, find bugs, suggest improvements. "
            "Report findings as: issues, suggestions, and summary. Never modify files."
        ),
    },
    {
        "name": "planner",
        "description": "Plan formulation and task breakdown. Use for analyzing complex requests and creating structured, actionable plans.",
        "system_prompt": (
            "You are a task planner. Analyze the request and create a structured plan.\n"
            "- Use write_todos to create an actionable todo list\n"
            "- Break complex tasks into clear, ordered steps\n"
            "- Consider dependencies between steps\n"
            "- Include specific file names and technologies in each step\n"
            "- Suggest an execution order"
        ),
    },
]


def create_atom_agent(config: AgentConfig):
    """Create the Atom agent wrapping create_deep_agent().

    Returns:
        tuple: (agent, checkpointer, store, auto_dream_extractor)
    """
    checkpointer = MemorySaver()
    store = InMemoryStore()

    system_prompt = _build_system_prompt(config)
    model = _resolve_model(config.model)

    # Build parallel subagent instances for orchestrator
    _build_orchestrator_subagents(model, config)

    # Custom tools + orchestrate
    from atom.orchestrator import orchestrate_tool
    custom_tools = [git_tool, web_search_tool, fetch_url_tool, ask_user_tool, orchestrate_tool]

    # HITL config
    if config.permissions.mode == "auto_approve":
        hitl_config = None
    else:
        hitl_config = {
            "execute": True,
            "write_file": True,
            "edit_file": True,
        }

    # Build custom middleware stack
    custom_middleware, auto_dream = _build_custom_middleware(config, store)

    agent = create_deep_agent(
        name="atom",
        model=model,
        tools=custom_tools,
        system_prompt=system_prompt,
        subagents=SUBAGENT_CONFIGS,
        backend=LocalShellBackend(
            root_dir=config.project_root,
            virtual_mode=False,
            inherit_env=True,
        ),
        interrupt_on=hitl_config,
        checkpointer=checkpointer,
        store=store,
        middleware=custom_middleware,
    )

    return agent, checkpointer, store, auto_dream


def _build_orchestrator_subagents(model, config: AgentConfig):
    """Pre-build subagent graphs and register them with the orchestrator."""
    from atom.orchestrator import register_subagents

    subagent_instances = {}
    for cfg in SUBAGENT_CONFIGS:
        name = cfg["name"]
        extra_tools = cfg.get("tools", [])
        subagent = create_deep_agent(
            model=model,
            system_prompt=cfg["system_prompt"],
            tools=extra_tools,
            backend=LocalShellBackend(
                root_dir=config.project_root,
                virtual_mode=False,
                inherit_env=True,
            ),
            checkpointer=MemorySaver(),
            middleware=[SanitizeMiddleware()],
        )
        subagent_instances[name] = subagent

    register_subagents(subagent_instances)


def _build_custom_middleware(config: AgentConfig, store):
    """Build custom middleware stack for Atom.

    Returns:
        tuple: (middleware_list, auto_dream_extractor)
    """
    middleware_list = []

    # 0. Sanitize — MUST be first: strips surrogate chars before API serialization
    middleware_list.append(SanitizeMiddleware())

    # 1. Stall Detection — after_model hook
    if config.loop.stall_detection:
        middleware_list.append(StallDetectorMiddleware(
            max_empty_turns=3,
        ))

    # 2. Auto-Dream Memory — after_model hook
    auto_dream = None
    if config.memory.auto_extract:
        lightweight_model = create_lightweight_model(config.fallback_model)
        auto_dream = AutoDreamExtractor(
            model=lightweight_model,
            store=store,
            config=config,
        )
        middleware_list.append(AutoDreamMiddleware(auto_dream))

    return middleware_list, auto_dream


def _resolve_model(model_name: str):
    """Resolve model — supports OpenRouter via OPENROUTER_API_KEY."""
    load_dotenv()

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    openrouter_base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    if openrouter_key:
        from langchain_openai import ChatOpenAI
        model_map = {
            "claude-sonnet-4-5-20250929": "anthropic/claude-sonnet-4-5",
            "claude-sonnet-4-5": "anthropic/claude-sonnet-4-5",
            "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4-5",
            "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
            "claude-opus-4-5": "anthropic/claude-opus-4-5",
        }
        resolved_name = model_map.get(model_name, model_name)
        return ChatOpenAI(
            model=resolved_name,
            openai_api_key=openrouter_key,
            openai_api_base=openrouter_base,
        )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model_name=model_name, api_key=anthropic_key)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, openai_api_key=openai_key)

    raise RuntimeError("No API key found. Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY.")


def _build_system_prompt(config: AgentConfig) -> str:
    """Assemble the system prompt."""
    sections = [CORE_SYSTEM_PROMPT]

    sections.append(f"""
# Environment
- Working directory: {Path(config.project_root).resolve()}
- Current date: {datetime.now().strftime('%Y-%m-%d')}
""")

    agents_md = _load_agents_md(config.project_root)
    if agents_md:
        if len(agents_md) > 16000:
            agents_md = agents_md[:16000] + "\n... (truncated)"
        sections.append(f"# Project Rules (AGENTS.md)\n{agents_md}")

    return "\n\n".join(sections)


def _load_agents_md(project_root: str) -> str | None:
    """Load AGENTS.md from project root if it exists."""
    path = Path(project_root) / "AGENTS.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None
