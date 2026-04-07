# ATOM-CODE: Advanced CLI Agent

> **Project**: Atom Code  
> **Stack**: Python 3.11+ / LangGraph / DeepAgents Framework  
> **Goal**: Claude Code, Codex 수준의 고도화된 CLI 전용 자율 에이전트  
> **Core API**: `create_deep_agent()` — DeepAgents 프레임워크의 단일 진입점

---

## 1. 에이전트 정체성

Atom Code는 **DeepAgents 프레임워크 위에 구축된** 터미널 자율 소프트웨어 엔지니어 에이전트다.
`create_deep_agent()`가 제공하는 미들웨어, 도구, 서브에이전트, HITL, 스킬 시스템을 그대로 활용하고,
CLI/TUI, Git 안전 규칙, 샌드박스, Stall Detection 등 **CLI 코딩 에이전트에 특화된 레이어**만 추가한다.

### 핵심 원칙

| 원칙 | 설명 |
|------|------|
| **Framework-First** | DeepAgents가 제공하는 것은 재구현하지 않고 그대로 사용 |
| **안전성 우선** | 파괴적 작업은 반드시 사용자 승인 후 실행 |
| **루프 불멸** | Agentic Loop이 어떤 상황에서도 깨지지 않는 방어적 설계 |
| **점진적 개인화** | 사용할수록 사용자와 프로젝트에 맞춤화되는 장기 메모리 |
| **동적 위임** | 서브에이전트를 필요 시 생성하고, 작업 후 소멸시키는 동적 오케스트레이션 |

### DeepAgents가 제공하는 것 vs ATOM-CODE가 추가하는 것

```
┌─────────────────────────────────────────────────────────────┐
│  DeepAgents Framework (create_deep_agent)                    │
│  ├── Built-in Tools: write_todos, ls, read_file, write_file,│
│  │    edit_file, glob, grep, task                            │
│  ├── Auto Middleware: TodoList, Filesystem, SubAgent,        │
│  │    HumanInTheLoop, Skills, Memory                         │
│  ├── Backend System: State, Store, Filesystem, Composite     │
│  ├── Declarative Subagents                                   │
│  ├── HITL (interrupt_on + Command(resume))                   │
│  ├── Skills (SKILL.md format, on-demand loading)             │
│  └── Store (InMemoryStore, PostgresStore)                    │
├─────────────────────────────────────────────────────────────┤
│  ATOM-CODE Extensions (on top of DeepAgents)                    │
│  ├── Custom Tools: git, bash, web_search, fetch_url, ask_user│
│  ├── CLI/TUI: Textual-based terminal UI                      │
│  ├── Session Management: resume, list, export                │
│  ├── Slash Commands: /help, /clear, /compact, etc.           │
│  ├── Git Safety Rules: force-push prevention, etc.           │
│  ├── Bash Sandbox: restricted / container modes              │
│  ├── Stall Detection: nudge, model switch, ask user          │
│  ├── Context Compaction: auto / reactive / emergency         │
│  ├── Auto-Dream Memory: automatic extraction layer           │
│  ├── MCP Tool Integration: trust_level permission model      │
│  ├── edit_file Conflict Defense: file locking between subs   │
│  └── Session Restore: interrupted subagent handling          │
└─────────────────────────────────────────────────────────────┘
```

### 에이전트 생성 (실제 API 호출)

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend, FilesystemBackend
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()  # dev (prod: PostgresStore)

agent = create_deep_agent(
    name="sds-ax",
    model="anthropic/claude-sonnet-4-5-20250929",
    system_prompt=SYSTEM_PROMPT,

    # DeepAgents built-in tools are auto-included.
    # Only specify ATOM-CODE custom tools here.
    tools=[git_tool, bash_tool, web_search_tool, fetch_url_tool, ask_user_tool],

    # Declarative subagent definitions
    subagents=[
        {"name": "explorer",   "description": "Codebase exploration",
         "tools": ["read_file", "grep", "glob", "ls"], "system_prompt": EXPLORER_PROMPT},
        {"name": "coder",      "description": "Code writing and modification",
         "tools": ["read_file", "write_file", "edit_file", "bash", "git"], "system_prompt": CODER_PROMPT},
        {"name": "researcher", "description": "Web research",
         "tools": ["web_search", "fetch_url", "read_file"], "system_prompt": RESEARCHER_PROMPT},
        {"name": "reviewer",   "description": "Code review (read-only, no test execution)",
         "tools": ["read_file", "grep", "glob"], "system_prompt": REVIEWER_PROMPT},
        {"name": "planner",    "description": "Plan formulation",
         "tools": ["read_file", "grep", "glob", "write_todos"], "system_prompt": PLANNER_PROMPT},
    ],

    # HITL: destructive tools require user approval
    # Note: git is NOT in interrupt_on because the git tool has its own
    # internal safety rules that selectively interrupt only for dangerous
    # subcommands (push --force, reset --hard, etc.). Adding git here
    # would cause double interrupts.
    interrupt_on={"write_file": True, "edit_file": True, "bash": True},

    # Skills directories
    skills=["./skills/", "~/.deepagents/skills/"],

    # Backend: factory function
    backend=lambda rt: CompositeBackend(
        StateBackend(rt),                          # ephemeral session state
        {"/memories/": StoreBackend(rt)}           # persistent cross-thread memory
    ),

    checkpointer=checkpointer,  # SQLite/PostgreSQL
    store=store,
)
```

> **Note**: `create_deep_agent()`는 TodoListMiddleware, FilesystemMiddleware, SubAgentMiddleware, HumanInTheLoopMiddleware, SkillsMiddleware, MemoryMiddleware를 **자동으로 포함**한다. 수동으로 미들웨어를 쌓을 필요가 없다.

---

## 2. 실행 모드

```
sds-ax
  │
  ├── 대화형 모드 (기본)
  │   └── Textual TUI → 스트리밍 응답, 도구 실행, 서브에이전트 모니터링
  │
  ├── 비대화형 모드 (-n "task")
  │   └── 단일 작업 실행 → 결과 출력 → 종료
  │
  ├── 서버 모드 (--serve)
  │   └── LangGraph API 서버 → 프로그래밍 방식 접근
  │
  └── 코디네이터 모드 (--coordinator)
      └── 리더 에이전트가 워커 서브에이전트를 오케스트레이션
```

### Backend 선택 (모드별)

| 모드 | Backend | 설명 |
|------|---------|------|
| 대화형 (TUI) | `CompositeBackend(StateBackend, {"/memories/": StoreBackend})` | 세션은 ephemeral, 메모리는 persistent |
| 비대화형 | `StateBackend(rt)` | 단일 실행, 상태 불필요 |
| 서버 | `CompositeBackend(StateBackend, {"/memories/": StoreBackend})` | 멀티 스레드 지원 |
| 코디네이터 | `FilesystemBackend(root_dir=".", virtual_mode=False)` | 실제 디스크 I/O |

---

## 3. 도구(Tool) 시스템

### 3.1 DeepAgents 프레임워크 내장 도구 (자동 제공)

`create_deep_agent()`가 자동으로 포함하는 도구. ATOM-CODE에서 별도 등록 불필요.

| 도구 | 타입 | 설명 | 병렬 안전 |
|------|------|------|-----------|
| **read_file** | 읽기 전용 | 파일 읽기 (이미지/PDF 포함) | O |
| **write_file** | 파괴적 | 새 파일 작성 | X |
| **edit_file** | 파괴적 | 파일 인라인 편집 (문자열 치환) | X |
| **grep** | 읽기 전용 | ripgrep 기반 패턴 검색 | O |
| **glob** | 읽기 전용 | 파일 패턴 매칭 | O |
| **ls** | 읽기 전용 | 디렉토리 목록 | O |
| **write_todos** | 상태 변경 | 태스크 계획 관리 | X |
| **task** | 오케스트레이션 | 서브에이전트 생성/위임 (자동 제공) | X |

### 3.2 ATOM-CODE 커스텀 도구

ATOM-CODE가 `tools=[]` 파라미터로 추가하는 CLI 전용 도구.

| 도구 | 타입 | 설명 | 병렬 안전 |
|------|------|------|-----------|
| **git** | 혼합 | Git 연산 (안전 규칙 내장, 아래 4 참조) | X |
| **bash** | 파괴적 | 셸 명령 실행, 샌드박스 지원 (아래 6 참조) | X |
| **web_search** | 읽기 전용 | Tavily 웹 검색 | O |
| **fetch_url** | 읽기 전용 | URL 콘텐츠 가져오기 | O |
| **ask_user** | UI | 사용자에게 질문 (interrupt) | X |

### 3.3 도구 실행 파이프라인

```
LLM이 tool_use 반환
    │
    ▼
[1] 도구 조회 (이름 → 구현체 매핑)
[2] 입력 검증 (Pydantic 스키마)
[3] PreToolUse 훅 실행 (차단/수정 가능)
[4] 권한 확인
    ├── allow_list 규칙 매칭 → 허용
    ├── deny_list 규칙 매칭 → 거부
    ├── interrupt_on 설정 매칭 → 사용자 승인 요청 (HITL)
    └── 기본값 → auto_approve 또는 interrupt
[5] 도구 실행 (타임아웃 적용)
[6] 결과 매핑 (ToolMessage 형식)
[7] 대형 결과 처리 (임계값 초과 시 파일로 영속화)
[8] PostToolUse 훅 실행
```

---

## 4. Git 도구 및 안전 규칙

Git은 코딩 에이전트의 핵심 워크플로이므로 ATOM-CODE 전용 도구로 제공한다.
bash를 통한 직접 git 명령도 가능하지만, 전용 도구를 통하면 안전 규칙이 자동 적용된다.

### 서브커맨드 분류

| 서브커맨드 | 타입 | 설명 |
|-----------|------|------|
| `git status` | 읽기 전용 | 워킹 트리 상태 확인 |
| `git diff` | 읽기 전용 | 변경 사항 확인 (--staged 포함) |
| `git log` | 읽기 전용 | 커밋 이력 조회 |
| `git blame` | 읽기 전용 | 라인별 변경 이력 |
| `git show` | 읽기 전용 | 커밋/오브젝트 내용 확인 |
| `git branch` | 읽기 전용 (목록) / 파괴적 (생성/삭제) | 브랜치 관리 |
| `git add` | 상태 변경 | 스테이징 영역 변경 |
| `git commit` | 파괴적 | 커밋 생성 |
| `git checkout` / `git switch` | 파괴적 | 브랜치/파일 전환 |
| `git push` | 외부 영향 | 리모트 푸시 (**항상 사용자 승인 필요**) |
| `git merge` | 파괴적 | 브랜치 병합 |
| `git rebase` | 파괴적 | 리베이스 (**항상 사용자 승인 필요**) |
| `git stash` | 파괴적 | 변경 사항 임시 저장 |

### Git 안전 규칙 (에이전트 필수 준수)

```
[금지 규칙]
├── git push --force / --force-with-lease → 절대 자동 실행 금지, 사용자 승인 필수
├── git reset --hard → 사용자 명시적 요청 시에만 실행
├── git clean -f → 사용자 명시적 요청 시에만 실행
├── git branch -D → 사용자 명시적 요청 시에만 실행
├── git rebase (대화형 -i 포함) → 항상 사용자 승인 필요
├── git config 변경 → 금지
└── --no-verify, --no-gpg-sign → 사용자 명시적 요청 없이 사용 금지

[권장 규칙]
├── 커밋 시 새 커밋 생성 (amend보다 우선)
├── git add 시 파일명 명시 (git add -A / . 지양)
├── .env, credentials.json 등 민감 파일 스테이징 금지 (경고 후 사용자 확인)
├── 커밋 메시지는 변경 사항의 "why"에 집중
├── 머지 충돌 발생 시 자동 해결보다 사용자에게 보고 우선
└── 리모트 푸시 전 반드시 현재 브랜치/리모트 상태 확인
```

---

## 5. edit_file 충돌 방어

edit_file은 `old_string → new_string` 문자열 치환 방식이며, 다음 충돌 시나리오를 방어한다.

```
[시나리오 1: 연속 편집 시 old_string 불일치]
├── 원인: 이전 편집으로 파일 내용이 바뀌어 old_string이 더 이상 매칭되지 않음
├── 감지: 도구 실행 시 old_string 매칭 실패 → ToolMessage로 에러 반환
├── LLM 복구: 에러를 받은 LLM이 현재 파일 내용을 다시 read_file 후 재시도
└── 방어: edit_file은 SEQUENTIAL_ONLY이므로 동일 턴 내 순차 실행 보장

[시나리오 2: old_string이 파일 내 여러 곳에 매칭]
├── 감지: 매칭 결과가 2개 이상 → 에러 반환 ("non-unique match")
├── LLM 복구: 더 넓은 컨텍스트(전후 라인 포함)로 old_string을 재구성
└── 옵션: replace_all=true 플래그로 전체 치환 의도를 명시할 수 있음

[시나리오 3: 서브에이전트 간 동일 파일 편집 충돌]
├── 원칙: 서브에이전트 간 동일 파일 동시 편집은 금지
├── 구현: 인메모리 파일 잠금 관리
│   └── 서브에이전트가 edit_file/write_file 호출 시 대상 파일 경로를 잠금
│   └── 다른 서브에이전트가 같은 파일 접근 시 대기 또는 거부
└── 메인 에이전트: 항상 최우선 접근 (서브에이전트 잠금 무시)
```

---

## 6. bash 샌드박스

bash 도구는 ATOM-CODE 커스텀 도구이며 선택적 샌드박스 모드를 지원한다.

```
샌드박스 수준:
    │
    ├── none (기본)
    │   └── 제한 없는 셸 실행, 권한 시스템으로만 통제
    │
    ├── restricted
    │   ├── 파일시스템: project_root 하위로 읽기/쓰기 제한
    │   ├── 네트워크: 허용된 호스트만 접근 (npm registry, pypi 등)
    │   ├── 프로세스: fork bomb 방지 (ulimit -u)
    │   ├── 시간: 명령별 타임아웃 강제 (기본 120초)
    │   └── 구현: Linux namespace (unshare) + seccomp 기반
    │
    └── container (최고 보안)
        ├── Docker/Podman 컨테이너 내 실행
        ├── 프로젝트 디렉토리만 bind mount (읽기/쓰기)
        ├── 네트워크: 필요 시에만 --network host 허용
        ├── 리소스 제한: --memory, --cpus
        └── auto_approve 모드에서도 container 샌드박스 강제 가능 (설정)

설정:
{
  "sandbox": {
    "mode": "none",
    "allowed_hosts": ["registry.npmjs.org", "pypi.org"],
    "force_on_auto_approve": false,
    "container_image": "sds-ax-sandbox:latest"
  }
}
```

---

## 7. 동시성 파티셔닝

```python
# 안전한 도구: 병렬 실행
CONCURRENT_SAFE = {"read_file", "grep", "glob", "ls", "web_search", "fetch_url"}

# 위험한 도구: 순차 실행
# - write_file, edit_file: 파일시스템 변경 → 순서 보장 필수
# - bash: 부작용 예측 불가 → 순차
# - git: 워킹 트리 상태에 의존 → 순차
# - task: 서브에이전트 생성은 리소스 관리상 순차
# - write_todos: 인메모리 상태 변경, 순서 의존성 존재
SEQUENTIAL_ONLY = {"write_file", "edit_file", "bash", "git", "task", "write_todos"}

# 실행 예시:
# [read_file, grep, glob]  → Batch 1: 병렬
# [edit_file]               → Batch 2: 순차
# [read_file, read_file]    → Batch 3: 병렬
# [git status]              → Batch 4: 순차
# [bash]                    → Batch 5: 순차
```

---

## 8. MCP 도구 확장 + 권한 모델

### 도구 발견 순서

```
[1] DeepAgents 내장 도구 + ATOM-CODE 커스텀 도구 (항상 우선)
[2] ~/.deepagents/.mcp.json (사용자 전역)
[3] .deepagents/.mcp.json (프로젝트)
[4] .mcp.json (프로젝트 루트)

이름 충돌 시 내장 도구가 우선한다.
```

### MCP 도구 권한 모델

MCP 외부 도구는 내장 도구와 달리 안전성을 자동 판별할 수 없다.
다음 규칙으로 권한을 결정한다.

```
MCP 도구 호출 시 권한 판정:
    │
    ▼
[1] MCP 서버 설정에 trust_level 명시됨?
    ├── "trusted" → 서버의 도구 메타데이터에서 is_read_only 참조
    ├── "untrusted" (기본값) → 모든 도구를 파괴적으로 간주
    └── "ask" → 매 호출마다 사용자에게 승인 요청
    │
[2] 개별 도구 오버라이드 (settings.json)
    ├── "allow": ["mcp_server_name.tool_name(*)"]  → 자동 허용
    └── "deny": ["mcp_server_name.tool_name(*)"]   → 자동 거부
    │
[3] 동시성 분류
    └── MCP 도구는 기본적으로 SEQUENTIAL_ONLY (부작용 예측 불가)
    └── 설정에서 concurrent_safe로 명시적 지정 가능
```

```json
// .deepagents/.mcp.json
{
  "servers": {
    "my-db-server": {
      "command": "npx",
      "args": ["@my/mcp-db"],
      "trust_level": "untrusted",
      "concurrent_safe_tools": ["query"],
      "tool_overrides": {
        "query": { "is_read_only": true },
        "migrate": { "is_read_only": false }
      }
    }
  }
}
```

---

## 9. 동적 서브에이전트 시스템

### 9.1 설계 철학

서브에이전트는 `create_deep_agent()`의 `subagents` 파라미터로 **선언적으로 정의**한다.
`task` 도구는 프레임워크가 자동 제공하며, LLM이 `task(agent="explorer", instruction="...")`로 호출하면
SubAgentMiddleware가 해당 선언을 기반으로 에이전트를 동적 생성한다.

- 서브에이전트는 **stateless/ephemeral** — 작업 완료 후 소멸
- 서브에이전트는 메인 에이전트의 **skills를 상속하지 않음**
- 서브에이전트는 격리된 메시지 컨텍스트를 가짐

### 9.2 선언적 서브에이전트 정의

```python
# create_deep_agent() 호출 시 subagents 파라미터
subagents=[
    {
        "name": "explorer",
        "description": "Codebase exploration and structure analysis",
        "tools": ["read_file", "grep", "glob", "ls"],
        "system_prompt": "You are a codebase explorer. Find and analyze code structure."
    },
    {
        "name": "coder",
        "description": "Code writing and modification",
        "tools": ["read_file", "write_file", "edit_file", "bash", "git"],
        "system_prompt": "You are a code writer. Implement changes as instructed."
    },
    {
        "name": "researcher",
        "description": "Web research and information gathering",
        "tools": ["web_search", "fetch_url", "read_file"],
        "system_prompt": "You are a researcher. Find relevant information online."
    },
    {
        "name": "reviewer",
        "description": "Code review (read-only, no test execution)",
        "tools": ["read_file", "grep", "glob"],
        "system_prompt": "You are a code reviewer. Analyze code for issues."
    },
    {
        "name": "planner",
        "description": "Plan formulation and task breakdown",
        "tools": ["read_file", "grep", "glob", "write_todos"],
        "system_prompt": "You are a planner. Break down complex tasks."
    },
]
```

### 9.3 생명주기

```
┌─────────────────────────────────────────────────────────┐
│                서브에이전트 생명주기                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [생성] LLM → task(agent="researcher", instruction="...")│
│    │                                                    │
│    ├── SubAgentMiddleware가 선언에서 매칭               │
│    ├── 격리된 메시지 컨텍스트 생성                       │
│    ├── 전용 도구 세트 할당                                │
│    └── max_turns: 100, timeout: 600s                    │
│    │                                                    │
│  [작업] 자율 실행                                        │
│    │                                                    │
│    ├── 독립된 Agentic Loop 내에서 도구 호출               │
│    ├── 메인 에이전트 컨텍스트와 격리                      │
│    ├── 진행 상황은 메인에 스트리밍                        │
│    └── 타임아웃/최대 턴 제한 적용                         │
│    │                                                    │
│  [보고] 결과 반환                                        │
│    │                                                    │
│    ├── 최종 결과를 메인 에이전트에 ToolMessage로 반환     │
│    └── 컨텍스트 정리 (메모리 해제)                        │
│    │                                                    │
│  [소멸] Stateless — GC에 의해 자동 정리                  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 9.4 서브에이전트 HITL 전파

서브에이전트가 사용자 승인이 필요한 상황(interrupt_on 매칭)에 처했을 때,
interrupt를 메인 에이전트를 거쳐 사용자에게 전파한다.

```
서브에이전트 내부에서 interrupt 발생 (interrupt_on 매칭)
    │
    ▼
[1] 서브에이전트 실행 중단, interrupt 정보 저장
    {
      "type": "permission_request",
      "tool": "bash",
      "input": "npm run deploy",
      "context": "Subagent 'coder' requests permission to run: bash(npm run deploy)"
    }
    │
    ▼
[2] 메인 에이전트 레벨에서 사용자에게 전파
    "[Subagent: coder] bash(npm run deploy) — approve? (y/n)"
    │
    ▼
[3] 사용자 응답 → Command(resume={"decisions": [...]})
    ├── approve → Command(resume={"decisions": [{"type": "approve"}]})
    ├── reject  → Command(resume={"decisions": [{"type": "reject"}]})
    └── edit    → Command(resume={"decisions": [{"type": "edit", "edited_action": {"name": "tool_name", "args": {...}}}]})
```

---

## 10. HITL (Human-in-the-Loop)

### DeepAgents HITL API

ATOM-CODE는 DeepAgents의 `interrupt_on` + `Command(resume)` 메커니즘을 그대로 사용한다.

```python
# 에이전트 생성 시 interrupt 대상 정의
agent = create_deep_agent(
    ...
    # Note: git is NOT in interrupt_on because the git tool has its own
    # internal safety rules that selectively interrupt only for dangerous
    # subcommands (push --force, reset --hard, etc.). Adding git here
    # would cause double interrupts.
    interrupt_on={
        "write_file": True,   # 파일 쓰기 시 항상 사용자 승인
        "edit_file": True,    # 파일 편집 시 항상 사용자 승인
        "bash": True,         # 셸 실행 시 항상 사용자 승인
    },
)
```

### 승인/거부/편집 흐름

```
LLM이 interrupt_on 대상 도구 호출
    │
    ▼
[1] HumanInTheLoopMiddleware가 실행을 중단 (interrupt)
[2] TUI가 사용자에게 표시:
    "bash(npm install express) — approve / edit / reject?"
    │
    ▼
[3] 사용자 결정 → Command(resume) 전송
    │
    ├── approve:
    │   Command(resume={"decisions": [{"type": "approve"}]})
    │   → 도구 원래 입력 그대로 실행
    │
    ├── edit:
    │   Command(resume={"decisions": [{"type": "edit", "edited_action": {"name": "tool_name", "args": {...}}}]})
    │   → 수정된 입력으로 도구 실행
    │
    └── reject:
        Command(resume={"decisions": [{"type": "reject"}]})
        → 도구 실행 거부, LLM에게 거부 사실 전달 (LLM이 대안 모색)
```

### 권한 모드 (ATOM-CODE 확장)

| 모드 | 동작 | 사용 사례 |
|------|------|----------|
| **default** | interrupt_on 대상 도구는 사용자에게 묻기 | 일반 사용 |
| **auto_approve** | 모든 도구 자동 허용 (interrupt_on 무시) | 신뢰 환경, CI/CD |
| **read_only** | 읽기 도구만 허용, 나머지 거부 | 탐색/리뷰 전용 |
| **plan_only** | 읽기 + 계획 도구만 허용 | 계획 수립 단계 |

### 권한 규칙 설정

```json
// .deepagents/settings.json
{
  "permissions": {
    "mode": "default",
    "allow": [
      "bash(npm install *)",
      "bash(npm run *)",
      "bash(git status)",
      "bash(git diff *)",
      "edit_file(src/**/*.py)",
      "write_file(tests/**/*.py)"
    ],
    "deny": [
      "bash(rm -rf *)",
      "bash(git push --force *)",
      "write_file(.env*)",
      "edit_file(*.lock)"
    ]
  }
}
```

```
권한 판정 파이프라인:
    │
    ▼
[1] deny 규칙 매칭 → 즉시 거부 (도구 실행 차단, interrupt_on 무시)
[2] allow 규칙 매칭 → 즉시 허용 (interrupt_on 바이패스)
[3] 도구의 is_read_only → True면 허용
[4] interrupt_on 매칭 → True면 사용자에게 승인 요청 (HITL)
[5] 모드별 판정
    ├── auto_approve → 허용
    ├── read_only → 거부
    └── default → 허용 (interrupt_on에 없는 도구는 자동 허용)
```

---

### 10.5 훅(Hook) 시스템

DeepAgents 미들웨어 훅 포인트를 통해 ATOM-CODE 커스텀 레이어를 에이전트 실행 루프에 삽입한다.

| 훅 | 시점 | ATOM-CODE 활용                           |
|------|------|----------------------------------------|
| `before_model` | LLM 호출 전 | Context Compaction 트리거 (70/85/95% 임계값) |
| `after_model` | LLM 응답 후 | Stall Detection 체크 (빈 턴 감지 → 복구)       |
| `wrap_tool_call` | 도구 실행 전/후 래핑 | allow/deny 규칙 적용, 파일 잠금 확인             |
| `before_agent` | 에이전트 턴 시작 | 메모리 검색/주입                              |
| `after_agent` | 에이전트 턴 완료 | Auto-Dream 메모리 추출 트리거, 파일 잠금 정리        |

```python
# create_sds_ax_agent() 내부에서 미들웨어로 통합
agent = create_deep_agent(
    ...
    middleware=[custom_middleware],  # ATOM-CODE 커스텀 레이어
)
```

이 훅 시스템이 Stall Detection, Context Compaction, Auto-Dream, 파일 잠금, allow/deny 규칙의 **통합 지점**이다.

---

## 11. 장기 메모리

### 11.1 메모리 아키텍처 (CompositeBackend + Store)

DeepAgents의 Backend 시스템과 Store를 조합하고, ATOM-CODE가 Auto-Dream 추출 레이어를 추가한다.

```
┌─────────────────────────────────────────────────────────┐
│                    메모리 아키텍처                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Tier 1: 세션 메모리 (Ephemeral)                        │
│  ├── StateBackend(rt) — CompositeBackend의 기본 경로    │
│  ├── 현재 대화 컨텍스트                                  │
│  ├── 활성 서브에이전트 상태                               │
│  └── 세션 종료 시 소멸                                   │
│                                                         │
│  Tier 2: 스레드 메모리 (Persistent per thread)           │
│  ├── Checkpointer (SQLite/PostgreSQL)                   │
│  ├── 대화 이력 및 체크포인트                              │
│  ├── 세션 간 대화 복원 가능                              │
│  └── thread_id 기반 접근                                 │
│                                                         │
│  Tier 3: 장기 메모리 (Cross-session)                    │
│  ├── StoreBackend(rt) — CompositeBackend의 /memories/   │
│  ├── Store: InMemoryStore() (dev) / PostgresStore (prod)│
│  ├── 개발자 개인화 메모리                                │
│  ├── 도메인 지식 저장소                                  │
│  ├── 프로젝트 맞춤 메모리                                │
│  └── 모든 세션에서 접근 가능                             │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 11.2 Backend 구성 (실제 코드)

```python
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.store.memory import InMemoryStore, PostgresStore

# Development
store = InMemoryStore()

# Production
# store = PostgresStore(connection_string="postgresql://...")

agent = create_deep_agent(
    ...
    backend=lambda rt: CompositeBackend(
        StateBackend(rt),                    # default: ephemeral
        {"/memories/": StoreBackend(rt)}     # /memories/ prefix: persistent
    ),
    store=store,
)
```

### 11.3 장기 메모리 스키마 (Tier 3)

#### 메모리 타입

| 타입 | namespace | 설명 | 자동 추출 |
|------|-----------|------|-----------|
| **user** | `("memory", "user")` | 개발자 역할, 선호, 전문성 | O |
| **feedback** | `("memory", "feedback")` | 작업 방식 교정/확인 | O |
| **domain** | `("memory", "domain")` | 도메인 지식 (비즈니스 로직, 용어) | O |
| **project** | `("memory", "project", "{project_slug}")` | 프로젝트별 맞춤 지식 | O |
| **reference** | `("memory", "reference")` | 외부 시스템 참조 | 수동 |

#### 메모리 파일 구조

```
~/.deepagents/memory/
├── MEMORY_INDEX.md          # 메모리 인덱스 (항상 시스템 프롬프트에 포함)
├── user/
│   ├── role.json            # 사용자 역할/전문성
│   └── preferences.json     # 작업 선호도
├── feedback/
│   ├── coding_style.json
│   └── workflow.json
├── domain/
│   ├── {topic_slug}.json
│   └── ...
├── project/
│   ├── {project_slug}/
│   │   ├── context.json
│   │   ├── architecture.json
│   │   └── conventions.json
│   └── ...
└── reference/
    └── external.json
```

### 11.4 자동 메모리 추출 (Auto-Dream) — ATOM-CODE 확장

DeepAgents의 MemoryMiddleware 위에 ATOM-CODE가 추가하는 자동 추출 레이어.

```
세션 진행 중
    │
    ▼
[임계값 확인]
├── 최소 메시지 토큰: 5,000
├── 최소 도구 호출 간격: 3회
└── 최소 토큰 간격: 3,000
    │
    ▼ (임계값 초과 시)
[포크된 서브에이전트에서 추출] (비차단)
    │
    ├── 사용자 선호/피드백 감지
    │   "Don't do it this way" → feedback 메모리 저장
    │   "I'm a senior developer" → user 메모리 저장
    │
    ├── 도메인 지식 감지
    │   "In our system, orders go through 3 stages..." → domain 메모리 저장
    │   "This API must be idempotent" → domain 메모리 저장
    │
    ├── 프로젝트 컨텍스트 감지
    │   "This project uses MSA..." → project 메모리 저장
    │   "The deploy pipeline is frozen" → project 메모리 저장
    │
    └── 기존 메모리와 병합/갱신
        ├── 중복 감지 (semantic similarity)
        ├── 충돌 시 최신 정보 우선
        └── MEMORY_INDEX.md 갱신
```

### 11.5 메모리 활용 시점

```
매 쿼리 시작 시
    │
    ▼
[1] MEMORY_INDEX.md 로드 (시스템 프롬프트에 포함)
[2] 현재 쿼리와 관련된 메모리 검색 (semantic search via Store)
[3] 관련 메모리를 컨텍스트에 주입
[4] 프로젝트 메모리는 현재 cwd 기준으로 자동 필터
```

---

## 12. Agentic Loop 방어 설계

### 12.1 핵심 루프 구조

```
사용자 입력
    │
    ▼
┌────────────────────────────────────────────┐
│  AGENTIC LOOP (max_turns: 200)             │
│                                            │
│  while not terminal:                       │
│    ├── [전처리] 메시지 정규화, 컨텍스트 압축 │
│    ├── [API 호출] LLM 스트리밍              │
│    ├── [에러 복구] 재시도/폴백              │
│    ├── [도구 실행] 도구 파이프라인          │
│    ├── [후처리] 메모리 갱신                 │
│    └── [판단] end_turn → 종료 / tool_use → 계속│
│                                            │
│  return Terminal                           │
└────────────────────────────────────────────┘
```

### 12.2 방어 계층

```
┌─────────────────────────────────────────────────────┐
│ Layer 1: API 호출 방어                               │
│ ├── 지수 백오프 재시도 (최대 3회)                     │
│ ├── 타임아웃 (단일 호출 60초, 전체 턴 300초)         │
│ ├── Rate Limit → 대기 후 재시도                      │
│ ├── prompt_too_long → 리액티브 컴팩션                │
│ ├── max_output_tokens → 자동 continuation            │
│ └── 연결 실패 → 폴백 모델 전환                       │
├─────────────────────────────────────────────────────┤
│ Layer 2: 도구 실행 방어                               │
│ ├── 도구별 타임아웃 (bash: 120초, 기타: 30초)        │
│ ├── 실패 시 에러를 ToolMessage로 반환 (LLM이 복구)   │
│ ├── 무한 반복 감지 (동일 도구+입력 3회 → 중단 경고)  │
│ └── 대형 결과 자동 트렁케이션                        │
├─────────────────────────────────────────────────────┤
│ Layer 3: 루프 수준 방어                               │
│ ├── 최대 턴 제한 (200턴)                             │
│ ├── 무한 루프 감지 (진전 없는 5턴 연속 → 경고)       │
│ ├── 컨텍스트 윈도우 소진 → 자동 컴팩션               │
│ └── 세션 상태 매 턴 영속화 (중단 복원 가능)          │
├─────────────────────────────────────────────────────┤
│ Layer 4: 모델 폴백 체인                               │
│ ├── Primary: 설정된 주 모델                          │
│ ├── Secondary: 폴백 모델                             │
│ └── Emergency: 최소 모델 (연결 유지 목적)            │
├─────────────────────────────────────────────────────┤
│ Layer 5: 프로세스 수준 방어                           │
│ ├── SIGINT (Ctrl+C) → 현재 턴 중단, 상태 보존       │
│ ├── SIGTERM → Graceful Shutdown (체크포인트 저장)     │
│ ├── 예상치 못한 예외 → 로깅 + 세션 복원 포인트 제공   │
│ └── OOM → 컨텍스트 비상 압축 후 속행                  │
└─────────────────────────────────────────────────────┘
```

### 12.3 Stall Detection — ATOM-CODE 커스텀 레이어

DeepAgents 기본 루프 위에 ATOM-CODE가 추가하는 멈춤 감지/복구 레이어.

```
Stall Detection Pipeline:
    │
    ▼
[감지 전략]
├── 타임아웃 기반: API 호출 60초, 스트리밍 청크 간 30초
├── 진전 감지: 도구 호출 없는 연속 3턴, 동일 도구+입력 3회 반복
└── 출력 품질: 빈 응답 반복
    │
    ▼ (stall detected)
[복구 액션 — 순차 에스컬레이션]
├── Step 1: retry_with_nudge
│   "Previous attempt failed. Please try a different approach."
├── Step 2: switch_model
│   폴백 모델로 전환하여 재시도
├── Step 3: compact_and_retry
│   컨텍스트 압축 후 재시도
├── Step 4: ask_user_for_guidance
│   ask_user 도구로 사용자에게 방향 질문
└── Step 5: graceful_stop
    안전하게 멈추고 진행 상황 보고
```

### 12.4 멀티 모델 환경 고려사항

```
모델 전환 시 주의사항:
├── 시스템 프롬프트 호환성 확인
│   └── 모델별 프롬프트 변형 매핑 (예: tool_use 포맷 차이)
├── 도구 스키마 호환성
│   └── 모델별 지원 도구 필터링
├── 컨텍스트 윈도우 차이
│   └── 폴백 모델의 윈도우가 작으면 사전 컴팩션
└── 토큰 카운팅 차이
    └── 모델별 토크나이저 사용
```

---

## 13. 컨텍스트 관리

### 13.1 컨텍스트 윈도우 전략

```
컨텍스트 사용량 모니터링
    │
    ├── < 50% : 정상 운영
    ├── 50-70% : Auto-Compact 준비
    ├── 70-85% : Auto-Compact 실행 (이전 대화 요약)
    ├── 85-95% : Reactive Compact (공격적 압축)
    └── > 95% : Emergency Compact (최소 컨텍스트만 유지)
```

### 13.2 Auto-Compact 동작

```
[1] 오래된 도구 실행 결과를 요약으로 교체
[2] 반복적인 대화를 핵심만 추출
[3] 메모리에 이미 저장된 정보는 참조로 대체
[4] 시스템 프롬프트와 최근 N턴은 보존
```

---

## 14. 스킬 시스템

### DeepAgents 스킬 API

ATOM-CODE는 `create_deep_agent()`의 `skills` 파라미터를 그대로 사용한다.
SkillsMiddleware가 자동으로 스킬 발견, 로딩, 실행을 처리한다.

```python
agent = create_deep_agent(
    ...
    skills=["./skills/", "~/.deepagents/skills/"],
)
```

### 14.1 스킬 발견 순서

```
[1] 내장 스킬    → deepagents_cli/built_in_skills/
[2] 사용자 스킬  → ~/.deepagents/skills/
[3] 프로젝트 스킬 → .deepagents/skills/ 또는 .agents/skills/
```

### 14.2 SKILL.md 형식

```markdown
---
name: my-skill
description: "Specific description (used by agent to decide when to invoke)"
---

# Skill Name

## Overview
Purpose and when to use this skill.

## Instructions
Step-by-step guide for the agent to follow.

## Examples
Good/bad usage examples.
```

### 14.3 스킬 호출 흐름

```
사용자: "/my-skill arg1 arg2" 또는 LLM이 자동 판단
    │
    ▼
SkillsMiddleware → 스킬 발견 → SKILL.md 로드 (on-demand)
    │
    ▼
포크된 서브에이전트 컨텍스트에서 실행
    ├── 격리된 메시지 이력
    ├── SKILL.md 내용이 시스템 프롬프트에 주입
    └── 결과를 메인 에이전트에 반환
```

> **Note**: 커스텀 서브에이전트는 메인 에이전트의 skills를 상속하지 않는다. 스킬이 필요한 작업은 메인 에이전트가 직접 처리해야 한다.

---

## 15. 슬래시 커맨드

ATOM-CODE TUI에서 제공하는 클라이언트 사이드 커맨드.

| 커맨드 | 설명 |
|--------|------|
| `/help` | Display help information |
| `/clear` | Clear conversation history |
| `/compact` | Manual context compaction |
| `/model` | Switch model |
| `/cost` | Show current session cost |
| `/memory` | View/manage memories |
| `/tasks` | List active subagents/tasks |
| `/resume` | Restore previous session |
| `/config` | Change settings |
| `/skills` | List available skills |
| `/plan` | Enter plan mode (read-only) |
| `/diff` | Show changes made in current session |
| `/export` | Export conversation |

---

## 16. 설정 체계

### 16.1 설정 우선순위

```
[1] CLI 인수              (최우선)
[2] 환경 변수
[3] .deepagents/settings.json  (프로젝트)
[4] ~/.deepagents/settings.json  (사용자)
[5] 기본값                (최하위)
```

### 16.2 주요 설정

```json
{
  "model": "anthropic/claude-sonnet-4-5-20250929",
  "fallback_model": "anthropic/claude-haiku-4-5-20251001",
  "api_base": "https://openrouter.ai/api/v1",

  "permissions": {
    "mode": "default",
    "allow": [],
    "deny": []
  },

  "memory": {
    "auto_extract": true,
    "extraction_threshold_tokens": 5000,
    "max_memory_entries": 500
  },

  "loop": {
    "max_turns": 200,
    "tool_timeout_seconds": 120,
    "api_timeout_seconds": 60,
    "stall_detection": true
  },

  "subagents": {
    "max_concurrent": 5,
    "default_max_turns": 100,
    "default_timeout_seconds": 600,
    "hitl_propagation": true
  },

  "context": {
    "auto_compact_threshold": 0.7,
    "reactive_compact_threshold": 0.85,
    "emergency_compact_threshold": 0.95
  },

  "sandbox": {
    "mode": "none",
    "allowed_hosts": [],
    "force_on_auto_approve": false,
    "container_image": "sds-ax-sandbox:latest"
  }
}
```

---

## 17. 세션 복원

세션 복원(/resume) 시 서브에이전트 상태 처리 — ATOM-CODE가 관리하는 복원 로직.

```
세션 복원 시
    │
    ▼
Checkpointer에서 AgentState 로드
    │
    ├── 완료된 서브에이전트 (completed / failed / aborted / timed_out)
    │   └── 그대로 유지 (이력 보존, 결과 참조 가능)
    │
    ├── 실행 중이던 서브에이전트 (running)
    │   ├── 서브에이전트는 stateless이므로 런타임 상태 복원 불가
    │   ├── 상태를 "interrupted"로 변경
    │   ├── 사용자에게 알림:
    │   │   "[Restore] Subagent 'coder' was running in previous session.
    │   │    instruction: 'Refactor auth module'
    │   │    Re-run? (y/n)"
    │   ├── y → 동일 instruction + 동일 선언으로 새 서브에이전트 spawn (via task)
    │   └── n → 상태를 "aborted"로 변경, 계속 진행
    │
    ├── 승인 대기 중이던 서브에이전트 (waiting_approval)
    │   ├── interrupt 정보 복원
    │   ├── 사용자에게 다시 표시:
    │   │   "[Restore] Subagent 'coder' was waiting for approval: ..."
    │   └── 사용자 응답에 따라 Command(resume) 전송
    │
    └── 파일 잠금 모두 해제 (이전 세션의 잠금은 무효)
```

---

## 18. 핵심 설계 패턴 (구현 시 준수)

### 패턴 1: Framework-First
DeepAgents `create_deep_agent()`가 제공하는 미들웨어, 도구, 서브에이전트 시스템을 그대로 사용한다.
수동 StateGraph 구성, 미들웨어 스택 조립, SubAgentManager 구현을 하지 않는다.

### 패턴 2: Declarative Subagents
서브에이전트는 `subagents=[...]` 선언으로 정의하고, `task` 도구(자동 제공)로 호출한다.
런타임에 명시적으로 에이전트를 생성하는 코드를 작성하지 않는다.

### 패턴 3: CompositeBackend Routing
`CompositeBackend(StateBackend, {"/memories/": StoreBackend})`로 경로 기반 자동 라우팅.
ephemeral/persistent 스토리지를 하나의 Backend 인터페이스로 통합한다.

### 패턴 4: HITL via interrupt_on + Command(resume)
`interrupt_on={"write_file": True, ...}`로 선언적으로 HITL 대상을 정의하고,
사용자 응답은 `Command(resume={"decisions": [{"type": "approve"}]})`로 전달한다.

### 패턴 5: AsyncGenerator Streaming
모든 쿼리 루프는 AsyncGenerator로 구현하여 메모리 효율적 스트리밍을 보장한다.

### 패턴 6: Withhold & Recover
복구 가능한 에러는 사용자에게 즉시 노출하지 않고 자동 복구를 시도한다.

### 패턴 7: Immutable State
LangGraph State는 reducer를 통해서만 갱신하며, 직접 변이를 금지한다.

### 패턴 8: Concurrent Partitioning
안전한 도구는 병렬로, 위험한 도구는 순차로 실행하여 처리량과 안전성을 동시에 확보한다.

### 패턴 9: HITL Propagation
서브에이전트의 interrupt를 메인 에이전트를 거쳐 사용자에게 전파하여, 서브에이전트도 사용자 승인을 받을 수 있게 한다.

### 패턴 10: File Locking
서브에이전트 간 동일 파일 동시 편집을 인메모리 잠금으로 방지한다.

### 패턴 11: Interruption Resilience
매 턴 시작 전 체크포인트를 저장하여 중단 시에도 상태를 복원할 수 있다.

---

## 부록: 아키텍처 참조

```
┌──────────────────────────────────────────────────────────┐
│  ATOM-CODE CLI/TUI Layer                                     │
│  ├── Textual TUI                                          │
│  ├── Slash Commands                                       │
│  ├── Session Management                                   │
│  └── Stall Detection                                      │
├──────────────────────────────────────────────────────────┤
│  ATOM-CODE Custom Tools                                      │
│  ├── git (+ safety rules)                                 │
│  ├── bash (+ sandbox)                                     │
│  ├── web_search (Tavily)                                  │
│  ├── fetch_url                                            │
│  └── ask_user                                             │
├──────────────────────────────────────────────────────────┤
│  ATOM-CODE Extensions                                        │
│  ├── Auto-Dream Memory Extraction                         │
│  ├── Context Compaction (auto/reactive/emergency)         │
│  ├── edit_file Conflict Defense (file locking)            │
│  ├── MCP Integration (trust_level permission)             │
│  └── Session Restore (interrupted subagent handling)      │
├──────────────────────────────────────────────────────────┤
│  DeepAgents Framework (create_deep_agent)                  │
│  ├── Built-in Tools: write_todos, ls, read_file,          │
│  │    write_file, edit_file, glob, grep, task             │
│  ├── Auto Middleware: TodoList, Filesystem, SubAgent,      │
│  │    HITL, Skills, Memory                                 │
│  ├── Backend: State, Store, Filesystem, Composite          │
│  ├── Store: InMemoryStore, PostgresStore                   │
│  ├── Declarative Subagents + task tool                     │
│  ├── HITL: interrupt_on + Command(resume)                  │
│  └── Skills: SKILL.md, on-demand loading                   │
├──────────────────────────────────────────────────────────┤
│  LangGraph Runtime                                        │
└──────────────────────────────────────────────────────────┘
```
