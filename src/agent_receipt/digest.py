"""Fold many sessions into one table: what the last week of Claude Code cost, and where."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .policy import Finding, Policy, evaluate
from .pricing import fmt_usd
from .report import node_cost, session_cost
from .session import session_files
from .tree import AgentNode, prune_carried_over

_DURATION = re.compile(r"^(\d+)([dhw])$")


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """'7d', '36h', '2w' or an ISO date; returns an aware UTC datetime."""
    now = now or datetime.now(timezone.utc)
    m = _DURATION.match(text.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return now - timedelta(**{{"d": "days", "h": "hours", "w": "weeks"}[unit]: n})
    dt = datetime.fromisoformat(text.strip())
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def list_sessions(claude_home: Path, since: datetime | None) -> list[Path]:
    projects = Path(claude_home) / "projects"
    if not projects.is_dir():
        return []
    out = []
    for path in projects.glob("*/*.jsonl"):
        if not path.is_file():
            continue
        if since and datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < since:
            continue
        out.append(path)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


@dataclass
class SessionRow:
    session_id: str
    path: Path
    started: str
    project: str
    title: str
    agents: int
    cost: float
    main_cost: float
    sub_cost: float
    findings: list[Finding]
    by_type: dict[str, tuple[int, float]] = field(default_factory=dict)   # subagent_type -> (n, $)

    @property
    def date(self) -> str:
        return self.started[:10] if self.started else "-"


def _project_label(cwd: str | None, path: Path) -> str:
    if cwd:
        parts = [p for p in Path(cwd).parts if p not in ("/", "Users", "home")]
        return "/".join(parts[-2:]) if parts else cwd
    return path.parent.name


def summarize(root: AgentNode, findings: list[Finding], policy: Policy, path: Path, *,
              title: str | None, cwd: str | None) -> SessionRow:
    root = prune_carried_over(root)
    nodes = list(root.walk())
    cost, _ = session_cost(nodes, policy)
    sub_cost, _ = session_cost([n for n in nodes if n.depth], policy)
    by_type: dict[str, list[float]] = defaultdict(list)
    for n in nodes:
        if n.depth and n.kind != "workflow":
            by_type[n.subagent_type].append(node_cost(n, policy)[0])
    return SessionRow(
        session_id=path.stem, path=path, started=root.started, project=_project_label(cwd, path),
        title=(title or "").strip() or "(untitled)", agents=root.subtree_agents(),
        cost=cost, main_cost=cost - sub_cost, sub_cost=sub_cost, findings=findings,
        by_type={t: (len(v), sum(v)) for t, v in by_type.items()},
    )


def render_digest_text(rows: list[SessionRow], policy: Policy, label: str) -> str:
    out = [f"agent-receipt · digest · {len(rows)} session{'s' if len(rows) != 1 else ''} {label}".rstrip(), ""]
    out.append(f"{'date':<11}{'project':<26}{'title':<34}{'agents':>6}{'main $':>9}{'sub $':>9}  findings")
    for r in rows:
        rules = Counter(f.rule for f in r.findings)
        flist = ", ".join(f"{k} {n}" for k, n in rules.most_common()) or "-"
        out.append(f"{r.date:<11}{r.project[:25]:<26}{r.title[:33]:<34}{r.agents:>6}{r.main_cost:>9.2f}{r.sub_cost:>9.2f}  {flist}")
    out.append("")
    total = sum(r.cost for r in rows)
    main = sum(r.main_cost for r in rows)
    sub = sum(r.sub_cost for r in rows)
    agents = sum(r.agents for r in rows)
    out.append(f"Total {fmt_usd(total)} · main sessions {fmt_usd(main)} · subagents {fmt_usd(sub)} · {agents} agents")
    rules = Counter(f.rule for r in rows for f in r.findings)
    out.append("Findings: " + (", ".join(f"{k} {n}" for k, n in rules.most_common()) or "none"))
    by_type: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for t, (n, c) in r.by_type.items():
            by_type[t].append((n, c))
    if by_type:
        out.append("Subagents by type:")
        for t, pairs in sorted(by_type.items(), key=lambda kv: -sum(c for _, c in kv[1])):
            n = sum(k for k, _ in pairs)
            c = sum(k for _, k in pairs)
            out.append(f"  {t:<20} {n:>5} agents  {fmt_usd(c):>9} total  {fmt_usd(c / n if n else 0):>8} avg")
    top = sorted(rows, key=lambda r: -r.cost)[:3]
    if top:
        out.append("Most expensive sessions:")
        for r in top:
            out.append(f"  {fmt_usd(r.cost):>9}  {r.date}  {r.project[:24]:<25} {r.title[:40]}  ({r.session_id[:8]})")
    return "\n".join(out) + "\n"


def render_digest_json(rows: list[SessionRow], policy: Policy, label: str) -> str:
    data = {
        "digest": label,
        "sessions": [{
            "session": r.session_id, "date": r.date, "project": r.project, "title": r.title, "agents": r.agents,
            "cost": r.cost, "main_cost": r.main_cost, "subagent_cost": r.sub_cost,
            "findings": [{"rule": f.rule, "agent_id": f.agent_id, "message": f.message} for f in r.findings],
            "by_type": {t: {"agents": n, "cost": c} for t, (n, c) in r.by_type.items()},
        } for r in rows],
        "total_cost": sum(r.cost for r in rows),
        "main_cost": sum(r.main_cost for r in rows),
        "subagent_cost": sum(r.sub_cost for r in rows),
        "agents": sum(r.agents for r in rows),
        "findings_by_rule": dict(Counter(f.rule for r in rows for f in r.findings)),
    }
    return json.dumps(data, indent=2)
