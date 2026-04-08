"""Skill manager — CRUD + remote install for SKILL.md files."""

import os
import json
import shutil
import urllib.request
from pathlib import Path
from dataclasses import dataclass

_DIM = "\033[0;90m"
_BOLD = "\033[1m"
_GREEN = "\033[0;32m"
_CYAN = "\033[0;36m"
_YELLOW = "\033[1;33m"
_RED = "\033[1;31m"
_RESET = "\033[0m"


@dataclass
class SkillInfo:
    name: str
    description: str
    path: Path
    scope: str  # "built-in" | "global" | "project"


class SkillManager:
    """Manages skills in built-in/skills/, ~/.atom/skills/ (global) and .atom/skills/ (project).

    Precedence (lowest → highest): built-in → global → project.
    """

    def __init__(self, project_root: str):
        # Built-in skills ship with the atom package
        self.builtin_dir = Path(__file__).resolve().parent.parent / "built-in" / "skills"
        self.global_dir = Path.home() / ".atom" / "skills"
        self.project_dir = Path(project_root) / ".atom" / "skills"

    def list_skills(self) -> list[SkillInfo]:
        """List all installed skills (built-in + global + project)."""
        skills = []
        skills.extend(self._scan_dir(self.builtin_dir, "built-in"))
        skills.extend(self._scan_dir(self.global_dir, "global"))
        skills.extend(self._scan_dir(self.project_dir, "project"))
        return skills

    def get_skill_paths(self) -> list[str]:
        """Return skill source paths for create_deep_agent(skills=...).

        Order: built-in (lowest precedence) → global → project (highest).
        """
        paths = []
        if self.builtin_dir.exists():
            paths.append(str(self.builtin_dir))
        if self.global_dir.exists():
            paths.append(str(self.global_dir))
        if self.project_dir.exists():
            paths.append(str(self.project_dir))
        return paths

    def add_skill(self, name: str, description: str, content: str,
                  allowed_tools: str = "", scope: str = "project") -> Path:
        """Create a new skill with SKILL.md."""
        base = self.project_dir if scope == "project" else self.global_dir
        skill_dir = base / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        frontmatter = f"""---
name: {name}
description: {description}
"""
        if allowed_tools:
            frontmatter += f"allowed-tools: {allowed_tools}\n"
        frontmatter += "---\n\n"

        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(frontmatter + content, encoding="utf-8")
        return skill_path

    def install_skill(self, source: str, skill_name: str = "",
                      scope: str = "global") -> tuple[str, Path | None]:
        """Install a skill from a remote URL or GitHub repo.

        Supports:
          /skill install <url>                        — single SKILL.md
          /skill install <repo-url> --skill <name>    — specific skill from repo's skills/ dir
          /skill install gh:user/repo --skill <name>  — same, shorthand

        Returns (message, path_or_none).
        """
        # ── Repo + --skill mode: download entire skill directory ──
        if skill_name:
            repo_info = self._parse_github_repo(source)
            if not repo_info:
                return f"Cannot parse GitHub repo from: {source}", None
            owner, repo, branch = repo_info
            return self._install_from_repo(owner, repo, branch, skill_name, scope)

        # ── Legacy single-file mode ──
        url = self._resolve_url(source)
        if not url:
            return f"Cannot resolve source: {source}", None

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "atom-code/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
        except Exception as e:
            return f"Download failed: {e}", None

        # Parse name from frontmatter
        name = self._extract_name(content, source)
        if not name:
            return "Could not determine skill name from SKILL.md", None

        base = self.global_dir if scope == "global" else self.project_dir
        skill_dir = base / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")

        return f"Installed '{name}'", skill_path

    def remove_skill(self, name: str) -> str:
        """Remove a skill by name. Checks project first, then global."""
        for base, scope in [(self.project_dir, "project"), (self.global_dir, "global")]:
            skill_dir = base / name
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
                return f"Removed '{name}' from {scope} skills"
        return f"Skill '{name}' not found"

    def format_list(self) -> str:
        """Format skill list for display."""
        skills = self.list_skills()
        if not skills:
            return f"  {_DIM}No skills installed.{_RESET}\n  {_DIM}Use /skill add <name> or /skill install <url>{_RESET}"

        lines = []
        # Group by scope
        builtin_skills = [s for s in skills if s.scope == "built-in"]
        global_skills = [s for s in skills if s.scope == "global"]
        project_skills = [s for s in skills if s.scope == "project"]

        if builtin_skills:
            lines.append(f"  {_DIM}built-in/skills/{_RESET}")
            for i, s in enumerate(builtin_skills):
                is_last = i == len(builtin_skills) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"  {_DIM}{connector}{_CYAN}{s.name}{_RESET} {_DIM}— {s.description}{_RESET}")

        if global_skills:
            if builtin_skills:
                lines.append("")
            lines.append(f"  {_DIM}~/.atom/skills/{_RESET}")
            for i, s in enumerate(global_skills):
                is_last = i == len(global_skills) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"  {_DIM}{connector}{_CYAN}{s.name}{_RESET} {_DIM}— {s.description}{_RESET}")

        if project_skills:
            if builtin_skills or global_skills:
                lines.append("")
            lines.append(f"  {_DIM}.atom/skills/{_RESET}")
            for i, s in enumerate(project_skills):
                is_last = i == len(project_skills) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"  {_DIM}{connector}{_CYAN}{s.name}{_RESET} {_DIM}— {s.description}{_RESET}")

        return "\n".join(lines)

    # ─── Internal ───

    def _scan_dir(self, base: Path, scope: str) -> list[SkillInfo]:
        if not base.exists():
            return []
        skills = []
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            desc = self._read_description(skill_md)
            skills.append(SkillInfo(name=child.name, description=desc, path=child, scope=scope))
        return skills

    def _read_description(self, skill_md: Path) -> str:
        try:
            text = skill_md.read_text(encoding="utf-8")[:2000]
            # Parse YAML frontmatter
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    frontmatter = text[3:end]
                    for line in frontmatter.splitlines():
                        if line.startswith("description:"):
                            return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return "(no description)"

    def _resolve_url(self, source: str) -> str | None:
        if source.startswith("http://") or source.startswith("https://"):
            # Direct URL — if GitHub blob, convert to raw
            if "github.com" in source and "/blob/" in source:
                return source.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
            if "github.com" in source and "/tree/" in source:
                # Assume SKILL.md in directory
                raw = source.replace("github.com", "raw.githubusercontent.com").replace("/tree/", "/")
                return raw.rstrip("/") + "/SKILL.md"
            return source

        if source.startswith("gh:"):
            # gh:user/repo/path/to/skill → raw GitHub URL
            parts = source[3:].split("/", 2)
            if len(parts) >= 2:
                user, repo = parts[0], parts[1]
                path = parts[2] if len(parts) > 2 else ""
                return f"https://raw.githubusercontent.com/{user}/{repo}/main/{path}/SKILL.md"

        if source.startswith("gist:"):
            gist_id = source[5:]
            return f"https://gist.githubusercontent.com/{gist_id}/raw/SKILL.md"

        return None

    def _parse_github_repo(self, source: str) -> tuple[str, str, str] | None:
        """Parse GitHub repo info from URL or shorthand.

        Returns (owner, repo, branch) or None.
        """
        import re

        # gh:user/repo or gh:user/repo/...
        if source.startswith("gh:"):
            parts = source[3:].split("/")
            if len(parts) >= 2:
                return parts[0], parts[1], "main"
            return None

        # https://github.com/user/repo[/tree/branch/...]
        m = re.match(
            r"https?://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+))?",
            source.rstrip("/"),
        )
        if m:
            owner, repo, branch = m.group(1), m.group(2), m.group(3) or "main"
            return owner, repo, branch

        return None

    def _install_from_repo(self, owner: str, repo: str, branch: str,
                           skill_name: str, scope: str) -> tuple[str, Path | None]:
        """Download an entire skill directory from a GitHub repo's skills/ folder."""
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/skills/{skill_name}?ref={branch}"

        try:
            entries = self._github_api_get(api_url)
        except Exception as e:
            return f"Failed to fetch skill '{skill_name}' from {owner}/{repo}: {e}", None

        if not isinstance(entries, list):
            return f"Skill '{skill_name}' not found in {owner}/{repo}/skills/", None

        base = self.global_dir if scope == "global" else self.project_dir
        skill_dir = base / skill_name

        # Clean existing before install
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        skill_dir.mkdir(parents=True, exist_ok=True)

        file_count = self._download_github_dir(entries, skill_dir, owner, repo, branch)

        # Verify SKILL.md exists
        if not (skill_dir / "SKILL.md").exists():
            shutil.rmtree(skill_dir)
            return f"No SKILL.md found in {owner}/{repo}/skills/{skill_name}", None

        return f"Installed '{skill_name}' ({file_count} files) from {owner}/{repo}", skill_dir

    def _download_github_dir(self, entries: list, dest: Path,
                             owner: str, repo: str, branch: str) -> int:
        """Recursively download a GitHub directory tree. Returns file count."""
        count = 0
        for entry in entries:
            name = entry["name"]
            if entry["type"] == "file":
                download_url = entry.get("download_url")
                if download_url:
                    try:
                        req = urllib.request.Request(
                            download_url, headers={"User-Agent": "atom-code/1.0"})
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            data = resp.read()
                        file_path = dest / name
                        file_path.write_bytes(data)
                        count += 1
                    except Exception:
                        pass  # skip failed files silently
            elif entry["type"] == "dir":
                sub_dir = dest / name
                sub_dir.mkdir(parents=True, exist_ok=True)
                try:
                    sub_entries = self._github_api_get(entry["url"])
                    if isinstance(sub_entries, list):
                        count += self._download_github_dir(
                            sub_entries, sub_dir, owner, repo, branch)
                except Exception:
                    pass
        return count

    def _github_api_get(self, url: str):
        """GET from GitHub API and return parsed JSON."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "atom-code/1.0",
            "Accept": "application/vnd.github.v3+json",
        })
        # Use token if available
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"token {token}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _extract_name(self, content: str, source: str) -> str | None:
        # Try frontmatter
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                for line in content[3:end].splitlines():
                    if line.startswith("name:"):
                        return line.split(":", 1)[1].strip()
        # Fallback: derive from URL
        clean = source.rstrip("/").split("/")[-1]
        if clean and clean != "SKILL.md":
            return clean.lower().replace(" ", "-")
        parts = source.rstrip("/").split("/")
        if len(parts) >= 2:
            return parts[-2].lower().replace(" ", "-")
        return None
