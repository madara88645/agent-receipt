"""Render the agent tree, the totals and the findings as text or JSON."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict

from .parse import Usage
from .policy import Finding, Policy
from .tree import AgentNode, failure_summary

_MAX_EXAMPLES_PER_RULE = 5


def fmt_tokens(n: int) -> str:
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def _totals_by_model(nodes: list[AgentNode]) -> dict[str, dict[str, int]]:
    calls: Counter = Counter()
    usage: dict[str, Usage] = defaultdict(Usage)
    for node in nodes:
        for call in node.calls:
            calls[call.model] += 1
            usage[call.model] = usage[call.model] + call.usage
    return {
        model: {"calls": calls[model], "input": u.input, "cache_create": u.cache_create,
                "cache_read": u.cache_read, "output": u.output}
        for model, u in sorted(usage.items(), key=lambda kv: -kv[1].output)
    }


def _time_range(root: AgentNode) -> str:
    starts = [n.started for n in root.walk() if n.started]
    ends = [n.ended for n in root.walk() if n.ended]
    if not starts:
        return ""
    a, b = min(starts), max(ends)
    return f"{a[:10]} {a[11:16]}Z → {b[11:16]}Z" if a[:10] == b[:10] else f"{a[:16]}Z → {b[:16]}Z"


def _model_column(node: AgentNode) -> str:
    models = node.models()
    if node.depth == 0:
        return ", ".join(f"{m} x{n}" for m, n in models.most_common()) or "-"
    if not node.has_transcript:
        return "(no transcript)"
    single = len(models) == 1 and (node.resolved_model is None or next(iter(models)) == node.resolved_model)
    if single:
        observed = next(iter(models))
        return f"{node.requested_model} → {observed}" if node.requested_model else observed
    return "ran " + ", ".join(f"{m} x{n}" for m, n in models.most_common())


def _usage_cells(u: Usage, calls: int) -> str:
    return (f"{calls:>4} calls  out {fmt_tokens(u.output):>6}  read {fmt_tokens(u.cache_read):>6}"
            f"  write {fmt_tokens(u.cache_create):>6}")


def _tree_lines(node: AgentNode, flagged: set[str | None], prefix: str = "", is_last: bool = True,
                is_root: bool = True) -> list[str]:
    connector = "" if is_root else ("└── " if is_last else "├── ")
    mark = "  !" if node.agent_id in flagged else ""
    orphan = "  (parent unknown)" if not node.parent_known else ""
    desc = node.description if node.depth else "main session"
    line = (f"{prefix}{connector}{desc[:40]:<40} {node.subagent_type:<15} {_model_column(node):<44} "
            f"{_usage_cells(node.usage(), len(node.calls))}{mark}{orphan}")
    lines = [line.rstrip()]
    child_prefix = "" if is_root else prefix + ("    " if is_last else "│   ")
    if node.failed_spawns:
        n = len(node.failed_spawns)
        bar = "│   " if node.children else "    "
        lines.append(f"{child_prefix}{bar}! {n} spawn attempt{'s' if n != 1 else ''} failed: "
                     f"{failure_summary(node.failed_spawns)}")
    for i, child in enumerate(node.children):
        lines += _tree_lines(child, flagged, child_prefix, i == len(node.children) - 1, is_root=False)
    return lines


def render_text(root: AgentNode, findings: list[Finding], policy: Policy, session_label: str) -> str:
    flagged = {f.agent_id for f in findings if f.agent_id}
    nodes = list(root.walk())
    max_depth = max(n.depth for n in nodes)
    out: list[str] = []
    out.append(f"agent-receipt · session {session_label}   {_time_range(root)}".rstrip())
    out.append("")
    out += _tree_lines(root, flagged)
    out.append("")

    def totals_block(title: str, rows: dict[str, dict[str, int]]) -> None:
        out.append(title)
        if not rows:
            out.append("  (none)")
        for model, r in rows.items():
            out.append(f"  {model:<24} {r['calls']:>5} calls  out {fmt_tokens(r['output']):>7}"
                       f"  read {fmt_tokens(r['cache_read']):>7}  write {fmt_tokens(r['cache_create']):>7}")

    totals_block("Totals by model (whole session):", _totals_by_model(nodes))
    totals_block("Totals by model (subagents only):", _totals_by_model([n for n in nodes if n.depth]))
    out.append("")
    limit_agents = policy.max_agents or "none"
    failed_total = sum(len(n.failed_spawns) for n in nodes)
    failed_note = f"failed spawn attempts: {failed_total} · " if failed_total else ""
    out.append(f"Agents spawned: {root.subtree_agents()} (limit {limit_agents}) · {failed_note}"
               f"max depth: {max_depth} (limit {policy.max_depth}) · "
               f"cheap models: {', '.join(policy.cheap_models)}")
    out.append("")
    if not findings:
        out.append("No findings.")
    else:
        out.append(f"{len(findings)} finding{'s' if len(findings) != 1 else ''}:")
        by_rule: dict[str, list[Finding]] = {}
        for f in findings:
            by_rule.setdefault(f.rule, []).append(f)
        for rule, group in by_rule.items():
            out.append(f"  {rule} ({len(group)})")
            for f in group[:_MAX_EXAMPLES_PER_RULE]:
                out.append(f"    {f.message}")
            if len(group) > _MAX_EXAMPLES_PER_RULE:
                out.append(f"    ... {len(group) - _MAX_EXAMPLES_PER_RULE} more")
    return "\n".join(out) + "\n"


def _node_dict(node: AgentNode) -> dict:
    u = node.usage()
    return {
        "agent_id": node.agent_id,
        "description": node.description,
        "subagent_type": node.subagent_type,
        "requested_model": node.requested_model,
        "resolved_model": node.resolved_model,
        "depth": node.depth,
        "has_transcript": node.has_transcript,
        "parent_known": node.parent_known,
        "calls": len(node.calls),
        "models": dict(node.models()),
        "usage": {"input": u.input, "cache_create": u.cache_create, "cache_read": u.cache_read, "output": u.output},
        "started": node.started,
        "ended": node.ended,
        "failed_spawns": [{"description": s.description, "subagent_type": s.subagent_type, "error": s.error}
                          for s in node.failed_spawns],
        "children": [_node_dict(c) for c in node.children],
    }


def render_json(root: AgentNode, findings: list[Finding], policy: Policy, session_label: str) -> str:
    nodes = list(root.walk())
    data = {
        "session": session_label,
        "agents": root.subtree_agents(),
        "max_depth": max(n.depth for n in nodes),
        "failed_spawn_attempts": sum(len(n.failed_spawns) for n in nodes),
        "totals": _totals_by_model(nodes),
        "subagent_totals": _totals_by_model([n for n in nodes if n.depth]),
        "findings": [asdict(f) for f in findings],
        "policy": asdict(policy),
        "tree": _node_dict(root),
    }
    return json.dumps(data, indent=2)
