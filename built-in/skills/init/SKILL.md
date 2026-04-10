---
name: init
description: "Project initialization — deeply scan the current project and generate TOTORO.md with comprehensive project context (architecture, tech stack, patterns, conventions). Triggered by /init command."
---

# Project Initialization (/init)

You are initializing the project context. Your goal is to **deeply explore** the current project
and generate a comprehensive `TOTORO.md` file that will serve as persistent context for all future conversations.

## Process

### Phase 1: Deep Exploration (use orchestrate_tool)

Dispatch **mei** (researcher) to explore the project in parallel. Break exploration into focused tasks:

1. **Project metadata & dependencies** — Read package.json, pyproject.toml, Cargo.toml, go.mod, or equivalent. Identify tech stack, dependencies, and scripts.
2. **Architecture & structure** — Use ls, glob to map directory structure. Identify main modules, entry points, and layer boundaries.
3. **Code patterns & conventions** — Use grep to find common patterns (imports, exports, class definitions, naming conventions). Read 2-3 representative source files.
4. **Build & deploy config** — Check for Dockerfile, docker-compose, CI/CD configs, Makefile, serverless.yml, etc.
5. **Documentation** — Read README.md, ARCHITECTURE.md, CONTRIBUTING.md, or any existing docs.

### Phase 2: Synthesis

After receiving exploration results, synthesize everything into TOTORO.md with this structure:

```markdown
# Project: <name>

## Overview
<1-2 sentence description of what this project does>

## Tech Stack
| Category | Technology |
|----------|-----------|
| Language | ... |
| Framework | ... |
| Database | ... |
| ... | ... |

## Architecture
<high-level architecture description>
<main modules and their responsibilities>
<data flow between components>

## Directory Structure
<key directories and their purpose — NOT a full tree, focus on what matters>

## Key Patterns & Conventions
- Naming: ...
- File organization: ...
- Import style: ...
- Error handling: ...
- Testing: ...

## Build & Run
- Install: `...`
- Dev: `...`
- Test: `...`
- Build: `...`

## API / Endpoints (if applicable)
<key API routes or CLI commands>

## Important Notes for AI Agent
<anything that would help an AI coding agent avoid mistakes>
<gotchas, non-obvious constraints, deployment considerations>
```

### Phase 3: Save

Use `write_file` to save the synthesized content to `TOTORO.md` in the project root.

## Rules

- Write in the SAME LANGUAGE as the project's README or code comments
- Be concise but thorough — this will be loaded into the system prompt
- Focus on information that helps an AI understand the project well enough to contribute code
- Do NOT include raw file contents — summarize and extract patterns
- If the project already has a TOTORO.md, read it first and update/improve it rather than rewriting from scratch
- Total document should be under 3000 characters to keep system prompt lean
