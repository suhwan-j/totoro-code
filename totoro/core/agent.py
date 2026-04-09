"""Totoro agent factory — wraps create_deep_agent()"""
import os
from pathlib import Path
from datetime import datetime

from deepagents import create_deep_agent, SubAgent
from deepagents.backends import LocalShellBackend
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.memory import InMemoryStore

from totoro.tools import git_tool, web_search_tool, fetch_url_tool, ask_user_tool
from totoro.config.schema import AgentConfig
from totoro.core.models import create_lightweight_model
from totoro.layers.sanitize import SanitizeMiddleware
from totoro.layers.stall_detector import StallDetectorMiddleware
from totoro.layers.auto_dream import AutoDreamExtractor, AutoDreamMiddleware


CORE_SYSTEM_PROMPT = """You are Totoro, an advanced CLI coding agent. You help users with software development tasks
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

### Step 2: Execute via PARALLEL Sub-agents (MANDATORY)
You MUST use orchestrate_tool to delegate ALL file creation and modification work.
NEVER call write_file, edit_file, or execute directly — always delegate to sub-agents.

  orchestrate_tool('[
    {"type": "coder", "task": "Create index.html with React setup"},
    {"type": "coder", "task": "Create src/App.tsx with component"},
    {"type": "coder", "task": "Create api/handler.ts serverless function"}
  ]')

Group as many independent tasks as possible into ONE orchestrate_tool call.
After orchestrate completes, update todos with write_todos to mark completed items.

### Step 3: Verify
Use execute to test the result (open files, run servers, check output).

CRITICAL RULES:
- Your FIRST action MUST be write_todos — no exceptions.
- ALWAYS use orchestrate_tool for file operations. NEVER write files yourself directly.
- You are the ORCHESTRATOR. You plan and delegate. Sub-agents do the actual work.
- Group independent work items into a SINGLE orchestrate_tool call for maximum parallelism.
- Each sub-agent task description must be detailed and self-contained.

## Tools
- write_todos: Create a todo list — MUST be your FIRST tool call
- orchestrate_tool: Run sub-agents in PARALLEL — MUST be your SECOND tool call
- read_file / glob / grep / ls: Read-only exploration (use before delegating)
- execute: Run shell commands (ONLY for verification in Step 3)
- git_tool: Git operations
- web_search_tool / fetch_url_tool: Web research

## Sub-agents (for orchestrate_tool)
Each task spawns an independent Totoro agent with full capabilities (file I/O, shell, web search, skills).
No need to specify a type — just describe the task clearly and self-contained.

## IMPORTANT: You do NOT write files yourself.
You call write_todos, then orchestrate_tool. That's your job.
Do NOT call write_file or edit_file directly. Delegate to Totoro sub-agents.

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
        "tools": [],  # populated at runtime by _build_orchestrator_subagents
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


def _create_checkpointer():
    """Create a SqliteSaver checkpointer at ~/.totoro/checkpoints.db.

    Falls back to MemorySaver if SQLite setup fails.
    """
    try:
        import sqlite3
        db_dir = Path.home() / ".totoro"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "checkpoints.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver
    except Exception as e:
        import sys
        from totoro.colors import DIM, RESET
        print(f"{DIM}  [warn] SQLite checkpointer failed ({e}), using in-memory{RESET}", file=sys.stderr)
        return MemorySaver()


def create_totoro_agent(config: AgentConfig):
    """Create the Totoro agent wrapping create_deep_agent().

    Returns:
        tuple: (agent, checkpointer, store, auto_dream_extractor)
    """
    checkpointer = _create_checkpointer()
    store = InMemoryStore()

    system_prompt = _build_system_prompt(config)
    model = _resolve_model(config.model, config.provider)

    # Build parallel subagent instances for orchestrator
    _build_orchestrator_subagents(model, config)

    # Custom tools + orchestrate
    from totoro.orchestrator import orchestrate_tool
    custom_tools = [git_tool, fetch_url_tool, ask_user_tool, orchestrate_tool]
    if os.environ.get("TAVILY_API_KEY"):
        custom_tools.append(web_search_tool)

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

    # Discover skill paths
    from totoro.skills import SkillManager
    skill_mgr = SkillManager(config.project_root)
    skill_paths = skill_mgr.get_skill_paths() or None

    agent = create_deep_agent(
        name="totoro",
        model=model,
        tools=custom_tools,
        system_prompt=system_prompt,
        skills=skill_paths,
        # subagents are managed by orchestrate_tool (parallel), not framework's task tool (sequential)
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
    """Register serializable subagent configs for multiprocessing orchestrator.

    Instead of pre-building graphs (not pickle-safe), we pass serializable
    configs to the orchestrator. Each child process rebuilds its own graph.
    """
    from totoro.orchestrator import register_subagent_configs

    # Extract serializable config: name + system_prompt only
    serializable_configs = []
    for cfg in SUBAGENT_CONFIGS:
        serializable_configs.append({
            "name": cfg["name"],
            "description": cfg.get("description", ""),
            "system_prompt": cfg["system_prompt"],
        })

    # Pass the resolved provider so child processes skip auto-detection
    register_subagent_configs(
        configs=serializable_configs,
        model_name=config.model,
        provider=_resolved_provider if _resolved_provider != "auto" else config.provider,
        project_root=config.project_root,
    )


def _build_custom_middleware(config: AgentConfig, store):
    """Build custom middleware stack for Totoro.

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


def _resolve_model(model_name: str, provider: str = "auto"):
    """Resolve model — supports OpenRouter, Anthropic, OpenAI, and vLLM.

    Args:
        model_name: Model name/identifier.
        provider: "auto" to detect from env, or explicit provider name.

    Returns:
        LLM model instance. Also sets _resolved_provider as side-effect for orchestrator.
    """
    global _resolved_provider

    providers = {
        "openrouter": _make_openrouter,
        "anthropic": _make_anthropic,
        "openai": _make_openai,
        "vllm": _make_vllm,
    }

    if provider != "auto":
        factory = providers.get(provider)
        if factory is None:
            raise RuntimeError(f"Unknown provider: {provider}")
        model = factory(model_name)
        if model is None:
            raise RuntimeError(f"Provider '{provider}' is not configured. Check your .env file.")
        _resolved_provider = provider
        return model

    # Auto-detect: try each provider in priority order
    for prov_name, factory in providers.items():
        model = factory(model_name)
        if model is not None:
            _resolved_provider = prov_name
            return model

    raise RuntimeError(
        "No API key found. Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, or VLLM_BASE_URL."
    )


# Resolved provider from last _resolve_model call (used by orchestrator to skip re-detection)
_resolved_provider: str = "auto"


def _make_openrouter(model_name: str):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    from langchain_openai import ChatOpenAI
    model_map = {
        "claude-sonnet-4-5-20250929": "anthropic/claude-sonnet-4-5",
        "claude-sonnet-4-5": "anthropic/claude-sonnet-4-5",
        "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4-5",
        "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
        "claude-opus-4-5": "anthropic/claude-opus-4-5",
    }
    return ChatOpenAI(
        model=model_map.get(model_name, model_name),
        openai_api_key=key,
        openai_api_base=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        request_timeout=60,
    )


def _make_anthropic(model_name: str):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model_name=model_name, api_key=key, timeout=60)


def _make_openai(model_name: str):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model_name, openai_api_key=key, request_timeout=60)


def _make_vllm(model_name: str):
    base_url = os.environ.get("VLLM_BASE_URL")
    if not base_url:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name,
        openai_api_key=os.environ.get("VLLM_API_KEY", "EMPTY"),
        openai_api_base=base_url,
        request_timeout=60,
    )


def _build_system_prompt(config: AgentConfig) -> str:
    """Assemble the system prompt.

    Order: static content first (cacheable prefix), dynamic content last.
    This maximizes prefix KV cache hits on vLLM (--enable-prefix-caching)
    and Anthropic (cache_control ephemeral).
    """
    # ── Static prefix (cacheable) ──
    sections = [CORE_SYSTEM_PROMPT]

    agents_md = _load_agents_md(config.project_root)
    if agents_md:
        if len(agents_md) > 16000:
            agents_md = agents_md[:16000] + "\n... (truncated)"
        sections.append(f"# Project Rules (AGENTS.md)\n{agents_md}")

    # ── Dynamic suffix (changes per session/model switch) ──
    sections.append(f"""
# Environment
- Working directory: {Path(config.project_root).resolve()}
- Current date: {datetime.now().strftime('%Y-%m-%d')}
- Model: {config.model}
- Provider: {config.provider}
""")

    return "\n\n".join(sections)


def _load_agents_md(project_root: str) -> str | None:
    """Load AGENTS.md from project root if it exists."""
    path = Path(project_root) / "AGENTS.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None
