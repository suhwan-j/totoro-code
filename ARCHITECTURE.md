# ATOM-CODE Architecture

> **구현 스택**: Python 3.11+ / DeepAgents / LangGraph / LangChain  
> **이 문서**: 기술적 구현 청사진. 무엇을 만들지는 AGENTS.md, 어떻게 만들지는 이 문서.  
> **핵심 원칙**: DeepAgents 프레임워크가 제공하는 것은 그대로 사용. ATOM-CODE는 프레임워크 위에 커스텀 레이어만 추가.

---

## 1. 시스템 개요

ATOM-CODE는 DeepAgents 프레임워크의 `create_deep_agent()`를 중심으로 구축된 CLI 코딩 에이전트다.
프레임워크가 StateGraph, 미들웨어, 내장 도구, 서브에이전트 관리, HITL, 스킬을 모두 처리하므로
ATOM-CODE는 **커스텀 도구 + 커스텀 레이어**만 구현한다.

```
┌──────────────────────────────────────────────────────────────────────┐
│                           ATOM-CODE CLI                                │
│                                                                     │
│  ┌────────────┐  ┌────────────────┐  ┌─────────────────────────┐   │
│  │ Textual UI │  │ Non-Interactive │  │ LangGraph API Server    │   │
│  │ (TUI)      │  │ Mode           │  │ (Programmatic Access)   │   │
│  └─────┬──────┘  └──────┬─────────┘  └──────────┬──────────────┘   │
│        │                │                        │                  │
│        └────────────────┼────────────────────────┘                  │
│                         │                                           │
│                         ▼                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                                                             │   │
│  │              create_deep_agent("sds-ax", ...)               │   │
│  │              ═══════════════════════════════                 │   │
│  │                                                             │   │
│  │  ┌───────────────────────────────────────────────────┐     │   │
│  │  │  DeepAgents 프레임워크 자동 제공                    │     │   │
│  │  │                                                   │     │   │
│  │  │  미들웨어: TodoList, Filesystem, SubAgent,         │     │   │
│  │  │           HumanInTheLoop, Skills, Memory          │     │   │
│  │  │                                                   │     │   │
│  │  │  내장 도구: write_todos, ls, read_file, write_file,│     │   │
│  │  │           edit_file, glob, grep, task             │     │   │
│  │  │                                                   │     │   │
│  │  │  HITL: interrupt_on → Command(resume=...)         │     │   │
│  │  │  서브에이전트: 선언적 config → task 도구           │     │   │
│  │  └───────────────────────────────────────────────────┘     │   │
│  │                                                             │   │
│  │  ┌───────────────────────────────────────────────────┐     │   │
│  │  │  ATOM-CODE 커스텀 레이어                              │     │   │
│  │  │                                                   │     │   │
│  │  │  커스텀 도구: git, bash, web_search, fetch_url,    │     │   │
│  │  │              ask_user                              │     │   │
│  │  │                                                   │     │   │
│  │  │  커스텀 기능: Stall Detection, Context Compaction, │     │   │
│  │  │              Auto-Dream Memory, MCP Trust,        │     │   │
│  │  │              Session Restore, Slash Commands       │     │   │
│  │  └───────────────────────────────────────────────────┘     │   │
│  │                                                             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                         │                                           │
│          ┌──────────────┼──────────────┐                           │
│          ▼              ▼              ▼                            │
│  ┌──────────────┐ ┌──────────┐ ┌──────────────┐                   │
│  │ Composite    │ │ LLM      │ │ LangSmith    │                   │
│  │ Backend      │ │ Provider │ │ Tracing      │                   │
│  │ (Storage)    │ │ (API)    │ │ (Telemetry)  │                   │
│  └──────────────┘ └──────────┘ └──────────────┘                   │
└──────────────────────────────────────────────────────────────────────┘
```

### 프레임워크 vs 커스텀 경계

| 영역 | DeepAgents 자동 제공 | ATOM-CODE 커스텀 구현                           |
|------|---------------------|--------------------------------------------|
| 그래프 구조 | StateGraph, 노드, 엣지, 라우팅 | -                                          |
| 미들웨어 | TodoList, Filesystem, SubAgent, HITL, Skills, Memory | Stall Detection, Context Compaction        |
| 내장 도구 | write_todos, ls, read_file, write_file, edit_file, glob, grep, task | git, bash, web_search, fetch_url, ask_user |
| 서브에이전트 | 선언적 config, task 도구, 생명주기 관리 | -                                          |
| HITL | interrupt_on, Command(resume=...) | interrupt 기반 ask_user                      |
| 백엔드 | CompositeBackend, StateBackend, StoreBackend, FilesystemBackend | Auto-Dream 메모리 추출                          |
| 스킬 | skills=["./skills/"], SKILL.md 포맷 | 커스텀 스킬 파일                                  |
| 체크포인터 | MemorySaver, PostgresSaver | 세션 복원 로직                                   |
| CLI/TUI | - | Textual UI, 슬래시 커맨드                        |
| MCP | - | trust_level 권한 모델                          |

---

## 2. 디렉토리 구조

```
sds-ax/
├── pyproject.toml                 # 프로젝트 메타데이터, 의존성
├── AGENTS.md                      # 에이전트 사양/행동 규칙
├── ARCHITECTURE.md                # 이 문서
├── .env.example                   # 환경변수 템플릿
│
├── sds_ax/                        # 메인 패키지
│   ├── __init__.py
│   ├── __main__.py                # python -m sds_ax 진입점
│   ├── cli.py                     # CLI 파서 (argparse), 모드 라우팅
│   │
│   ├── core/                      # 핵심 — create_deep_agent() 래퍼
│   │   ├── __init__.py
│   │   ├── agent.py               # create_sds_ax_agent() — 단일 진입점
│   │   └── models.py              # LLM 프로바이더 초기화, 폴백 체인
│   │
│   ├── tools/                     # 커스텀 도구 (프레임워크 내장 도구 외)
│   │   ├── __init__.py
│   │   ├── git.py                 # git 도구 (서브커맨드별 안전 규칙)
│   │   ├── bash.py                # bash 도구 (샌드박스: none/restricted/container)
│   │   ├── web_search.py          # web_search (Tavily)
│   │   ├── fetch_url.py           # fetch_url (httpx)
│   │   ├── ask_user.py            # ask_user (interrupt 기반)
│   │   └── mcp/                   # MCP 도구 통합
│   │       ├── __init__.py
│   │       ├── client.py          # MCP 클라이언트
│   │       ├── trust.py           # MCP 서버 신뢰 관리
│   │       └── permissions.py     # MCP 도구 권한 분류 (trust_level 기반)
│   │
│   ├── layers/                    # 커스텀 레이어 (프레임워크 미들웨어 외)
│   │   ├── __init__.py
│   │   ├── stall_detector.py      # Stall Detection (넛지 → 모델 전환 → ask_user → 중단)
│   │   ├── context_compaction.py  # Auto/Reactive/Emergency 컴팩션
│   │   └── auto_dream.py          # Auto-Dream 메모리 추출
│   │
│   ├── session/                   # 세션 관리
│   │   ├── __init__.py
│   │   ├── manager.py             # 세션 생성/복원/목록
│   │   └── restore.py             # 세션 복원 시 서브에이전트 상태 처리
│   │
│   ├── commands/                  # 슬래시 커맨드
│   │   ├── __init__.py
│   │   ├── registry.py            # 커맨드 등록/발견
│   │   ├── help.py
│   │   ├── clear.py
│   │   ├── compact.py
│   │   ├── model.py
│   │   ├── memory.py
│   │   ├── tasks.py
│   │   └── config.py
│   │
│   ├── config/                    # 설정
│   │   ├── __init__.py
│   │   ├── settings.py            # 설정 로더 (파일 + 환경변수 + CLI)
│   │   ├── schema.py              # 설정 스키마 (Pydantic)
│   │   └── env.py                 # 환경변수 관리
│   │
│   ├── ui/                        # TUI 레이어
│   │   ├── __init__.py
│   │   ├── app.py                 # Textual App 메인
│   │   ├── widgets/               # UI 위젯
│   │   │   ├── prompt_input.py    # 입력 프롬프트
│   │   │   ├── message_list.py    # 메시지 렌더링
│   │   │   ├── status_bar.py      # 상태 바 (모델, 세션)
│   │   │   ├── task_panel.py      # 서브에이전트/태스크 패널
│   │   │   └── spinner.py         # 로딩 스피너
│   │   └── theme.py               # 테마 설정
│   │
│   ├── telemetry/                 # 텔레메트리
│   │   ├── __init__.py
│   │   └── tracer.py              # LangSmith 트레이싱 래퍼
│   │
│   └── utils/                     # 유틸리티
│       ├── __init__.py
│       ├── async_helpers.py       # 비동기 유틸리티
│       ├── retry.py               # 지수 백오프 재시도
│       └── formatting.py          # 출력 포맷팅
│
├── skills/                        # 프로젝트 레벨 스킬 (SKILL.md 포맷)
│   ├── deep-interview/SKILL.md
│   ├── remember/SKILL.md
│   └── explore/SKILL.md
│
├── tests/                         # 테스트
│   ├── unit/
│   ├── integration/
│   └── conftest.py
│
└── scripts/                       # 빌드/배포 스크립트
    ├── install.sh
    └── dev.sh
```

**이전 구조 대비 제거된 디렉토리:**
- `core/graph.py`, `core/state.py`, `core/loop.py` — 프레임워크가 StateGraph 자동 구성
- `middleware/` — 프레임워크가 미들웨어 자동 포함 (커스텀 레이어는 `layers/`로 분리)
- `subagents/` — 프레임워크가 선언적 config로 서브에이전트 관리
- `tools/builtin/` — 프레임워크 내장 도구 (read_file, write_file 등)는 자동 제공
- `memory/` — 프레임워크의 MemoryMiddleware + StoreBackend 사용 (Auto-Dream만 커스텀)
- `permissions/` — 프레임워크의 HumanInTheLoopMiddleware가 interrupt_on으로 처리

---

## 3. 핵심 모듈 상세 설계

### 3.1 create_sds_ax_agent() (core/agent.py)

`create_deep_agent()`를 래핑하여 ATOM-CODE 전용 설정을 주입하는 단일 진입점.

```python
# sds_ax/core/agent.py
from deepagents import create_deep_agent
from deepagents.backends import (
    CompositeBackend,
    StateBackend,
    StoreBackend,
    FilesystemBackend,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
# from langgraph.checkpoint.postgres import PostgresSaver  # 프로덕션
# from langgraph.store.postgres import PostgresStore        # 프로덕션

from sds_ax.tools.git import git_tool
from sds_ax.tools.bash import bash_tool
from sds_ax.tools.web_search import web_search_tool
from sds_ax.tools.fetch_url import fetch_url_tool
from sds_ax.tools.ask_user import ask_user_tool
from sds_ax.config.schema import AgentConfig


def create_sds_ax_agent(config: AgentConfig):
    """ATOM-CODE 에이전트 생성 — create_deep_agent() 래퍼

    DeepAgents 프레임워크가 자동 제공하는 것:
    - StateGraph 구성 (agent → tools → agent 루프)
    - 내장 도구: write_todos, ls, read_file, write_file, edit_file, glob, grep, task
    - 미들웨어: TodoList, Filesystem, SubAgent, HITL, Skills, Memory
    - 서브에이전트 관리 (선언적 config → task 도구)
    - HITL (interrupt_on → Command(resume=...))

    ATOM-CODE가 추가하는 것:
    - 커스텀 도구: git, bash, web_search, fetch_url, ask_user
    - 서브에이전트 타입 정의 (explorer, coder, researcher, reviewer, planner)
    - CompositeBackend 구성 (/memories/ → StoreBackend)
    - interrupt_on 설정 (파괴적 도구에 HITL 적용)
    - 스킬 디렉토리 설정
    """

    # ─── 커스텀 도구 조립 ───
    # 프레임워크 내장 도구(write_todos, ls, read_file, write_file, edit_file,
    # glob, grep, task)는 자동 포함되므로 여기에 추가하지 않는다.
    custom_tools = [
        git_tool,
        bash_tool,
        web_search_tool,
        fetch_url_tool,
        ask_user_tool,
    ]

    # ─── MCP 도구 로드 (설정에 MCP 서버가 있으면) ───
    if config.mcp and config.mcp.servers:
        from sds_ax.tools.mcp.client import load_mcp_tools
        mcp_tools = load_mcp_tools(config.mcp)
        custom_tools.extend(mcp_tools)

    # ─── 서브에이전트 타입 선언 ───
    # DeepAgents는 subagents를 선언적 config로 받아
    # task 도구를 자동 생성하고 생명주기를 관리한다.
    subagent_configs = [
        {
            "name": "explorer",
            "description": "Codebase exploration and structure analysis",
            "tools": ["read_file", "glob", "grep", "ls"],
            "system_prompt": (
                "You are a codebase explorer. Read files, search patterns, "
                "and report findings. Never modify files."
            ),
        },
        {
            "name": "coder",
            "description": "Code writing and modification",
            "tools": ["read_file", "write_file", "edit_file", "glob", "grep", "ls", "bash"],
            "system_prompt": (
                "You are a code implementer. Write and edit code based on "
                "the given instruction. Follow existing code style."
            ),
        },
        {
            "name": "researcher",
            "description": "Web research and information gathering",
            "tools": ["read_file", "glob", "grep", "ls", "web_search", "fetch_url"],
            "system_prompt": (
                "You are a researcher. Search the web and codebase to gather "
                "information. Summarize findings clearly."
            ),
        },
        {
            "name": "reviewer",
            "description": "Code review (read-only, no test execution)",
            "tools": ["read_file", "glob", "grep", "ls"],
            "system_prompt": (
                "You are a code reviewer. Read code, find bugs, suggest improvements. "
                "Report findings in a structured format."
            ),
        },
        {
            "name": "planner",
            "description": "Plan formulation and task breakdown",
            "tools": ["read_file", "glob", "grep", "ls", "write_todos"],
            "system_prompt": (
                "You are a task planner. Analyze the request, break it into "
                "sub-tasks, and create a structured todo list."
            ),
        },
    ]

    # ─── 체크포인터 + 스토어 ───
    checkpointer = MemorySaver()        # 개발용 (인메모리)
    store = InMemoryStore()             # 개발용 (인메모리)
    # checkpointer = PostgresSaver(...)  # 프로덕션
    # store = PostgresStore(...)         # 프로덕션

    # ─── 시스템 프롬프트 조립 ───
    system_prompt = _build_system_prompt(config)

    # ─── 에이전트 생성 ───
    agent = create_deep_agent(
        name="sds-ax",
        model=config.model,                  # "claude-sonnet-4-5-20250929"
        tools=custom_tools,
        system_prompt=system_prompt,
        subagents=subagent_configs,
        backend=lambda rt: CompositeBackend(
            StateBackend(rt),                # 기본: 세션 내 임시 상태
            {
                "/memories/": StoreBackend(rt),       # 장기 메모리 (persistent)
                "/project/": StoreBackend(rt),        # 프로젝트별 메모리
                "/workspace/": FilesystemBackend(rt), # 디스크 파일
            },
        ),
        interrupt_on={
            "write_file": True,
            "edit_file": True,
            "bash": True,
            # git is excluded — it has internal safety rules that selectively
            # interrupt only dangerous subcommands (push --force, reset --hard, etc.)
        },
        skills=["./skills/"],                # SKILL.md 포맷 스킬 디렉토리
        checkpointer=checkpointer,
        store=store,
        middleware=[_build_custom_middleware(config, store)],
    )

    return agent


def _build_system_prompt(config: AgentConfig) -> str:
    """시스템 프롬프트 조립"""
    sections = []

    # 1. 핵심 행동 규칙
    sections.append(CORE_SYSTEM_PROMPT)

    # 2. 환경 정보
    from datetime import datetime
    sections.append(f"""
# Environment
- Working directory: {config.project_root}
- Current date: {datetime.now().isoformat()}
""")

    # 3. AGENTS.md (프로젝트 규칙)
    agents_md = _load_agents_md(config.project_root)
    if agents_md:
        sections.append(f"""
# Project Rules (AGENTS.md)
{agents_md}
""")

    return "\n\n".join(sections)


# 핵심 시스템 프롬프트 (하드코딩)
CORE_SYSTEM_PROMPT = """
You are ATOM-CODE, a CLI coding agent. You help users with software development tasks
by reading, writing, and editing code, running commands, searching the web,
and managing git repositories.

Key behaviors:
- Always read a file before editing it
- Use edit_file for targeted changes, write_file only for new files or complete rewrites
- Never commit without explicit user request
- Never run destructive git commands (push --force, reset --hard) without user approval
- Use task to delegate sub-tasks to specialized sub-agents when beneficial
- Use ask_user when you need clarification or approval
""".strip()
```

### 3.2 에이전트 실행 흐름

`create_deep_agent()`가 내부적으로 구성하는 실행 그래프:

```
                    ┌─────────┐
                    │  START  │
                    └────┬────┘
                         │
                         ▼
              ┌──────────────────────┐
         ┌───>│       agent          │  LLM 호출 (시스템 프롬프트 + 메시지)
         │    └──────────┬───────────┘
         │               │
         │               ▼
         │    ┌──────────────────────┐
         │    │   조건부 라우팅       │
         │    └──┬────┬─────────────┘
         │       │    │
         │       │    └──────────────── ▶ END (end_turn / max_turns 도달)
         │       │
         │       ▼
         │  ┌────────────┐
         │  │   tools     │  도구 실행
         │  └─────┬──────┘
         │        │
         │        ▼
         │  ┌────────────────────────────────────────────────┐
         │  │  자동 미들웨어 (프레임워크 제공)                 │
         │  │                                                │
         │  │  TodoListMiddleware    → write_todos 연동      │
         │  │  FilesystemMiddleware  → 파일 도구 전처리/후처리│
         │  │  SubAgentMiddleware    → task 도구 + 생명주기   │
         │  │  HumanInTheLoopMiddleware → interrupt_on 처리  │
         │  │  SkillsMiddleware      → 스킬 발견/실행        │
         │  │  MemoryMiddleware      → 메모리 검색/주입      │
         │  └────────────────────────────────────────────────┘
         │        │
         └────────┘
```

### 3.3 HITL (Human-in-the-Loop) 흐름

`interrupt_on` 설정에 따라 프레임워크가 자동으로 HITL을 처리:

```python
# 에이전트가 write_file을 호출하면:
# 1. HumanInTheLoopMiddleware가 interrupt_on 설정 확인
# 2. interrupt_on["write_file"] == True → 실행 중단, 사용자에게 승인 요청
# 3. 사용자가 승인/거부

# CLI 측 (resume 호출):
from langgraph.types import Command

# 승인
agent.invoke(
    Command(resume={"decisions": [{"type": "approve"}]}),
    config={"configurable": {"thread_id": session_id}}
)

# reject
agent.invoke(
    Command(resume={"decisions": [{"type": "reject"}]}),
    config={"configurable": {"thread_id": session_id}}
)

# 수정 후 승인
agent.invoke(
    Command(resume={"decisions": [{"type": "edit", "edited_action": {"name": "write_file", "args": {"content": "modified..."}}}]}),
    config={"configurable": {"thread_id": session_id}}
)
```

---

## 4. 커스텀 도구 구현

프레임워크 내장 도구(`write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `task`)는
자동 제공되므로, ATOM-CODE는 아래 5개의 커스텀 도구만 구현한다.

### 4.1 git 도구 (tools/git.py)

```python
# sds_ax/tools/git.py
from langchain.tools import tool
from langgraph.types import interrupt
from typing import Optional
import shlex

# Git 서브커맨드별 안전성 분류
GIT_READ_ONLY = frozenset({
    "status", "diff", "log", "blame", "show", "branch --list",
    "remote -v", "tag --list", "stash list", "rev-parse",
})

GIT_DESTRUCTIVE = frozenset({
    "add", "commit", "checkout", "switch", "merge", "stash",
    "stash pop", "stash drop", "branch -d", "branch -m", "tag",
    "restore", "reset --soft", "reset --mixed",
})

GIT_DANGEROUS = frozenset({
    "push", "push --force", "push --force-with-lease",
    "reset --hard", "clean -f", "clean -fd",
    "branch -D", "rebase", "rebase -i",
})

GIT_FORBIDDEN = frozenset({
    "config",  # git config 변경 금지
})

SENSITIVE_PATTERNS = {".env", "credentials", "secret", ".pem", ".key", "token"}


@tool
async def git_tool(
    subcommand: str,
    args: Optional[str] = None,
    no_verify: bool = False,
) -> str:
    """Git 연산을 실행합니다. 안전 규칙이 자동 적용됩니다.

    Args:
        subcommand: Git 서브커맨드 (예: "status", "diff", "commit")
        args: 추가 인수 (예: "-m 'fix bug'", "--staged")
        no_verify: hook 스킵 여부 -- 사용자 명시적 요청 시에만 True
    """
    full_args = f"{subcommand} {args}" if args else subcommand
    parsed_subcmd = subcommand.split()[0]

    # 1. 금지 명령 차단
    if parsed_subcmd in GIT_FORBIDDEN:
        return f"Blocked: 'git {parsed_subcmd}' is not allowed."

    # 2. --no-verify 보호
    if "--no-verify" in (args or "") and not no_verify:
        return "Blocked: --no-verify requires explicit opt-in via no_verify=True."

    # 3. force push to main/master 차단
    if parsed_subcmd == "push" and args:
        if ("--force" in args or "--force-with-lease" in args):
            target_branch = _extract_push_target(args)
            if target_branch in ("main", "master"):
                return f"Blocked: force push to '{target_branch}' is not allowed."

    # 4. 위험 등급 판정
    danger_level = _classify_git_command(subcommand, args or "")

    # 5. 위험 명령은 interrupt로 승인 요청
    if danger_level == "dangerous":
        approval = interrupt({
            "type": "permission_request",
            "tool": "git",
            "input": f"git {full_args}",
            "message": f"Dangerous git command: 'git {full_args}'. Allow?",
        })
        if not approval:
            return f"User denied: git {full_args}"

    # 6. 민감 파일 스테이징 감지 (git add 시)
    if parsed_subcmd == "add" and args:
        sensitive = _detect_sensitive_files(args)
        if sensitive:
            approval = interrupt({
                "type": "permission_request",
                "tool": "git",
                "input": f"git add {args}",
                "message": f"Staging potentially sensitive files: {sensitive}. Allow?",
            })
            if not approval:
                return f"User denied staging sensitive files: {sensitive}"

    # 7. 실행
    import asyncio
    proc = await asyncio.create_subprocess_shell(
        f"git {full_args}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        return "Git command timed out after 60s"

    output = stdout.decode() + stderr.decode()
    return output.strip() or "(no output)"


def _classify_git_command(subcommand: str, args: str) -> str:
    """Git 명령의 위험 등급 반환: read_only | destructive | dangerous"""
    full_cmd = f"{subcommand} {args}".strip()
    for pattern in GIT_DANGEROUS:
        if full_cmd.startswith(pattern) or subcommand.startswith(pattern):
            return "dangerous"
    for pattern in GIT_DESTRUCTIVE:
        if full_cmd.startswith(pattern) or subcommand.startswith(pattern):
            return "destructive"
    return "read_only"


def _detect_sensitive_files(args: str) -> list[str]:
    """git add 인수에서 민감 파일 패턴 감지"""
    if args.strip() in ("-A", "--all", "."):
        return [f"'{args.strip()}' stages all files - review before committing"]
    files = shlex.split(args)
    return [f for f in files if any(p in f.lower() for p in SENSITIVE_PATTERNS)]


def _extract_push_target(args: str) -> str:
    """push 인수에서 대상 브랜치 추출"""
    parts = shlex.split(args)
    # git push origin main --force → "main"
    non_flag = [p for p in parts if not p.startswith("-")]
    if len(non_flag) >= 2:
        return non_flag[1]  # remote 다음이 branch
    return ""
```

### 4.2 bash 도구 (tools/bash.py)

```python
# sds_ax/tools/bash.py
from langchain.tools import tool
from enum import Enum
import asyncio
import shlex
import shutil


class SandboxMode(Enum):
    NONE = "none"
    RESTRICTED = "restricted"
    CONTAINER = "container"


class BashSandbox:
    """bash 도구의 샌드박스 실행 환경"""

    def __init__(self, config: dict):
        self._mode = SandboxMode(config.get("mode", "none"))
        self._container_image = config.get("container_image", "sds-ax-sandbox:latest")
        self._project_root = config.get("project_root", ".")

    async def execute(self, command: str, timeout: int = 120) -> str:
        """샌드박스 모드에 따라 명령 실행"""
        if self._mode == SandboxMode.NONE:
            return await self._exec_direct(command, timeout)
        elif self._mode == SandboxMode.RESTRICTED:
            return await self._exec_restricted(command, timeout)
        elif self._mode == SandboxMode.CONTAINER:
            return await self._exec_container(command, timeout)

    async def _exec_direct(self, command: str, timeout: int) -> str:
        """제한 없는 직접 실행"""
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._project_root,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {timeout}s"
        return (stdout.decode() + stderr.decode()).strip()

    async def _exec_restricted(self, command: str, timeout: int) -> str:
        """Linux namespace 기반 제한된 실행

        - 파일시스템: project_root 하위로 제한
        - 프로세스: fork bomb 방지 (ulimit)
        - 메모리: 2GB 제한
        """
        restricted_cmd = (
            f"unshare --mount --map-root-user "
            f"bash -c '"
            f"mount --bind {self._project_root} /workspace && "
            f"cd /workspace && "
            f"ulimit -u 256 && "
            f"ulimit -v 2097152 && "
            f"timeout {timeout} bash -c {shlex.quote(command)}"
            f"'"
        )
        proc = await asyncio.create_subprocess_shell(
            restricted_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Sandboxed command timed out after {timeout}s"
        return (stdout.decode() + stderr.decode()).strip()

    async def _exec_container(self, command: str, timeout: int) -> str:
        """Docker/Podman 컨테이너 내 실행

        - 프로젝트 디렉토리만 bind mount
        - 리소스 제한 (CPU 2, memory 4GB)
        - 네트워크: 기본 차단
        """
        runtime = "docker" if shutil.which("docker") else "podman"
        container_cmd = (
            f"{runtime} run --rm "
            f"--memory=4g --cpus=2 "
            f"-v {self._project_root}:/workspace:rw "
            f"-w /workspace "
            f"--network none "
            f"{self._container_image} "
            f"bash -c {shlex.quote(command)}"
        )
        proc = await asyncio.create_subprocess_shell(
            container_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 30)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Container command timed out after {timeout}s"
        return (stdout.decode() + stderr.decode()).strip()


# 전역 sandbox 인스턴스 (설정 로드 시 초기화)
_sandbox: BashSandbox | None = None


def init_sandbox(config: dict):
    global _sandbox
    _sandbox = BashSandbox(config)


@tool
async def bash_tool(
    command: str,
    timeout: int = 120,
) -> str:
    """셸 명령을 실행합니다. 샌드박스 설정에 따라 격리 수준이 결정됩니다.

    Args:
        command: 실행할 셸 명령
        timeout: 타임아웃 (초, 기본 120)
    """
    if _sandbox is None:
        init_sandbox({})
    return await _sandbox.execute(command, timeout=timeout)
```

### 4.3 web_search / fetch_url / ask_user

```python
# sds_ax/tools/web_search.py
from langchain.tools import tool


@tool
async def web_search_tool(query: str, max_results: int = 5) -> str:
    """Tavily를 사용하여 웹 검색합니다.

    Args:
        query: 검색 쿼리
        max_results: 최대 결과 수 (기본 5)
    """
    from tavily import AsyncTavilyClient
    import os

    client = AsyncTavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    response = await client.search(query, max_results=max_results)

    results = []
    for r in response.get("results", []):
        results.append(f"**{r['title']}**\n{r['url']}\n{r['content'][:500]}")
    return "\n\n---\n\n".join(results) if results else "No results found."


# sds_ax/tools/fetch_url.py
from langchain.tools import tool


@tool
async def fetch_url_tool(url: str, max_length: int = 10000) -> str:
    """URL의 콘텐츠를 가져옵니다.

    Args:
        url: 가져올 URL
        max_length: 최대 응답 길이 (기본 10000)
    """
    import httpx

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()

    content = response.text
    if len(content) > max_length:
        content = content[:max_length] + f"\n\n... (truncated, {len(response.text)} total chars)"
    return content


# sds_ax/tools/ask_user.py
from langchain.tools import tool
from langgraph.types import interrupt


@tool
def ask_user_tool(question: str) -> str:
    """사용자에게 질문하고 응답을 기다립니다. 명확화나 승인이 필요할 때 사용합니다.

    Args:
        question: 사용자에게 보여줄 질문
    """
    # DeepAgents의 interrupt 메커니즘 사용
    # 에이전트 실행이 중단되고, CLI가 사용자 입력을 받아
    # Command(resume=...) 로 응답을 전달한다
    response = interrupt({
        "type": "ask_user",
        "question": question,
    })
    return str(response)
```

---

## 4.5 CLI 메인 루프 (cli.py)

프로그램의 진입점. `create_sds_ax_agent()`가 반환한 에이전트를 실행하는 메인 루프.

```python
# sds_ax/cli.py
import time
from langgraph.types import Command
from sds_ax.core.agent import create_sds_ax_agent
from sds_ax.config.schema import AgentConfig
from sds_ax.config.settings import load_config
from sds_ax.commands.registry import parse_slash_command


def run_interactive(config: AgentConfig):
    """대화형 모드 메인 루프 (동기 — 스트리밍 출력)"""
    agent = create_sds_ax_agent(config)
    session_id = f"session-{int(time.time())}"
    invoke_config = {"configurable": {"thread_id": session_id}}

    print("ATOM-CODE ready. Type /help for commands.")

    while True:
        user_input = input("> ").strip()
        if not user_input:
            continue

        # 슬래시 커맨드
        if user_input.startswith("/"):
            result = parse_slash_command(user_input, agent, invoke_config)
            if result == "exit":
                break
            if result:
                print(result)
            continue

        # 에이전트 실행 — 스트리밍 + HITL 처리
        _stream_with_hitl(agent, user_input, invoke_config)


def _stream_with_hitl(agent, user_input: str, config: dict) -> None:
    """에이전트 호출을 스트리밍하고, interrupt 발생 시 HITL 루프 처리.

    흐름:
      1. agent.stream(input, stream_mode="messages") 로 토큰 단위 스트리밍
      2. 각 chunk의 token.content를 즉시 터미널에 출력 (flush)
      3. 스트림 종료 후 interrupt가 감지되면 사용자에게 승인/거부/편집 요청
      4. Command(resume=...) 로 재개 — resume은 동기 invoke 사용
      5. interrupt가 없을 때까지 반복
    """
    import json
    import sys

    input_payload = {"messages": [{"role": "user", "content": user_input}]}

    # --- 첫 호출: 스트리밍 ---
    interrupt_info = _do_stream(agent, input_payload, config)

    # --- HITL interrupt 처리 루프 ---
    while interrupt_info is not None:
        decisions = []

        for intr in interrupt_info:
            tool_name = intr.value.get("tool", "unknown")
            tool_input = intr.value.get("input", "")
            print(f"\n[HITL] {tool_name}({tool_input})")
            print("  (a)pprove / (r)eject / (e)dit ?")

            choice = input("  > ").strip().lower()

            if choice in ("a", "approve", "y", "yes"):
                decisions.append({"type": "approve"})
            elif choice in ("e", "edit"):
                edited = input("  Enter edited args (JSON): ").strip()
                try:
                    edited_args = json.loads(edited)
                    decisions.append({
                        "type": "edit",
                        "edited_action": {"name": tool_name, "args": edited_args},
                    })
                except json.JSONDecodeError:
                    print("  Invalid JSON, rejecting.")
                    decisions.append({"type": "reject"})
            else:
                decisions.append({"type": "reject"})

        # resume은 동기 invoke — Command는 짧은 제어 메시지이므로
        # 스트리밍할 필요 없음
        result = agent.invoke(
            Command(resume={"decisions": decisions}),
            config=config,
        )

        # resume 결과에 다시 interrupt가 있는지 확인
        if "__interrupt__" in result:
            interrupt_info = result["__interrupt__"]
        else:
            # 최종 응답 출력 (resume은 invoke이므로 여기서 출력)
            messages = result.get("messages", [])
            if messages:
                last = messages[-1]
                print(getattr(last, "content", str(last)))
            interrupt_info = None

    print()  # 스트리밍 출력 후 줄바꿈


def _do_stream(agent, input_payload: dict, config: dict):
    """agent.stream()으로 토큰을 실시간 출력하고, interrupt 정보를 반환.

    Returns:
        interrupt 목록 (list) 또는 None (interrupt 없음)
    """
    import sys

    interrupt_info = None

    for chunk in agent.stream(input_payload, config=config, stream_mode="messages"):
        token, metadata = chunk

        # 토큰 내용이 있으면 즉시 출력
        if hasattr(token, "content") and token.content:
            sys.stdout.write(token.content)
            sys.stdout.flush()

        # interrupt 감지
        if hasattr(token, "type") and token.type == "__interrupt__":
            interrupt_info = token.value

    # stream_mode="messages"에서 interrupt는 마지막 chunk에 나타남
    # 일부 LangGraph 버전은 반환값의 메타데이터로 전달
    if interrupt_info is None and metadata and metadata.get("__interrupt__"):
        interrupt_info = metadata["__interrupt__"]

    return interrupt_info


def run_non_interactive(config: AgentConfig, task: str):
    """비대화형 모드 (스트리밍 출력)"""
    agent = create_sds_ax_agent(config)
    config_dict = {"configurable": {"thread_id": f"task-{hash(task)}"}}
    _stream_with_hitl(agent, task, config_dict)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ATOM-CODE CLI Agent")
    parser.add_argument("-n", "--non-interactive", type=str, help="Run single task")
    parser.add_argument("--resume", type=str, help="Resume session by ID")
    args = parser.parse_args()

    ensure_api_keys()
    config = load_config()

    if args.non_interactive:
        run_non_interactive(config, args.non_interactive)
    elif args.resume:
        from sds_ax.session.restore import restore_session
        agent = create_sds_ax_agent(config)
        restore_session(agent, args.resume)
    else:
        run_interactive(config)
```

---

### 4.5.1 스트리밍 모드 선택 가이드

LangGraph `graph.stream()`은 `stream_mode` 파라미터로 출력 형태를 제어한다.
ATOM-CODE CLI는 용도에 따라 세 가지 모드를 사용할 수 있다.

| `stream_mode` | 용도 | 설명 |
|---|---|---|
| `"messages"` | **TUI 실시간 토큰 표시** (기본) | 각 chunk가 `(token, metadata)` 튜플. `token.content`를 즉시 출력하여 사용자에게 타이핑 효과를 제공한다. CLI 메인 루프에서 사용. |
| `"updates"` | 상태 변화 추적 | 각 chunk가 노드 이름과 해당 노드의 state delta. 디버깅이나 로깅에 유용하다. |
| `"custom"` | 진행 표시기 | 그래프 내부에서 `stream_writer`로 보낸 커스텀 이벤트를 수신. 파일 다운로드 진행률, 코드 실행 단계 등 사용자 정의 프로그레스 표시에 활용. |

현재 `_do_stream()`은 `stream_mode="messages"`를 사용하여 토큰을 실시간으로
터미널에 출력한다. interrupt 재개 시에는 `agent.invoke(Command(resume=...))`를
동기 호출하는데, resume 응답은 보통 짧은 제어 메시지이므로 스트리밍이 불필요하다.

---

## 4.6 커스텀 레이어 통합 (미들웨어 훅)

ATOM-CODE의 커스텀 레이어(Stall Detection, Context Compaction, Auto-Dream)는
`create_deep_agent()`의 `middleware` 파라미터를 통해 에이전트 실행 루프에 삽입된다.

```python
# sds_ax/core/agent.py — create_sds_ax_agent() 내부에서 커스텀 미들웨어 구성

from sds_ax.layers.stall_detector import StallDetector
from sds_ax.layers.context_compaction import ContextCompactor
from sds_ax.layers.auto_dream import AutoDreamExtractor


def _build_custom_middleware(config, store):
    """ATOM-CODE 커스텀 레이어를 DeepAgents 미들웨어 훅으로 통합"""

    stall_detector = StallDetector(config)
    compactor = ContextCompactor(config)
    auto_dream = AutoDreamExtractor(
        model=_create_lightweight_model(config.fallback_model),
        store=store,
    )

    class SdsAxMiddleware:
        """DeepAgents 미들웨어 훅 인터페이스 구현"""

        async def before_model(self, state, config):
            """LLM 호출 전: 컨텍스트 사용률 확인 → 컴팩션"""
            compacted = compactor.check_and_compact(
                state["messages"],
                model_context_window=200_000,
            )
            if compacted is not None:
                return {"messages": compacted}
            return None

        async def after_model(self, state, response, config):
            """LLM 응답 후: Stall Detection"""
            recovery = stall_detector.check(response)
            if recovery:
                return recovery  # {"action": "inject_message", ...} or {"action": "stop"}
            return None

        async def wrap_tool_call(self, tool_call, config):
            """도구 실행 전: allow/deny 규칙 적용"""
            permissions = config.get("configurable", {}).get("permissions", {})
            return _apply_permission_rules(tool_call, permissions)

        async def after_agent(self, state, config):
            """에이전트 턴 완료: Auto-Dream 메모리 추출"""
            token_count = sum(len(str(getattr(m, "content", ""))) // 4
                             for m in state.get("messages", []))
            tool_count = sum(1 for m in state.get("messages", [])
                            if hasattr(m, "tool_call_id"))
            await auto_dream.maybe_extract(state["messages"], token_count, tool_count)

    return SdsAxMiddleware()


def _apply_permission_rules(tool_call, permissions):
    """allow/deny 규칙 적용

    우선순위: deny → allow → interrupt_on → 모드별 기본값
    - deny 매칭 → 즉시 차단 (interrupt_on 무시)
    - allow 매칭 → 즉시 허용 (interrupt_on 바이패스)
    - 둘 다 아님 → None (기본 흐름, interrupt_on 확인)
    """
    import fnmatch
    tool_name = tool_call.get("name", "")
    tool_input = str(tool_call.get("args", ""))
    full = f"{tool_name}({tool_input})"

    for rule in permissions.get("deny", []):
        if fnmatch.fnmatch(full, rule):
            return {"blocked": True, "reason": f"Denied by rule: {rule}"}

    for rule in permissions.get("allow", []):
        if fnmatch.fnmatch(full, rule):
            return {"bypass_interrupt": True}

    return None
```

그리고 `create_sds_ax_agent()` 내부의 `create_deep_agent()` 호출에 `middleware` 추가:

```python
agent = create_deep_agent(
    ...
    middleware=[_build_custom_middleware(config, store)],
)
```

통합 포인트 요약:

| 훅 | 커스텀 레이어 | 동작 |
|------|-------------|------|
| `before_model` | ContextCompactor | 컨텍스트 70/85/95% → 압축 |
| `after_model` | StallDetector | 빈 턴 3회 → 넛지/모델전환/ask_user/중단 |
| `wrap_tool_call` | Permission Rules | deny → 차단, allow → interrupt_on 바이패스 |
| `after_agent` | AutoDreamExtractor | 토큰 3000+ 또는 도구 3회+ → 비차단 메모리 추출 |

---

## 5. 서브에이전트 구현

DeepAgents 프레임워크가 서브에이전트의 생명주기를 완전히 관리한다.
ATOM-CODE는 `create_deep_agent(subagents=[...])` 에 선언적 config만 전달하면 된다.

### 5.1 선언적 서브에이전트 config

`create_sds_ax_agent()` 내부에서 전달하는 서브에이전트 선언은 **section 3.1**에 정의되어 있다.
5가지 타입: explorer, coder, researcher, reviewer, planner.

### 5.2 프레임워크가 자동 처리하는 것

```
메인 에이전트가 task(agent="coder", instruction="...") 호출
    │
    ▼
SubAgentMiddleware:
    1. subagent_configs에서 "coder" config 로드
    2. ephemeral/stateless 서브에이전트 생성
    3. 서브에이전트 실행 (timeout=600s 이내)
    4. 서브에이전트 내부에서 interrupt 발생 시 → 메인으로 전파
    5. 서브에이전트 완료 시 → 결과를 task 도구 응답으로 반환
    6. 서브에이전트 자동 소멸
```

### 5.3 서브에이전트 HITL 전파

서브에이전트 내부에서 `interrupt_on`에 해당하는 도구가 호출되면,
프레임워크가 자동으로 메인 에이전트를 거쳐 사용자에게 전파한다:

```
서브에이전트 → edit_file 호출
    │
    ▼ (interrupt_on["edit_file"] == True)
서브에이전트 실행 중단
    │
    ▼
메인 에이전트로 interrupt 전파
    │
    ▼
CLI가 사용자에게 승인 요청
    │
    ▼
사용자 응답 → Command(resume={"decisions": [{"type": "approve"}]})
    │
    ▼
메인 에이전트 → 서브에이전트로 resume 전달
    │
    ▼
서브에이전트 실행 재개
```

---

## 6. 메모리 시스템

### 6.1 CompositeBackend 구성

`create_deep_agent(backend=...)` 에 전달하는 백엔드 구성:

```python
backend=lambda rt: CompositeBackend(
    StateBackend(rt),                      # 기본: 세션 내 임시 상태 (ephemeral)
    {
        "/memories/": StoreBackend(rt),    # 장기 메모리 (persistent, cross-session)
        "/project/": StoreBackend(rt),     # 프로젝트별 메모리 (persistent)
        "/workspace/": FilesystemBackend(rt),  # 디스크 파일 직접 접근
    },
)
```

```
┌─────────────────────────────────────────────────────────┐
│                   CompositeBackend                       │
│                                                         │
│  ┌─────────────────┐  경로 라우팅:                       │
│  │  StateBackend    │  기본 (매칭 안 되는 경로)           │
│  │  (ephemeral)     │  → 세션 내 임시 데이터              │
│  └─────────────────┘                                     │
│                                                         │
│  ┌─────────────────┐  /memories/* 경로                   │
│  │  StoreBackend    │  → 사용자/도메인/피드백 메모리       │
│  │  (persistent)    │  → InMemoryStore (dev)              │
│  └─────────────────┘    PostgresStore (prod)              │
│                                                         │
│  ┌─────────────────┐  /project/* 경로                    │
│  │  StoreBackend    │  → 프로젝트별 아키텍처/컨벤션 메모리│
│  │  (persistent)    │                                    │
│  └─────────────────┘                                     │
│                                                         │
│  ┌─────────────────┐  /workspace/* 경로                  │
│  │ FilesystemBackend│  → 디스크 파일 직접 읽기/쓰기      │
│  │  (disk)          │                                    │
│  └─────────────────┘                                     │
└─────────────────────────────────────────────────────────┘
```

### 6.2 Auto-Dream 메모리 추출 (layers/auto_dream.py)

프레임워크의 MemoryMiddleware가 메모리 검색/주입을 처리하지만,
**대화에서 새로운 메모리를 자동 추출**하는 것은 ATOM-CODE 커스텀 레이어다.

```python
# sds_ax/layers/auto_dream.py
import asyncio
from typing import Optional


class AutoDreamExtractor:
    """대화에서 자동으로 메모리를 추출하는 비동기 프로세서

    경량 LLM을 포크하여 메인 에이전트 실행과 병렬로 동작.
    임계값 (토큰 또는 도구 호출 수) 도달 시 추출 트리거.
    """

    EXTRACTION_PROMPT = """
    Analyze the following conversation segment and extract information worth
    storing as long-term memory.

    Extract these types:
    1. user: user's role, expertise, preferences
    2. feedback: corrections or confirmations about work style
    3. domain: domain knowledge (business logic, terminology, rules)
    4. project: project-specific context (architecture decisions, conventions)

    Do NOT extract:
    - Code itself (available in git/files)
    - Temporary task state
    - Information already in existing memories

    Existing memories:
    {existing_memories}

    Conversation segment:
    {conversation_segment}

    Return as JSON array:
    [{"type": "...", "name": "...", "description": "...", "content": "...", "tags": [...]}]
    Return empty array [] if nothing to extract.
    """

    def __init__(self, model, store):
        self._model = model       # 경량 LLM (haiku 등)
        self._store = store
        self._last_extraction_token_count = 0
        self._last_extraction_tool_count = 0

    async def maybe_extract(
        self,
        messages: list,
        current_token_count: int,
        tool_count: int,
    ) -> None:
        """임계값 확인 후 비차단 추출 트리거"""
        token_delta = current_token_count - self._last_extraction_token_count
        tool_delta = tool_count - self._last_extraction_tool_count

        if token_delta < 3000 and tool_delta < 3:
            return  # 임계값 미달

        # 비차단: fire-and-forget
        asyncio.create_task(self._extract(messages))
        self._last_extraction_token_count = current_token_count
        self._last_extraction_tool_count = tool_count

    async def _extract(self, messages: list) -> None:
        """실제 추출 로직 (경량 LLM 호출)"""
        try:
            existing = await self._store.list_all_summaries()
            recent_segment = messages[-20:]  # 최근 20개 메시지만

            from langchain_core.messages import SystemMessage
            response = await self._model.ainvoke([
                SystemMessage(content=self.EXTRACTION_PROMPT.format(
                    existing_memories=existing,
                    conversation_segment=_format_messages(recent_segment),
                ))
            ])

            entries = _parse_json_response(response.content)
            for entry in entries:
                # StoreBackend에 /memories/ 경로로 저장
                await self._store.put(
                    namespace=("memory", entry["type"]),
                    key=entry["name"],
                    value=entry,
                )
        except Exception:
            pass  # 추출 실패는 메인 흐름에 영향 없음


def _format_messages(messages: list) -> str:
    """메시지 리스트를 텍스트로 변환"""
    lines = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", str(m))
        if content:
            lines.append(f"[{role}] {content[:500]}")
    return "\n".join(lines)


def _parse_json_response(text: str) -> list[dict]:
    """LLM 응답에서 JSON 배열 추출"""
    import json
    try:
        # JSON 블록 찾기
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
```

---

## 7. Stall Detection (layers/stall_detector.py)

프레임워크에 없는 ATOM-CODE 전용 커스텀 레이어.
에이전트가 진행하지 못하고 빈 턴을 반복할 때 단계적으로 복구한다.

### 7.1 복구 단계

```
빈 턴 3회 연속 감지
    │
    ▼
[Stage 1] 넛지 메시지 삽입
    "No progress detected. Try a different approach, or use ask_user."
    │
    ▼ (여전히 진행 없음)
[Stage 2] 모델 전환
    config.model → config.fallback_model (예: sonnet → haiku)
    "다른 관점으로 접근"
    │
    ▼ (여전히 진행 없음)
[Stage 3] 사용자에게 방향 질문 (interrupt)
    "The agent is not making progress. Could you provide guidance?"
    │
    ▼ (여전히 진행 없음)
[Stage 4] 중단
    "Agent stopped after multiple stall recovery attempts."
```

### 7.2 구현

```python
# sds_ax/layers/stall_detector.py
from langgraph.types import interrupt
from langchain_core.messages import HumanMessage


class StallDetector:
    """에이전트 멈춤 감지 및 단계적 복구"""

    def __init__(self, config):
        self._max_empty_turns = 3           # 빈 턴 감지 임계값
        self._consecutive_empty = 0
        self._recovery_stage = 0            # 0=none, 1=nudge, 2=model_switch, 3=ask, 4=stop
        self._fallback_model = config.fallback_model

    def check(self, last_message) -> dict | None:
        """매 턴 후 호출. 복구 액션이 필요하면 dict 반환, 아니면 None"""
        has_tool_calls = hasattr(last_message, "tool_calls") and last_message.tool_calls

        if has_tool_calls:
            self._consecutive_empty = 0
            self._recovery_stage = 0
            return None

        self._consecutive_empty += 1

        if self._consecutive_empty < self._max_empty_turns:
            return None

        # 멈춤 감지 → 단계별 복구
        self._recovery_stage += 1

        if self._recovery_stage == 1:
            # Stage 1: 넛지
            self._consecutive_empty = 0
            return {
                "action": "inject_message",
                "message": HumanMessage(content=(
                    "[System] No progress detected in previous attempts. "
                    "Try a different approach, or use ask_user if you need specific guidance."
                )),
            }

        elif self._recovery_stage == 2:
            # Stage 2: 모델 전환
            self._consecutive_empty = 0
            return {
                "action": "switch_model",
                "model": self._fallback_model,
            }

        elif self._recovery_stage == 3:
            # Stage 3: 사용자에게 질문
            self._consecutive_empty = 0
            response = interrupt({
                "type": "stall_recovery",
                "message": "The agent is not making progress. Could you provide guidance?",
            })
            return {
                "action": "inject_message",
                "message": HumanMessage(content=str(response)),
            }

        else:
            # Stage 4: 중단
            return {"action": "stop"}
```

---

## 8. 컨텍스트 관리 (layers/context_compaction.py)

컨텍스트 윈도우 사용률에 따른 3단계 자동 압축.

### 8.1 임계값

| 단계 | 임계값 | 동작 |
|------|--------|------|
| Auto Compact | 70% | 오래된 메시지를 요약으로 대체 |
| Reactive Compact | 85% | 더 공격적인 요약 + 도구 결과 축약 |
| Emergency Compact | 95% | 최근 N개 메시지만 유지, 나머지 요약 한 줄 |

### 8.2 구현

```python
# sds_ax/layers/context_compaction.py
from langchain_core.messages import SystemMessage, HumanMessage


class ContextCompactor:
    """컨텍스트 윈도우 사용률 기반 자동 압축"""

    def __init__(self, config):
        self._auto_threshold = config.context.auto_compact_threshold          # 0.7
        self._reactive_threshold = config.context.reactive_compact_threshold  # 0.85
        self._emergency_threshold = config.context.emergency_compact_threshold  # 0.95
        self._model = None  # 요약용 LLM (lazy init)

    def check_and_compact(self, messages: list, model_context_window: int) -> list | None:
        """컨텍스트 사용률 확인 후 필요 시 압축된 메시지 리스트 반환

        Returns:
            None: 압축 불필요
            list: 압축된 메시지 리스트
        """
        token_count = _estimate_tokens(messages)
        usage_ratio = token_count / model_context_window

        if usage_ratio < self._auto_threshold:
            return None

        if usage_ratio >= self._emergency_threshold:
            return self._emergency_compact(messages)
        elif usage_ratio >= self._reactive_threshold:
            return self._reactive_compact(messages)
        else:
            return self._auto_compact(messages)

    def _auto_compact(self, messages: list) -> list:
        """Auto Compact (70%): 오래된 메시지 요약"""
        # 메시지를 절반으로 분할: 오래된 절반 → 요약
        midpoint = len(messages) // 2
        old_messages = messages[:midpoint]
        recent_messages = messages[midpoint:]

        summary = _summarize_messages(old_messages)
        return [
            SystemMessage(content=f"[Compacted context summary]\n{summary}"),
            *recent_messages,
        ]

    def _reactive_compact(self, messages: list) -> list:
        """Reactive Compact (85%): 공격적 요약 + 도구 결과 축약"""
        # 최근 1/3만 유지, 나머지 요약
        keep_count = max(len(messages) // 3, 10)
        old_messages = messages[:-keep_count]
        recent_messages = messages[-keep_count:]

        # 도구 결과도 축약
        recent_messages = [_truncate_tool_result(m) for m in recent_messages]

        summary = _summarize_messages(old_messages)
        return [
            SystemMessage(content=f"[Compacted context summary]\n{summary}"),
            *recent_messages,
        ]

    def _emergency_compact(self, messages: list) -> list:
        """Emergency Compact (95%): 최소한만 유지"""
        # 최근 5개 메시지만 유지
        recent = messages[-5:]
        summary = "Previous conversation compacted due to context limit."
        return [
            SystemMessage(content=f"[Emergency compact]\n{summary}"),
            *recent,
        ]


def _estimate_tokens(messages: list) -> int:
    """메시지 리스트의 대략적 토큰 수 추정 (4 chars = 1 token)"""
    total_chars = sum(len(getattr(m, "content", str(m)) or "") for m in messages)
    return total_chars // 4


def _truncate_tool_result(message) -> object:
    """도구 결과 메시지의 content가 너무 길면 축약"""
    if hasattr(message, "tool_call_id") and message.content:
        if len(message.content) > 2000:
            message.content = message.content[:2000] + "\n... (truncated)"
    return message


def _summarize_messages(messages: list) -> str:
    """메시지 리스트를 간단한 요약으로 변환"""
    lines = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", "")
        if content and role in ("human", "ai"):
            lines.append(f"- [{role}] {content[:200]}")
    return "\n".join(lines[-20:]) if lines else "No significant content."
```

---

## 9. MCP 도구 권한 (tools/mcp/permissions.py)

외부 MCP 서버의 도구에 대한 trust_level 기반 권한 모델.

### 9.1 Trust Level

| Level | 동작 |
|-------|------|
| `trusted` | 서버 메타데이터의 is_read_only 참조. read-only 도구는 자동 승인 |
| `untrusted` | 모든 도구를 파괴적으로 간주. 매 호출 interrupt (기본값) |
| `ask` | 매 호출마다 사용자에게 승인 요청 |

### 9.2 구현

```python
# sds_ax/tools/mcp/permissions.py
from enum import Enum
from typing import Optional
import fnmatch


class MCPTrustLevel(Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    ASK = "ask"


class MCPPermissionResolver:
    """MCP 외부 도구의 권한을 판정"""

    def __init__(self, mcp_config: dict, permission_rules: dict):
        self._servers = mcp_config.get("servers", {})
        self._allow_rules = permission_rules.get("allow", [])
        self._deny_rules = permission_rules.get("deny", [])

    def classify_tool(
        self,
        server_name: str,
        tool_name: str,
        tool_metadata: Optional[dict] = None,
    ) -> dict:
        """MCP 도구의 권한 분류 결과 반환

        Returns:
            {
                "is_read_only": bool,
                "is_concurrent_safe": bool,
                "requires_approval": bool,
                "denied": bool,
            }
        """
        full_name = f"{server_name}.{tool_name}"
        server_config = self._servers.get(server_name, {})
        trust_level = MCPTrustLevel(server_config.get("trust_level", "untrusted"))

        # 1. deny 규칙 먼저 확인
        for rule in self._deny_rules:
            if self._match_rule(rule, full_name):
                return {
                    "is_read_only": False,
                    "is_concurrent_safe": False,
                    "requires_approval": False,
                    "denied": True,
                }

        # 2. allow 규칙
        for rule in self._allow_rules:
            if self._match_rule(rule, full_name):
                return {
                    "is_read_only": True,
                    "is_concurrent_safe": True,
                    "requires_approval": False,
                    "denied": False,
                }

        # 3. 서버별 tool_overrides
        overrides = server_config.get("tool_overrides", {})
        if tool_name in overrides:
            is_ro = overrides[tool_name].get("is_read_only", False)
            return {
                "is_read_only": is_ro,
                "is_concurrent_safe": is_ro,
                "requires_approval": not is_ro,
                "denied": False,
            }

        # 4. trust_level 기반 기본 분류
        if trust_level == MCPTrustLevel.TRUSTED:
            is_ro = (tool_metadata or {}).get("is_read_only", False)
            return {
                "is_read_only": is_ro,
                "is_concurrent_safe": is_ro,
                "requires_approval": not is_ro,
                "denied": False,
            }
        elif trust_level == MCPTrustLevel.ASK:
            return {
                "is_read_only": False,
                "is_concurrent_safe": False,
                "requires_approval": True,
                "denied": False,
            }
        else:  # UNTRUSTED (기본)
            return {
                "is_read_only": False,
                "is_concurrent_safe": False,
                "requires_approval": True,
                "denied": False,
            }

    @staticmethod
    def _match_rule(rule: str, full_name: str) -> bool:
        """규칙 패턴 매칭 (glob 스타일)"""
        rule_normalized = rule.replace("(*)", "").replace("(*", "").rstrip(")")
        return fnmatch.fnmatch(full_name, rule_normalized + "*")
```

---

## 10. 세션 복원 (session/restore.py)

세션 복원 시 체크포인트에서 상태를 로드하고, 중단된 서브에이전트를 처리한다.

### 10.1 복원 흐름

```
sds-ax --resume <session_id>
    │
    ▼
체크포인터에서 상태 로드 (MemorySaver / PostgresSaver)
    │
    ▼
서브에이전트 상태 처리:
    ├── completed/failed/aborted → 이력 보존 (결과 참조 가능)
    ├── running → interrupted로 변경, 사용자에게 재실행 제안
    └── waiting_approval → 사용자에게 승인 재요청
    │
    ▼
파일 잠금 전부 해제 (이전 세션의 잠금은 무효)
    │
    ▼
에이전트 실행 재개
```

### 10.2 구현

```python
# sds_ax/session/restore.py
from langgraph.types import interrupt, Command


async def restore_session(agent, session_id: str) -> None:
    """체크포인트에서 세션 복원 후 에이전트 실행 재개

    DeepAgents의 체크포인터가 상태를 자동 저장하므로,
    동일한 thread_id로 에이전트를 호출하면 이전 상태에서 재개된다.
    """
    config = {"configurable": {"thread_id": session_id}}

    # 체크포인트에서 현재 상태 조회
    state = await agent.aget_state(config)

    if state is None:
        print(f"Session '{session_id}' not found.")
        return

    # 중단된 interrupt가 있으면 처리
    if state.next:
        # 중단된 지점에서 재개
        print(f"Resuming session '{session_id}' from interrupted state...")
        print(f"Pending: {state.next}")

        # 사용자에게 재개 방법 안내
        # CLI 루프가 Command(resume=...) 로 처리
        return

    print(f"Session '{session_id}' restored. Ready for input.")


async def list_sessions(checkpointer) -> list[dict]:
    """저장된 세션 목록 조회"""
    # 체크포인터 구현에 따라 다름
    # MemorySaver: 인메모리 (프로세스 재시작 시 소멸)
    # PostgresSaver: DB에서 조회
    sessions = []
    async for checkpoint in checkpointer.alist():
        sessions.append({
            "session_id": checkpoint.config.get("configurable", {}).get("thread_id"),
            "created_at": checkpoint.metadata.get("created_at"),
        })
    return sessions
```

---

## 11. 설정 스키마 (config/schema.py)

```python
# sds_ax/config/schema.py
from pydantic import BaseModel, Field
from typing import Optional, Literal


class PermissionConfig(BaseModel):
    mode: Literal["default", "auto_approve", "read_only", "plan_only", "shell_confirm"] = "default"
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    auto_extract: bool = True
    extraction_threshold_tokens: int = 3000
    max_memory_entries: int = 500


class LoopConfig(BaseModel):
    max_turns: int = 200              # 관대한 한도
    tool_timeout_seconds: int = 120
    api_timeout_seconds: int = 60
    stall_detection: bool = True


class SubagentConfig(BaseModel):
    max_concurrent: int = 5
    default_max_turns: int = 100
    default_timeout_seconds: int = 600  # 관대한 한도 (10분)
    hitl_propagation: bool = True


class ContextConfig(BaseModel):
    auto_compact_threshold: float = 0.7
    reactive_compact_threshold: float = 0.85
    emergency_compact_threshold: float = 0.95


class SandboxConfig(BaseModel):
    mode: Literal["none", "restricted", "container"] = "none"
    allowed_hosts: list[str] = Field(default_factory=list)
    container_image: str = "sds-ax-sandbox:latest"


class MCPServerConfig(BaseModel):
    command: str                          # MCP 서버 실행 명령
    args: list[str] = Field(default_factory=list)
    trust_level: Literal["trusted", "untrusted", "ask"] = "untrusted"
    tool_overrides: dict[str, dict] = Field(default_factory=dict)
    concurrent_safe_tools: list[str] = Field(default_factory=list)


class MCPConfig(BaseModel):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """ATOM-CODE 전체 설정 스키마"""
    model: str = "claude-sonnet-4-5-20250929"
    fallback_model: str = "claude-haiku-4-5-20251001"
    project_root: str = "."

    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    mcp: Optional[MCPConfig] = None
```

### 11.2 설정 로더 (config/settings.py)

```python
# sds_ax/config/settings.py
import os
import json
from pathlib import Path
from sds_ax.config.schema import AgentConfig


def load_config(
    cli_overrides: dict | None = None,
    project_root: str | None = None,
) -> AgentConfig:
    """설정 로드 — 우선순위: CLI > 환경변수 > 프로젝트 설정 > 사용자 설정 > 기본값

    설정 파일 탐색 순서:
    1. .deepagents/settings.json (프로젝트)
    2. ~/.deepagents/settings.json (사용자 전역)
    """
    # 1. 기본값 (Pydantic 모델 기본값)
    config_dict = {}

    # 2. 사용자 전역 설정
    user_config_path = Path.home() / ".deepagents" / "settings.json"
    if user_config_path.exists():
        with open(user_config_path) as f:
            config_dict.update(json.load(f))

    # 3. 프로젝트 설정
    root = Path(project_root or os.getcwd())
    project_config_path = root / ".deepagents" / "settings.json"
    if project_config_path.exists():
        with open(project_config_path) as f:
            config_dict.update(json.load(f))

    # 4. 환경변수 오버라이드
    env_overrides = _load_env_overrides()
    config_dict.update(env_overrides)

    # 5. CLI 인수 오버라이드
    if cli_overrides:
        config_dict.update(cli_overrides)

    # project_root 설정
    config_dict.setdefault("project_root", str(root))

    return AgentConfig(**config_dict)


def _load_env_overrides() -> dict:
    """환경변수에서 설정 오버라이드 로드

    .env 파일이 있으면 자동 로드 (python-dotenv).
    환경변수 매핑: SDS_AX_MODEL → model, SDS_AX_FALLBACK_MODEL → fallback_model 등.
    """
    from dotenv import load_dotenv
    load_dotenv()  # .env 파일 자동 로드

    overrides = {}

    # 모델 설정
    if v := os.environ.get("SDS_AX_MODEL"):
        overrides["model"] = v
    if v := os.environ.get("SDS_AX_FALLBACK_MODEL"):
        overrides["fallback_model"] = v

    # API 키 (create_deep_agent가 내부적으로 사용하지만, 환경변수로 전달됨)
    # ANTHROPIC_API_KEY, OPENAI_API_KEY 등은 LangChain이 자동으로 읽으므로
    # 여기서 별도 처리 불필요. 단, 존재 여부만 검증.

    # 샌드박스
    if v := os.environ.get("SDS_AX_SANDBOX_MODE"):
        overrides.setdefault("sandbox", {})["mode"] = v

    return overrides


def ensure_api_keys():
    """필수 API 키 존재 여부 검증. 없으면 에러 메시지와 함께 종료."""
    required = []

    # LLM API 키 — LangChain 프로바이더가 자동 참조
    # Anthropic: ANTHROPIC_API_KEY
    # OpenAI/OpenRouter: OPENAI_API_KEY
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        required.append("ANTHROPIC_API_KEY or OPENAI_API_KEY")

    if required:
        print(f"Error: Missing required environment variables: {', '.join(required)}")
        print("Set them in .env file or export them in your shell.")
        raise SystemExit(1)
```

> **`.env.example` 템플릿** — 프로젝트 루트에 포함:
> ```
> # .env.example
> ANTHROPIC_API_KEY=sk-ant-...
> # OPENAI_API_KEY=sk-...          # Alternative: OpenAI/OpenRouter
> # TAVILY_API_KEY=tvly-...        # Optional: for web_search tool
> # SDS_AX_MODEL=claude-sonnet-4-5-20250929
> # SDS_AX_SANDBOX_MODE=none
> ```

---

## 12. 의존성

```toml
[project]
name = "sds-ax"
requires-python = ">=3.11"

[project.dependencies]
# 핵심 프레임워크
deepagents = ">=0.1"             # create_deep_agent(), 미들웨어, 내장 도구
langgraph = ">=0.4"
langchain = ">=0.3"
langchain-core = ">=0.3"
langchain-anthropic = ">=0.3"    # Claude 모델

# 저장소 (개발: 인메모리, 프로덕션: PostgreSQL)
# langgraph-checkpoint-postgres = ">=2.0"
# langgraph-store-postgres = ">=0.1"

# TUI
textual = ">=3.0"               # 터미널 UI

# 커스텀 도구 의존성
tavily-python = ">=0.5"          # web_search
httpx = ">=0.27"                 # fetch_url

# MCP 통합
langchain-mcp-adapters = ">=0.1"

# 유틸리티
pydantic = ">=2.0"
python-dotenv = ">=1.0"
rich = ">=13.0"                  # 터미널 출력 포맷팅

[project.scripts]
sds-ax = "sds_ax.cli:main"
```

---

## 13. 구현 로드맵

### Phase 1: 기초 골격 (MVP)
- [ ] 프로젝트 구조 생성 (pyproject.toml, 패키지 구조)
- [ ] AgentConfig 설정 스키마 (config/schema.py)
- [ ] `create_sds_ax_agent()` 구현 (core/agent.py) — `create_deep_agent()` 래핑
- [ ] 커스텀 도구 5종: git, bash, web_search, fetch_url, ask_user
- [ ] Git 안전 규칙 엔진 (서브커맨드별 분류, force-push 차단, 민감 파일 감지)
- [ ] CLI 진입점 (cli.py) — 비대화형 모드 먼저
- [ ] 기본 HITL 흐름 (interrupt_on + Command(resume=...))

### Phase 2: 루프 강화
- [ ] bash 샌드박스 (none/restricted/container 모드)
- [ ] Stall Detector (넛지 → 모델 전환 → ask_user → 중단)
- [ ] Context Compaction (Auto 70% / Reactive 85% / Emergency 95%)
- [ ] 슬래시 커맨드 기본 셋 (/help, /clear, /compact, /model)

### Phase 3: 메모리 + 서브에이전트
- [ ] CompositeBackend 구성 (StateBackend + StoreBackend + FilesystemBackend)
- [ ] Auto-Dream 메모리 추출 (경량 LLM 포크)
- [ ] 서브에이전트 타입 5종 선언 (explorer, coder, researcher, reviewer, planner)
- [ ] 서브에이전트 HITL 전파 검증

### Phase 4: TUI
- [ ] Textual App 메인 (ui/app.py)
- [ ] 메시지 렌더링, 스트리밍
- [ ] 입력 프롬프트 (슬래시 커맨드 자동완성)
- [ ] 서브에이전트/태스크 패널
- [ ] 상태 바 (모델, 세션, 턴 수)

### Phase 5: 확장
- [ ] MCP 도구 통합 + trust_level 권한 모델
- [ ] 스킬 시스템 (./skills/ 디렉토리, SKILL.md 포맷)
- [ ] 세션 관리 (--resume, --list, 복원 시 서브에이전트 처리)
- [ ] LangSmith 트레이싱 통합
- [ ] PostgresSaver / PostgresStore 프로덕션 백엔드
