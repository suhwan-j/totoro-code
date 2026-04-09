---
name: skill-creator
description: "Guide for creating effective skills that extend agent capabilities with specialized knowledge, workflows, or tool integrations. Use when the user asks to: (1) create a new skill, (2) make a skill, (3) build a skill, (4) set up a skill, (5) update or modify an existing skill, (6) learn about skill structure."
---

# Skill Creator

## Skill Location for Totoro

Totoro loads skills from three sources (lowest to highest precedence):

| # | Directory | Scope | Notes |
|---|-----------|-------|-------|
| 0 | `built-in/skills/` | Built-in | Ships with Totoro |
| 1 | `~/.totoro/skills/` | User (global) | Shared across all projects |
| 2 | `.totoro/skills/` | Project | Project-specific skills |

When two directories contain a skill with the same name, the higher-precedence version wins.

## Antotoroy of a Skill

```
skill-name/
├── SKILL.md          (required - main instructions)
├── scripts/          (optional - executable code)
├── references/       (optional - documentation)
└── assets/           (optional - templates, examples)
```

### SKILL.md Format

```markdown
---
name: skill-name
description: "What this skill does AND when to use it. Include triggers."
allowed-tools: tool1, tool2  # optional
---

# Instructions here (markdown)
```

- `description` is the primary triggering mechanism — be specific about when to use
- Body is only loaded after the skill triggers
- Keep SKILL.md under 500 lines

## Core Principles

### Concise is Key
Only add context the agent doesn't already have. Challenge each piece: "Does this paragraph justify its token cost?"

### Set Appropriate Degrees of Freedom
- **High freedom** (text instructions): Multiple approaches valid
- **Medium freedom** (pseudocode/scripts with params): Preferred pattern exists
- **Low freedom** (specific scripts): Operations are fragile, consistency critical

### Progressive Disclosure
1. **Metadata** (name + description) — Always in context (~100 words)
2. **SKILL.md body** — When skill triggers (<5k words)
3. **Bundled resources** — As needed (unlimited)

## Skill Creation Process

### Step 1: Understand with Examples
Ask: What should the skill support? How would it be triggered? What are concrete usage examples?

### Step 2: Plan Reusable Contents
For each example, identify what scripts, references, or assets would help when executing repeatedly.

### Step 3: Create the Skill

```bash
# Via /skill add command
/skill add <skill-name>

# Or manually create directory
mkdir -p .totoro/skills/<skill-name>
# Then create SKILL.md with frontmatter
```

### Step 4: Write SKILL.md
- Use imperative form ("Create a file" not "You should create a file")
- Put best practices near the top
- Include anti-patterns and common pitfalls
- Reference bundled resources clearly

### Step 5: Iterate
Use the skill on real tasks, notice struggles, and update accordingly.

## What NOT to Include
- README.md, CHANGELOG.md, or other auxiliary docs
- Setup/testing procedures
- User-facing documentation (the skill is for the agent)
