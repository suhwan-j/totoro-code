# Atom: Advanced CLI Coding Agent

```
        .     *    .        .   *     .
    .        ___         .        .
        .  /     \  *        .          .
   *     ./  @ @  \.    .        *       .
    .   /  \  '  /  \        .
       |    '---'    |  .     ____  ______  ____  __  __
    .  |  \       /  |       |    ||__  __||    ||  \/  |
       \   '.___.'   /    .  | || |  ||    | || || |\/| |
    .   \___________/        |_||_|  ||    |_||_||_|  |_|
         /  | | |  \    *
    *   /   | | |   \        Advanced CLI Coding Agent
       '----' ' '----'  .    Powered by DeepAgents
```

DeepAgents 프레임워크 기반의 터미널 자율 코딩 에이전트 CLI.

## 설치

```bash
# 가상환경 활성화
source .venv/bin/activate

# 패키지 설치 (개발 모드)
pip install -e .
```

## 설정

`.env` 파일에 API 키를 설정합니다:

```env
# OpenRouter (권장 - 다양한 모델 지원)
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# 또는 직접 Anthropic API
ANTHROPIC_API_KEY=sk-ant-...

# 또는 OpenAI
OPENAI_API_KEY=sk-...

# 웹 검색 (선택)
TAVILY_API_KEY=tvly-...

# LangSmith 트레이싱 (선택)
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=Atom
```

API 키 우선순위: `OPENROUTER_API_KEY` > `ANTHROPIC_API_KEY` > `OPENAI_API_KEY`

## 실행 방법

### 대화형 모드 (기본)

```bash
atom
```

터미널에서 에이전트와 대화하며 작업합니다. 도구 실행 시 승인을 요청합니다.

### 비대화형 모드

```bash
atom -n "버그를 찾아서 수정해줘"
atom -n "pyproject.toml을 읽고 의존성 목록을 알려줘"
```

단일 작업을 실행하고 결과를 출력한 뒤 종료합니다.

### 위치 인수로 작업 전달

```bash
atom fix the login bug
atom "add error handling to the API endpoint"
```

## CLI 옵션

| 옵션 | 설명 |
|------|------|
| `-n TASK`, `--non-interactive TASK` | 비대화형 모드로 단일 작업 실행 |
| `--auto-approve` | 모든 도구 실행을 자동 승인 (HITL 비활성화) |
| `--model MODEL` | 사용할 모델 지정 (예: `anthropic/claude-sonnet-4-5`) |
| `--resume SESSION_ID` | 이전 세션 이어서 작업 |
| `--verbose` | 도구 호출 및 결과를 상세히 표시 |

### 사용 예시

```bash
# 기본 대화형 모드
atom

# 자동 승인으로 빠르게 작업
atom --auto-approve -n "테스트 실행하고 결과 알려줘"

# 모델 지정
atom --model anthropic/claude-sonnet-4-5

# 도구 호출 과정을 상세히 보기
atom --verbose

# 이전 세션 이어서 작업
atom --resume session-1234567890
```

## 슬래시 커맨드 (대화형 모드)

| 커맨드 | 설명 |
|--------|------|
| `/help` | 사용 가능한 커맨드 목록 표시 |
| `/exit`, `/quit` | CLI 종료 |
| `/clear` | 대화 초기화 (새 세션 시작) |
| `/model` | 현재 사용 중인 모델 표시 |
| `/session` | 현재 세션 ID 표시 |

## 도구 (Tools)

### DeepAgents 내장 도구 (자동 제공)

| 도구 | 설명 |
|------|------|
| `read_file` | 파일 읽기 |
| `write_file` | 새 파일 작성 |
| `edit_file` | 파일 인라인 편집 (문자열 치환) |
| `glob` | 파일 패턴 매칭 |
| `grep` | 파일 내용 검색 |
| `ls` | 디렉토리 목록 |
| `write_todos` | 태스크 관리 |
| `execute` | 셸 명령 실행 (백엔드 제공) |

### Atom 커스텀 도구

| 도구 | 설명 |
|------|------|
| `git_tool` | Git 연산 (안전 규칙 내장) |
| `bash_tool` | 셸 명령 실행 (타임아웃, 출력 제한) |
| `web_search_tool` | Tavily 웹 검색 |
| `fetch_url_tool` | URL 콘텐츠 가져오기 |
| `ask_user_tool` | 사용자에게 질문 |

## HITL (Human-in-the-Loop)

기본 모드에서 `bash_tool` 실행 시 사용자 승인을 요청합니다:

```
[APPROVAL REQUIRED] bash_tool
  command: npm install express
  (a)pprove / (r)eject / (e)dit ?
  >
```

- `a` 또는 Enter: 승인하고 실행
- `r`: 거부 (에이전트가 대안 모색)
- `e`: 인수를 수정하여 실행

`--auto-approve` 플래그로 모든 승인을 자동 처리할 수 있습니다.

## Git 안전 규칙

`git_tool`에는 안전 규칙이 내장되어 있습니다:

- `git config` 변경 차단
- `--no-verify` 사용 차단
- `main`/`master` 브랜치에 force push 차단
- 위험한 명령 (`push --force`, `reset --hard`, `rebase` 등)은 사용자 승인 필요
- 민감한 파일 (`.env`, `credentials` 등) 스테이징 시 경고

## 설정 파일

설정은 5단계 우선순위로 적용됩니다:

1. CLI 인수 (최우선)
2. 환경변수 (`ATOM_MODEL`, `ATOM_FALLBACK_MODEL`, `ATOM_SANDBOX_MODE`)
3. 프로젝트 설정 (`.atom/settings.json`)
4. 사용자 전역 설정 (`~/.atom/settings.json`)
5. 기본값

```json
// .atom/settings.json (예시)
{
  "model": "anthropic/claude-sonnet-4-5",
  "permissions": {
    "mode": "default"
  }
}
```

## 모델 지원

OpenRouter를 통해 다양한 모델을 사용할 수 있습니다:

| 모델 | OpenRouter ID |
|------|---------------|
| Claude Sonnet 4.5 | `anthropic/claude-sonnet-4-5` |
| Claude Haiku 4.5 | `anthropic/claude-haiku-4-5` |
| Claude Opus 4.5 | `anthropic/claude-opus-4-5` |

## 아키텍처

```
atom/
├── cli.py              # CLI 진입점, 대화형/비대화형 모드
├── core/
│   ├── agent.py        # create_deep_agent() 래퍼
│   └── models.py       # LLM 프로바이더 초기화
├── tools/              # 커스텀 도구
│   ├── git.py          # Git 도구 (안전 규칙)
│   ├── bash.py         # Bash 도구
│   ├── web_search.py   # 웹 검색 (Tavily)
│   ├── fetch_url.py    # URL 가져오기
│   └── ask_user.py     # 사용자 질문
├── commands/
│   └── registry.py     # 슬래시 커맨드 처리
├── config/
│   ├── schema.py       # Pydantic 설정 스키마
│   └── settings.py     # 설정 로더
├── layers/             # 커스텀 레이어
│   ├── stall_detector.py
│   └── context_compaction.py
└── session/            # 세션 관리
```

## 개발

```bash
# 소스에서 실행
python -m atom

# 또는 설치 후 실행
pip install -e .
atom
```
