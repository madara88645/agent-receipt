"""Locate Claude Code session transcripts on disk.

Claude Code keeps transcripts under ``~/.claude/projects/<encoded cwd>/``: the main session
is ``<session-id>.jsonl`` and its subagents live in ``<session-id>/subagents/agent-*.jsonl``.
The encoded cwd replaces every character outside ``[A-Za-z0-9-]`` with ``-``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CLAUDE_HOME = Path("~/.claude").expanduser()


def encode_cwd(cwd: str | Path) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(cwd))


def _loose(name: str) -> str:
    """Comparison key that ignores how punctuation was encoded."""
    return re.sub(r"[^A-Za-z0-9]", "-", name)


def find_project_dir(cwd: str | Path, claude_home: Path = DEFAULT_CLAUDE_HOME) -> Path | None:
    projects = Path(claude_home) / "projects"
    if not projects.is_dir():
        return None
    exact = projects / encode_cwd(cwd)
    if exact.is_dir():
        return exact
    wanted = _loose(str(cwd))
    for candidate in sorted(projects.iterdir()):
        if candidate.is_dir() and _loose(candidate.name) == wanted:
            return candidate
    return None


def latest_session(project_dir: Path) -> Path | None:
    if not project_dir.is_dir():
        return None
    files = [p for p in project_dir.glob("*.jsonl") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


@dataclass(frozen=True)
class SessionFiles:
    session_id: str
    main: Path
    subagents: list[Path]


def session_files(main: Path) -> SessionFiles:
    main = Path(main)
    sub_dir = main.parent / main.stem / "subagents"
    subagents = sorted(sub_dir.glob("agent-*.jsonl")) if sub_dir.is_dir() else []
    return SessionFiles(session_id=main.stem, main=main, subagents=subagents)


def resolve_session(arg: str | None, cwd: str | Path, claude_home: Path = DEFAULT_CLAUDE_HOME) -> Path:
    if arg:
        as_path = Path(arg).expanduser()
        if as_path.is_file():
            return as_path
        if "/" in arg or arg.endswith(".jsonl"):
            raise FileNotFoundError(f"session file not found: {arg}")
        projects = Path(claude_home) / "projects"
        search_dirs = [d for d in [find_project_dir(cwd, claude_home)] if d]
        if projects.is_dir():
            search_dirs += [d for d in sorted(projects.iterdir()) if d.is_dir() and d not in search_dirs]
        for directory in search_dirs:
            matches = [p for p in directory.glob(f"{arg}*.jsonl") if p.is_file()]
            if matches:
                return max(matches, key=lambda p: p.stat().st_mtime)
        raise FileNotFoundError(f"session '{arg}' not found (not a file, and no session id starts with it)")

    project = find_project_dir(cwd, claude_home)
    if project is None:
        raise FileNotFoundError(
            f"no Claude Code project directory found for {cwd} under {Path(claude_home) / 'projects'}")
    latest = latest_session(project)
    if latest is None:
        raise FileNotFoundError(f"no session transcripts found in {project}")
    return latest
