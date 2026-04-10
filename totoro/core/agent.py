"""Totoro agent factory — uses create_agent() directly for lean middleware control.

Bypasses create_deep_agent() to avoid the automatic SubAgentMiddleware (task tool)
which adds ~2,178 tokens of overhead. Totoro uses its own orchestrate_tool for
sub-agent management, so the framework's task tool is dead weight.
"""
import os
from pathlib import Path
from datetime import datetime

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, TodoListMiddleware
from deepagents.backends import LocalShellBackend
from deepagents.graph import BASE_AGENT_PROMPT
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.summarization import create_summarization_middleware
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.memory import InMemoryStore

from totoro.tools import git_tool, web_search_tool, fetch_url_tool, ask_user_tool
from totoro.config.schema import AgentConfig
from totoro.core.models import create_lightweight_model
from totoro.layers.sanitize import SanitizeMiddleware
from totoro.layers.stall_detector import StallDetectorMiddleware
from totoro.layers.auto_dream import AutoDreamExtractor, AutoDreamMiddleware, CharacterFile

# Re-export SubAgent type for SUBAGENT_CONFIGS (used by orchestrator)
from deepagents.middleware.subagents import SubAgent


CORE_SYSTEM_PROMPT = """You are Totoro, a CLI coding agent orchestrator. You delegate ALL work to sub-agents via orchestrate_tool.

## How to Use orchestrate_tool

orchestrate_tool takes a JSON array of tasks: '[{"type":"<agent>","task":"<detailed description>"}]'

You MUST call orchestrate_tool **multiple times per request**. A single call is almost never enough.

### Available Agents
- **catbus** — Planner. Analyzes the request, explores the codebase, and returns a structured execution plan. Call catbus FIRST for complex or unfamiliar tasks.
- **satsuki** — Senior coder. Implements features, refactors code, runs builds. Use for multi-file changes.
- **mei** — Researcher. Explores codebases, searches the web, reads docs. Read-only — never modifies files.
- **susuwatari** — Micro agent. Does exactly one atomic operation (one file edit, one command). Fast.
- **tatsuo** — Reviewer. Runs tests, checks code quality, verifies correctness. Call AFTER implementation.

### Typical Call Sequence
1. orchestrate_tool with **catbus** → receive a plan
2. Record the plan with write_todos
3. orchestrate_tool with **satsuki/mei/susuwatari** → execute the plan (you can run multiple agents in one call)
4. orchestrate_tool with **tatsuo** → verify the work

**CRITICAL: After catbus returns a plan, you MUST immediately call orchestrate_tool again to execute it. NEVER stop after planning.**

If tatsuo finds critical issues, call satsuki/susuwatari to fix them, then tatsuo again.

## Rules
- NEVER write/edit files directly. Always delegate via orchestrate_tool.
- Task descriptions must be detailed and self-contained — sub-agents have NO context about prior steps.
- Never commit or run destructive git commands without user approval.
- Do NOT output "I'll analyze this" or "please wait" without actually calling orchestrate_tool.
"""


# ─── Totoro character-based subagent declarations ───
#
# 🚌 Catbus   (네코버스) — Router/Planner: 복잡한 작업을 분해, 실행 계획 수립
# 🧒 Satsuki  (사츠키)   — Senior Agent: 복잡한 코드 구현, 빌드, 테스트
# 👧 Mei      (메이)     — Explorer/Researcher: 탐색, 검색, 패턴 발견
# 👨 Tatsuo   (타츠오)   — Knowledge/Reviewer: 코드 리뷰, 문서 관리, 컨텍스트 보존
# 🌱 Susuwatari(스스와타리) — Micro Agent: 단순 파일 수정, atomic 작업
#
SUBAGENT_CONFIGS: list[SubAgent] = [
    {
        "name": "catbus",
        "description": "Planner — 요청을 분석하고 구체적인 실행 계획을 수립. 태스크 분해, 에이전트 배정, 의존성 정리.",
        "system_prompt": (
            "You are Catbus (네코버스), the strategic planner. You analyze requests and create "
            "detailed execution plans that other agents will follow.\n\n"
            "## Your Job\n"
            "1. Analyze the user's request and the working directory context\n"
            "2. Break the work into concrete, independent tasks\n"
            "3. Assign the right agent type to each task\n"
            "4. Output a structured plan as TEXT + JSON block\n\n"
            "## CRITICAL: You are a PLANNER, not an executor\n"
            "- You have NO tools. You CANNOT explore the codebase.\n"
            "- Make your best plan based on the request and working directory info alone.\n"
            "- Do NOT say you will explore or use tools — just output the plan immediately.\n"
            "- NEVER output anything without a JSON plan block.\n\n"
            "## Task Granularity\n"
            "- Keep tasks COARSE — prefer fewer, larger tasks over many small ones.\n"
            "- **Maximum 5 tasks** in a plan. Combine related work into one task.\n"
            "- Each satsuki task can handle multiple files — do NOT split per-file.\n"
            "- Use susuwatari ONLY for truly atomic, independent operations.\n"
            "- BAD: 10 tasks, one per file. GOOD: 2 tasks (frontend + backend).\n\n"
            "## Agent Assignment Guide\n"
            "- 'satsuki': Complex code implementation, multi-file changes, build/test setup (can handle MANY files in one task)\n"
            "- 'mei': Codebase exploration, web research, pattern discovery (read-only)\n"
            "- 'susuwatari': ONLY for truly simple, single atomic operation — one file edit, one command\n\n"
            "## Output Format (MANDATORY)\n"
            "Your response MUST end with a JSON plan block like this:\n"
            "```plan\n"
            "[\n"
            '  {"type": "mei", "task": "Research existing API patterns in src/api/"},\n'
            '  {"type": "satsuki", "task": "Create src/api/users.ts with CRUD endpoints"},\n'
            '  {"type": "susuwatari", "task": "Add users route to src/api/index.ts"}\n'
            "]\n"
            "```\n"
            "This is your ONLY output format. Plan as text + JSON block. Nothing else."
        ),
    },
    {
        "name": "satsuki",
        "description": "Senior Agent — 복잡한 코드 구현, 리팩토링, 빌드/테스트. 책임감 있고 실행력이 강함.",
        "system_prompt": (
            "You are Satsuki (사츠키), the senior coding agent. You handle complex implementations "
            "with responsibility and strong execution.\n"
            "- Use write_file to create new files\n"
            "- Use read_file before editing existing files\n"
            "- Use edit_file for targeted modifications\n"
            "- Use execute to run shell commands (install packages, build, test)\n"
            "- Follow existing code style and conventions\n"
            "- You are thorough and reliable — verify your work when possible"
        ),
    },
    {
        "name": "mei",
        "description": "Explorer/Researcher — 코드베이스 탐색, 웹 검색, 패턴 발견. 호기심 많고 새로운 것을 먼저 발견.",
        "system_prompt": (
            "You are Mei (메이), the curious explorer and researcher. You discover things first.\n"
            "- Use ls, read_file, glob, and grep to explore the codebase\n"
            "- Use web_search_tool and fetch_url_tool to research online\n"
            "- Report findings in a clear, structured format\n"
            "- You are curious and thorough — look in unexpected places\n"
            "- Read-only for codebase exploration. Never modify files unless explicitly asked."
        ),
    },
    {
        "name": "tatsuo",
        "description": "Reviewer/Tester — 코드 리뷰, 테스트 실행, 품질 검증. 작업 완료 후 정상 동작 확인.",
        "system_prompt": (
            "You are Tatsuo (타츠오), the quality reviewer and tester. You verify that work "
            "was done correctly and meets quality standards.\n\n"
            "## Your Job\n"
            "1. Review the code changes for correctness and quality\n"
            "2. Run tests and verify functionality\n"
            "3. Check for bugs, security issues, and edge cases\n"
            "4. Report your findings clearly\n\n"
            "## Review Checklist\n"
            "- Read all modified/created files with read_file\n"
            "- Run the test suite with execute (e.g., npm test, pytest, cargo test)\n"
            "- Run linters/formatters if configured (e.g., eslint, ruff, cargo clippy)\n"
            "- Try to build/compile the project if applicable\n"
            "- Check for common issues: missing imports, typos, incorrect logic\n"
            "- Verify files are consistent with each other (imports match exports, etc.)\n\n"
            "## Output Format (MANDATORY)\n"
            "Your response MUST follow this structure:\n"
            "### Test Results\n"
            "- (pass/fail status of each test command you ran)\n\n"
            "### Issues Found\n"
            "- CRITICAL: (must fix before shipping)\n"
            "- WARNING: (should fix, potential problems)\n"
            "- INFO: (suggestions for improvement)\n\n"
            "### Summary\n"
            "- Overall status: PASS / FAIL\n"
            "- (one-line summary)\n\n"
            "## Rules\n"
            "- NEVER use the 'task' tool. You do NOT have sub-agents. Do all review work yourself.\n"
            "- Be thorough but concise\n"
            "- Use execute to run tests, builds, and linters — do NOT just read code\n"
            "- If no test suite exists, verify by running the program or checking syntax\n"
            "- You CAN run commands (execute) for testing, but do NOT modify source files"
        ),
    },
    {
        "name": "susuwatari",
        "description": "Micro Agent — 단순 파일 수정, API 호출 등 atomic한 단일 작업. 명확한 지시 필요.",
        "system_prompt": (
            "You are Susuwatari (스스와타리), a micro agent for small, atomic tasks. "
            "You are fast and focused — do exactly one thing and finish.\n"
            "- Execute the given task immediately and directly\n"
            "- Use write_file or edit_file for single file operations\n"
            "- Use execute for single shell commands\n"
            "- Do NOT explore, plan, or verify — just do the one task and stop\n"
            "- If the instruction is unclear, you fail. Be precise."
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
    """Create the Totoro agent using create_agent() directly.

    Uses create_agent() instead of create_deep_agent() to control the
    middleware stack precisely — notably excluding SubAgentMiddleware
    (task tool) which adds ~2,178 tokens of overhead per turn.
    Totoro manages sub-agents via its own orchestrate_tool.

    Returns:
        tuple: (agent, checkpointer, store, auto_dream_extractor)
    """
    global _api_timeout
    _api_timeout = config.loop.api_timeout_seconds

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

    # Build backend
    backend = LocalShellBackend(
        root_dir=config.project_root,
        virtual_mode=False,
        inherit_env=True,
    )

    # Build complete middleware stack
    all_middleware = _build_full_middleware_stack(config, model, backend, store, hitl_config)

    # Discover skill paths
    from totoro.skills import SkillManager
    skill_mgr = SkillManager(config.project_root)
    skill_paths = skill_mgr.get_skill_paths() or None

    # Extract auto_dream extractor for CLI access
    auto_dream = None
    for mw in all_middleware:
        if isinstance(mw, AutoDreamMiddleware):
            auto_dream = mw._extractor
            break

    agent = create_agent(
        model=model,
        tools=custom_tools,
        system_prompt=system_prompt,
        middleware=all_middleware,
        checkpointer=checkpointer,
        store=store,
        name="totoro",
    ).with_config({
        "recursion_limit": 9_999,
    })

    return agent, checkpointer, store, auto_dream


def _build_full_middleware_stack(config, model, backend, store, hitl_config):
    """Build the complete middleware stack, replacing create_deep_agent()'s auto-stack.

    Middleware ordering (matches create_deep_agent minus SubAgentMiddleware):

    Framework base stack:
      1. TodoListMiddleware         — write_todos tool
      2. SkillsMiddleware           — skill discovery (if skills provided)
      3. FilesystemMiddleware       — ls, read/write/edit_file, glob, grep, execute
      4. [SubAgentMiddleware]       — EXCLUDED: task tool (~2,178 tokens saved)
      5. SummarizationMiddleware    — conversation summarization
      6. PatchToolCallsMiddleware   — fix dangling tool calls

    Totoro custom stack:
      7. SanitizeMiddleware         — strip surrogate chars
      8. ContextCompactionMiddleware — LLM-based context compaction
      9. StallDetectorMiddleware    — detect agent stalls
     10. AutoDreamMiddleware        — memory extraction

    Tail stack:
     11. AnthropicPromptCachingMiddleware — prefix caching
     12. HumanInTheLoopMiddleware   — HITL interrupts (if configured)
    """
    middleware_list = []

    # ── Framework base stack ──

    # 1. TodoList — write_todos tool for task management
    middleware_list.append(TodoListMiddleware())

    # 2. Skills — skill discovery (if configured)
    from totoro.skills import SkillManager
    skill_mgr = SkillManager(config.project_root)
    skill_paths = skill_mgr.get_skill_paths()
    if skill_paths:
        middleware_list.append(SkillsMiddleware(backend=backend, sources=skill_paths))

    # 3. Filesystem — file I/O + shell execution tools
    middleware_list.append(FilesystemMiddleware(backend=backend))

    # 4. SubAgentMiddleware — INTENTIONALLY EXCLUDED
    #    Saves ~2,178 tokens/turn. Totoro uses orchestrate_tool instead.

    # 5. Summarization — conversation compaction
    middleware_list.append(create_summarization_middleware(model, backend))

    # 6. PatchToolCalls — fix dangling tool calls in history
    middleware_list.append(PatchToolCallsMiddleware())

    # ── Totoro custom stack ──

    # 7. Sanitize — strip surrogate chars before API serialization
    middleware_list.append(SanitizeMiddleware())

    # 8. Context Compaction — LLM-based auto-compact
    from totoro.layers.context_compaction import ContextCompactionMiddleware
    from totoro.layers._token_utils import get_model_context_window
    compact_model = create_lightweight_model(config.fallback_model)
    context_window = config.context.model_context_window or get_model_context_window(config.model)
    middleware_list.append(ContextCompactionMiddleware(
        auto_threshold=config.context.auto_compact_threshold,
        reactive_threshold=config.context.reactive_compact_threshold,
        emergency_threshold=config.context.emergency_compact_threshold,
        model_context_window=context_window,
        model=compact_model,
    ))

    # 9. Stall Detection
    if config.loop.stall_detection:
        middleware_list.append(StallDetectorMiddleware(max_empty_turns=3))

    # 10. Auto-Dream Memory
    if config.memory.auto_extract:
        lightweight_model = create_lightweight_model(config.fallback_model)
        character_file = CharacterFile()
        auto_dream = AutoDreamExtractor(
            model=lightweight_model,
            config=config,
            store=character_file,
        )
        middleware_list.append(AutoDreamMiddleware(auto_dream))

    # ── Tail stack ──

    # 11. Anthropic Prompt Caching — cache system prompt + tools prefix
    #     "ignore" silently skips for non-Anthropic models
    try:
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
        middleware_list.append(AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"))
    except ImportError:
        pass

    # 12. HITL — interrupt on destructive tools
    if hitl_config:
        middleware_list.append(HumanInTheLoopMiddleware(interrupt_on=hitl_config))

    return middleware_list


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
        project_root=str(Path(config.project_root).resolve()),
    )


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
            raise RuntimeError(f"Provider '{provider}' is not configured. Run `totoro --setup` to configure.")
        _resolved_provider = provider
        return model

    # Auto-detect: try each provider in priority order
    for prov_name, factory in providers.items():
        model = factory(model_name)
        if model is not None:
            _resolved_provider = prov_name
            return model

    raise RuntimeError(
        "No API key found. Run `totoro --setup` to configure your provider."
    )


# Resolved provider from last _resolve_model call (used by orchestrator to skip re-detection)
_resolved_provider: str = "auto"
_api_timeout: int = 60  # Set from config in create_totoro_agent


def _make_openrouter(model_name: str):
    """Main model uses ChatOpenAI + OpenRouter base URL for reliable streaming.

    ChatOpenRouter is used only for lightweight (non-streaming) calls in models.py.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name,
        openai_api_key=key,
        openai_api_base=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        request_timeout=_api_timeout,
    )


def _make_anthropic(model_name: str):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model_name=model_name, api_key=key, timeout=_api_timeout)


def _make_openai(model_name: str):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model_name, openai_api_key=key, request_timeout=_api_timeout)


def _make_vllm(model_name: str):
    base_url = os.environ.get("VLLM_BASE_URL")
    if not base_url:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name,
        openai_api_key=os.environ.get("VLLM_API_KEY", "EMPTY"),
        openai_api_base=base_url,
        request_timeout=_api_timeout,
    )


def _build_system_prompt(config: AgentConfig) -> str:
    """Assemble the system prompt.

    Order: static content first (cacheable prefix), dynamic content last.
    This maximizes prefix KV cache hits on vLLM (--enable-prefix-caching)
    and Anthropic (cache_control ephemeral).

    Note: BASE_AGENT_PROMPT from DeepAgents is appended for core agent behavior
    guidelines (conciseness, task execution patterns, progress updates).
    """
    # ── Static prefix (cacheable) ──
    sections = [CORE_SYSTEM_PROMPT]

    # ── Project context from TOTORO.md ──
    totoro_md = _load_totoro_md(config.project_root)
    if totoro_md:
        sections.append(totoro_md)

    # ── User memory from character.md ──
    character_md = _load_character_md()
    if character_md:
        sections.append(character_md)

    # ── DeepAgents base prompt (core behavior guidelines) ──
    sections.append(BASE_AGENT_PROMPT)

    # ── Dynamic suffix (changes per session/model switch) ──
    sections.append(f"""
# Environment
- Working directory: {Path(config.project_root).resolve()}
- Current date: {datetime.now().strftime('%Y-%m-%d')}
- Model: {config.model}
- Provider: {config.provider}
""")

    return "\n\n".join(sections)


def _load_totoro_md(project_root: str) -> str | None:
    """Load project context from TOTORO.md in the project root.

    Generated by /init command. Contains project architecture, tech stack,
    patterns, and conventions for AI agent context.
    """
    path = Path(project_root).resolve() / "TOTORO.md"
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return f"# Project Context (TOTORO.md)\n{content}"
        except Exception:
            pass
    return None


def _load_character_md() -> str | None:
    """Load user memory from ~/.totoro/character.md if it exists."""
    path = Path.home() / ".totoro" / "character.md"
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return f"# User Memory (character.md)\n{content}"
        except Exception:
            pass
    return None
