# Totoro: 터미널 자율 코딩 에이전트 CLI

![totoro.png](asset/cli_img.png)

## 주요 기능

- **멀티 프로바이더** — OpenRouter, Anthropic, OpenAI, vLLM 지원 + 런타임 모델 전환
- **병렬 서브에이전트** — catbus(플래너), satsuki(코더), mei(연구원), tatsuo(리뷰어), susuwatari(마이크로) 동시 실행
- **실시간 상태 표시** — 서브에이전트 진행 상황, 도구 호출, 토큰 사용량 라이브 표시
- **세션 영구 저장** — SQLite 기반 체크포인터로 프로세스 재시작 후에도 세션 복원
- **인라인 자동완성** — `/` 입력 시 드롭다운 메뉴로 커맨드 선택 (prompt_toolkit)
- **HITL (Human-in-the-Loop)** — 위험한 도구 실행 전 승인/거부/수정 선택
- **Stall Detection** — 에이전트가 멈추면 4단계 자동 복구 (넛지 → 모델전환 → 질문 → 중단)
- **Auto-Dream Memory** — 대화에서 중요 정보를 자동 추출하여 `~/.totoro/character.md`에 장기 기억 저장
- **프로젝트 컨텍스트** — `/init`으로 프로젝트 스캔 후 `TOTORO.md` 자동 생성
- **토큰 최적화** — CJK 가중치 토큰 추정, 모델별 컨텍스트 윈도우 자동 매핑, LLM 기반 컨텍스트 압축
- **Git 안전 규칙** — force push, config 변경 등 위험 명령 자동 차단

## 설치

```bash
# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate

# 패키지 설치 (개발 모드)
pip install -e .
```

### Docker

```bash
docker build -t totoro .

# 인터랙티브 모드
docker run -it --rm \
  -e OPENROUTER_API_KEY=sk-or-... \
  -v ~/.totoro:/root/.totoro \
  -v $(pwd):/workspace \
  totoro

# 단일 명령
docker run --rm \
  -e OPENROUTER_API_KEY=sk-or-... \
  -v $(pwd):/workspace \
  totoro -n "이 프로젝트를 분석해줘" --auto-approve
```

## 설정

첫 실행 시 셋업 위저드가 실행됩니다. 수동 설정은 `~/.totoro/settings.json`에서:

```json
{
  "provider": "openrouter",
  "api_key": "sk-or-v1-...",
  "model": "anthropic/claude-sonnet-4-5"
}
```

지원 프로바이더: OpenRouter, Anthropic, OpenAI, vLLM

## 사용법

### 대화형 모드

```bash
totoro
```

```
─────────────────────────────────────────────────────────────────────  DEFAULT  ──
◆ > 안녕! 오늘 날짜가 뭐야?

● > 오늘은 2026년 4월 10일 (금요일)이야.
── Done (Tools: 0 · ↑ 6.1k ↓ 98 tokens) ───────────────────────────────────────
```

### 비대화형 모드

```bash
totoro -n "버그를 찾아서 수정해줘"
totoro fix the login bug
totoro --auto-approve -n "테스트 실행하고 결과 알려줘"
```

## CLI 옵션

| 옵션 | 설명 |
|------|------|
| `-n TASK` | 비대화형 모드로 단일 작업 실행 |
| `--auto-approve` | 모든 도구 실행을 자동 승인 |
| `--model MODEL` | 사용할 모델 지정 |
| `--provider PROVIDER` | LLM 프로바이더 (`auto`, `openrouter`, `anthropic`, `openai`, `vllm`) |
| `--resume SESSION_ID` | 이전 세션 이어서 작업 |
| `--list-sessions` | 저장된 세션 목록 표시 |
| `--verbose` | 도구 호출 결과를 상세 표시 |

## 슬래시 커맨드

대화형 모드에서 `/`를 입력하면 자동완성 드롭다운이 표시됩니다.

| 커맨드 | 설명 |
|--------|------|
| `/help` | 사용 가능한 커맨드 목록 |
| `/init` | 프로젝트 스캔 → TOTORO.md 생성 |
| `/new [설명]` | 새 세션 시작 |
| `/model [모델명]` | 현재 모델 표시 또는 런타임 전환 |
| `/mode` | 모드 순환 (default → auto-approve → plan-only) |
| `/session [번호]` | 세션 정보 또는 전환 |
| `/sessions` | 저장된 세션 목록 |
| `/compact` | 컨텍스트 강제 압축 |
| `/memory` | 추출된 장기 기억 표시/관리 |
| `/skill` | 스킬 관리 (list/add/install/remove) |
| `/status` | 에이전트 상태 (턴, 토큰, 메모리) |
| `/exit` | CLI 종료 |

## 토큰 표시

```
── Done (Tools: 2 · ↑ 6.0k ↓ 200 tokens) ──
                    │         │
                    │         └─ ↓ 출력 토큰 (모델 응답)
                    └─ ↑ 입력 토큰 (캐시 제외, 새로 전송된 컨텍스트)
```

프롬프트 캐싱 활성 시 ↑는 캐시되지 않은 실효 입력만 표시합니다.

## 모드

`Shift+Tab` 또는 `/mode`로 순환:

| 모드 | 아이콘 | 설명 |
|------|--------|------|
| default | ◆ | 위험한 도구 실행 시 승인 요청 |
| auto-approve | ⏵⏵ | 모든 도구 실행 자동 승인 |
| plan-only | ◇ | 계획만 수립, 실행 안 함 |

## 병렬 서브에이전트

복잡한 작업은 `orchestrate_tool`로 여러 서브에이전트를 동시 실행합니다:

| 타입 | 이름 | 역할 | 도구 |
|------|------|------|------|
| planner | catbus | 요청 분석, 실행 계획 수립 | 없음 (단일 LLM 호출) |
| coder | satsuki | 코드 구현, 리팩토링, 빌드 | 전체 |
| researcher | mei | 코드베이스 탐색, 웹 검색 | ls, read_file, glob, grep (읽기 전용) |
| reviewer | tatsuo | 테스트, 코드 리뷰, 품질 검증 | ls, read_file, glob, grep, execute |
| micro | susuwatari | 단일 파일 수정, atomic 작업 | 전체 |

서브에이전트 실행 중 요약이 표시됩니다:

```
── Subagent Summary ──
  ✓ satsuki-0 (25s, 8 tools)
  ✓ mei-0 (13s, 3 tools)
● > 프로젝트 구조를 생성하고 의존성을 설치했습니다.
── Done (Tools: 6 · Subagents: 2 · ↑ 6.2k ↓ 105 tokens) ─────────────────────
```

### Auto-Dispatch

catbus만 디스패치하면 자동으로 플랜 생성 → 실행 에이전트 배치까지 진행:

```
1. orchestrate_tool → catbus (플랜 생성)
2. catbus 플랜 JSON 파싱
3. 실행 에이전트에 원래 유저 요청 + 플랜 컨텍스트 자동 주입
4. 병렬 실행 → 결과 반환
```

## HITL (Human-in-the-Loop)

기본 모드에서 파일 쓰기, 셸 명령 등 실행 시 승인을 요청합니다:

```
[APPROVAL REQUIRED] execute
  command: npm install express
  (a)pprove / (r)eject / (e)dit ?
  >
```

## 도구

### 프레임워크 도구 (FilesystemMiddleware)

| 도구 | 설명 |
|------|------|
| `read_file` | 파일 읽기 (offset/limit 지원) |
| `write_file` | 새 파일 작성 |
| `edit_file` | 파일 인라인 편집 (문자열 치환) |
| `glob` | 파일 패턴 매칭 |
| `grep` | 파일 내용 검색 |
| `ls` | 디렉토리 목록 |
| `write_todos` | 태스크 관리 (플랜 생성) |
| `execute` | 셸 명령 실행 |

### 커스텀 도구

| 도구 | 설명 |
|------|------|
| `orchestrate_tool` | 병렬 서브에이전트 실행 |
| `git_tool` | Git 연산 (안전 규칙 내장) |
| `web_search_tool` | Tavily 웹 검색 |
| `fetch_url_tool` | URL 콘텐츠 가져오기 |
| `ask_user_tool` | 사용자에게 질문 |

## 미들웨어 스택

`create_agent()`를 직접 사용하여 미들웨어를 명시적으로 조립합니다.
`create_deep_agent()`의 SubAgentMiddleware(task 도구)를 제거하여 턴당 ~2,178 토큰 절감.

| 순서 | 미들웨어 | 역할 |
|------|----------|------|
| 1 | TodoListMiddleware | write_todos 도구 |
| 2 | SkillsMiddleware | 스킬 시스템 |
| 3 | FilesystemMiddleware | 파일 I/O + 셸 실행 |
| 4 | SummarizationMiddleware | 대화 요약 |
| 5 | PatchToolCallsMiddleware | dangling tool call 수정 |
| 6 | SanitizeMiddleware | surrogate 문자 정리 |
| 7 | ContextCompactionMiddleware | LLM 기반 3단계 컨텍스트 압축 |
| 8 | StallDetectorMiddleware | 에이전트 정체 감지/복구 |
| 9 | AutoDreamMiddleware | 장기 기억 자동 추출 |
| 10 | AnthropicPromptCachingMiddleware | 프롬프트 캐싱 |
| 11 | HumanInTheLoopMiddleware | HITL 인터럽트 |

## 프로젝트 컨텍스트 (/init)

`/init` 명령으로 에이전트가 프로젝트를 동적 탐색하고 `TOTORO.md`를 생성합니다:

```
◆ > /init
● > (mei가 프로젝트를 탐색하고 TOTORO.md를 생성합니다)
```

생성된 `TOTORO.md`는 시스템 프롬프트에 전체 로딩되지 않고, 에이전트가 필요 시 `read_file`로 온디맨드 참조합니다 (~60 토큰 힌트만 주입).

## 데이터 저장 경로

| 경로 | 설명 |
|------|------|
| `~/.totoro/settings.json` | 사용자 전역 설정 (API 키, 모델) |
| `~/.totoro/checkpoints.db` | 세션 대화 상태 (SQLite) |
| `~/.totoro/sessions.json` | 세션 메타데이터 |
| `~/.totoro/character.md` | Auto-Dream 장기 기억 |
| `TOTORO.md` | 프로젝트 컨텍스트 (/init 생성) |
| `.totoro/settings.json` | 프로젝트별 설정 |
| `.totoro/skills/` | 프로젝트별 스킬 |

## 아키텍처

```
totoro/
├── cli.py                    # CLI 진입점 + 대화형 메인 루프
├── orchestrator.py           # 병렬 서브에이전트 실행 (multiprocessing)
├── status.py                 # 실시간 상태 표시 (↑/↓ 토큰 표시)
├── pane.py                   # 서브에이전트 패널 상태
├── tui.py                    # curses 기반 split-pane TUI
├── core/
│   ├── agent.py              # create_agent() 기반 에이전트 생성
│   └── models.py             # LLM 프로바이더 초기화
├── layers/
│   ├── _token_utils.py       # CJK 토큰 추정 + 모델 윈도우 매핑
│   ├── context_compaction.py # LLM 기반 3단계 컨텍스트 압축
│   ├── auto_dream.py         # 장기 기억 추출 (비례 배분 주입)
│   ├── stall_detector.py     # 4단계 정체 감지/복구
│   └── sanitize.py           # surrogate 문자 정리
├── commands/registry.py      # 슬래시 커맨드 (16개)
├── config/                   # Pydantic 설정 스키마 + 로더
├── session/                  # 세션 관리 + 복원
├── tools/                    # git, web_search, fetch_url, ask_user
└── built-in/skills/          # remember, init, skill-creator
```
