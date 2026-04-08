---
name: remember
description: "Review the current conversation and capture valuable knowledge — best practices, coding conventions, architecture decisions, workflows, and user feedback — into persistent memory (AGENTS.md) or reusable skills. Use when the user says: (1) remember this, (2) save what we learned, (3) update memory, (4) capture learnings."
---

Review our conversation and capture valuable knowledge. Focus especially on **best practices** we discussed or discovered.

## Step 1: Identify Best Practices and Key Learnings

Scan the conversation for:

### Best Practices (highest priority)
- **Patterns that worked well** - approaches, techniques, or solutions we found effective
- **Anti-patterns to avoid** - mistakes, gotchas, or approaches that caused problems
- **Quality standards** - criteria we established for good code, documentation, or processes
- **Decision rationale** - why we chose one approach over another

### Other Valuable Knowledge
- Coding conventions and style preferences
- Project architecture decisions
- Workflows and processes we developed
- Tools, libraries, or techniques worth remembering
- Feedback about agent behavior or outputs

## Step 2: Decide Where to Store Each Learning

### -> Memory (AGENTS.md) for preferences and guidelines
Use memory when the knowledge is:
- A preference or guideline (not a multi-step process)
- Something to always keep in mind
- A simple rule or pattern

**Global** (`~/.atom/AGENTS.md`): Universal preferences across all projects
**Project** (`.atom/AGENTS.md`): Project-specific conventions and decisions

### -> Skill for reusable workflows and methodologies
**Create a skill when** we developed:
- A multi-step process worth reusing
- A methodology for a specific type of task
- A workflow with best practices baked in
- A procedure that should be followed consistently

## Step 3: Create Skills for Significant Best Practices

If we established best practices around a workflow or process, capture them in a skill.

### Skill Location
- User skills: `~/.atom/skills/<skill-name>/SKILL.md`
- Project skills: `.atom/skills/<skill-name>/SKILL.md`

### SKILL.md Format
```markdown
---
name: skill-name
description: "What this skill does AND when to use it."
---

# Skill Name

## Best Practices
- Best practice 1: explanation
- Best practice 2: explanation

## Process
1. First, do X
2. Then, do Y

## Common Pitfalls
- Pitfall to avoid and why
```

## Step 4: Update Memory for Simpler Learnings

For preferences, guidelines, and simple rules that don't warrant a full skill, update AGENTS.md:

```markdown
## Best Practices
- When doing X, always Y because Z
- Avoid A because it leads to B
```

## Step 5: Summarize Changes

List what you captured and where you stored it:
- Skills created (with key best practices encoded)
- Memory entries added (with location)
