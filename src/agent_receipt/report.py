"""Render the agent tree, the totals and the findings as text or JSON."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict

from .parse import Usage
from .policy import Finding, Policy
from .pricing import fmt_usd
from .tree import AgentNode, carried_over, failure_summary

_MAX_EXAMPLES_PER_RULE = 5


def fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "-"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def fmt_tokens(n: int) -> str:
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def node_cost(node: AgentNode, policy: Policy) -> tuple[float, int]:
    """Priced dollars for the node's own calls, plus how many calls had no known price."""
    total, unpriced = 0.0, 0
    for call in node.calls:
        c = policy.cost_of(call.model, call.usage)
        if c is None:
            unpriced += 1
        else:
            total += c
    return total, unpriced


def _totals_by_model(nodes: list[AgentNode], policy: Policy) -> dict[str, dict]:
    calls: Counter = Counter()
    usage: dict[str, Usage] = defaultdict(Usage)
    for node in nodes:
        for call in node.calls:
            calls[call.model] += 1
            usage[call.model] = usage[call.model] + call.usage
    return {
        model: {"calls": calls[model], "input": u.input, "cache_create": u.cache_create,
                "cache_read": u.cache_read, "output": u.output, "cost": policy.cost_of(model, u)}
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
    if node.kind == "workflow":
        return f"{len(node.children)} agents" if node.children else "(no agent files)"
    if not node.has_transcript:
        return "(no transcript)"
    single = len(models) == 1 and (node.resolved_model is None or next(iter(models)) == node.resolved_model)
    if single:
        observed = next(iter(models))
        return f"{node.requested_model} → {observed}" if node.requested_model else observed
    return "ran " + ", ".join(f"{m} x{n}" for m, n in models.most_common())


def _usage_cells(u: Usage, calls: int, cost: float | None) -> str:
    return (f"{calls:>4} calls  out {fmt_tokens(u.output):>6}  read {fmt_tokens(u.cache_read):>6}"
            f"  write {fmt_tokens(u.cache_create):>6}  {fmt_usd(cost):>8}")


def _activity_cells(node: AgentNode) -> str:
    if node.kind == "workflow":
        return ""
    tools = node.tool_call_count
    return f"  {fmt_duration(node.duration_ms):>7}  {(str(tools) + ' tools') if tools is not None else '-':>9}"


def _tree_lines(node: AgentNode, flagged: set[str | None], policy: Policy, prefix: str = "", is_last: bool = True,
                is_root: bool = True) -> list[str]:
    connector = "" if is_root else ("└── " if is_last else "├── ")
    mark = "  !" if node.agent_id in flagged else ""
    orphan = "  (parent unknown)" if not node.parent_known else ""
    desc = node.description if node.depth else "main session"
    line = (f"{prefix}{connector}{desc[:40]:<40} {node.subagent_type:<15} {_model_column(node):<44} "
            f"{_usage_cells(node.usage(), len(node.calls), _cost_or_none(node, policy))}{_activity_cells(node)}{mark}{orphan}")
    lines = [line.rstrip()]
    child_prefix = "" if is_root else prefix + ("    " if is_last else "│   ")
    if node.failed_spawns:
        n = len(node.failed_spawns)
        bar = "│   " if node.children else "    "
        lines.append(f"{child_prefix}{bar}! {n} spawn attempt{'s' if n != 1 else ''} failed: "
                     f"{failure_summary(node.failed_spawns)}")
    for i, child in enumerate(node.children):
        lines += _tree_lines(child, flagged, policy, child_prefix, i == len(node.children) - 1, is_root=False)
    return lines


def _delta_note(ours: float, theirs: float) -> str:
    if theirs <= 0:
        return ""
    pct = (ours - theirs) / theirs * 100
    return f" (our estimate {'+' if pct >= 0 else ''}{pct:.0f}%)"


def _cost_or_none(node: AgentNode, policy: Policy) -> float | None:
    cost, unpriced = node_cost(node, policy)
    return None if unpriced and not cost else cost


def session_cost(nodes: list[AgentNode], policy: Policy) -> tuple[float, int]:
    total, unpriced = 0.0, 0
    for n in nodes:
        c, u = node_cost(n, policy)
        total += c
        unpriced += u
    return total, unpriced


def render_text(root: AgentNode, findings: list[Finding], policy: Policy, session_label: str) -> str:
    flagged = {f.agent_id for f in findings if f.agent_id}
    nodes = list(root.walk())
    max_depth = max(n.depth for n in nodes)
    out: list[str] = []
    out.append(f"agent-receipt · session {session_label}   {_time_range(root)}".rstrip())
    out.append("")
    out += _tree_lines(root, flagged, policy)
    out.append("")

    def totals_block(title: str, rows: dict[str, dict]) -> None:
        out.append(title)
        if not rows:
            out.append("  (none)")
        for model, r in rows.items():
            out.append(f"  {model:<24} {r['calls']:>5} calls  out {fmt_tokens(r['output']):>7}"
                       f"  read {fmt_tokens(r['cache_read']):>7}  write {fmt_tokens(r['cache_create']):>7}"
                       f"  {fmt_usd(r['cost']):>9}")

    totals_block("Totals by model (whole session):", _totals_by_model(nodes, policy))
    totals_block("Totals by model (subagents only):", _totals_by_model([n for n in nodes if n.depth], policy))
    cost, unpriced = session_cost(nodes, policy)
    sub_cost, _ = session_cost([n for n in nodes if n.depth], policy)
    unpriced_note = f" ({unpriced} calls on unpriced models)" if unpriced else ""
    main_cost = cost - sub_cost
    ratio = f" ({sub_cost / main_cost:.1f}x the main session)" if main_cost > 0 and sub_cost > 0 else ""
    out.append(f"Estimated cost: {fmt_usd(cost)} · main session {fmt_usd(main_cost)} · subagents {fmt_usd(sub_cost)}{ratio}{unpriced_note}")
    inherited_calls, inherited_children = carried_over(root)
    if root.continued_from:
        inherited = Usage()
        for c in inherited_calls:
            inherited = inherited + c.usage
        inh_cost = sum((policy.cost_of(c.model, c.usage) or 0.0) for c in inherited_calls)
        inh_cost += sum(session_cost(list(ch.walk()), policy)[0] for ch in inherited_children)
        out.append(f"Continues session {', '.join(s[:8] for s in root.continued_from)}: {len(inherited_calls)} calls, "
                   f"{len(inherited_children)} agents and {fmt_usd(inh_cost)} above were carried over from it "
                   f"(the digest counts them once)")
    if root.reported_cost_usd is not None:
        out.append(f"Claude Code's own figure for this session: {fmt_usd(root.reported_cost_usd)}"
                   f"{_delta_note(cost, root.reported_cost_usd)}; per model, ours vs Claude Code:")
        ours = _totals_by_model(nodes, policy)
        for model, theirs in sorted(root.reported_model_usage.items(), key=lambda kv: -float(kv[1].get("costUSD") or 0)):
            mine = ours.get(model, {}).get("cost")
            note = "" if model in ours else "  (never appears in the transcripts)"
            out.append(f"  {model:<28} {fmt_usd(mine if model in ours else 0.0):>9}  vs {fmt_usd(float(theirs.get('costUSD') or 0)):>9}{note}")
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


def _node_dict(node: AgentNode, policy: Policy) -> dict:
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
        "cost": _cost_or_none(node, policy),
        "kind": node.kind,
        "workflow_id": node.workflow_id,
        "duration_ms": node.duration_ms,
        "tool_calls": node.tool_call_count,
        "tools": dict(node.tool_calls),
        "tool_stats": node.tool_stats,
        "started": node.started,
        "ended": node.ended,
        "failed_spawns": [{"description": s.description, "subagent_type": s.subagent_type, "error": s.error}
                          for s in node.failed_spawns],
        "children": [_node_dict(c, policy) for c in node.children],
    }


def render_json(root: AgentNode, findings: list[Finding], policy: Policy, session_label: str) -> str:
    nodes = list(root.walk())
    cost, unpriced = session_cost(nodes, policy)
    sub_cost, _ = session_cost([n for n in nodes if n.depth], policy)
    data = {
        "session": session_label,
        "agents": root.subtree_agents(),
        "max_depth": max(n.depth for n in nodes),
        "failed_spawn_attempts": sum(len(n.failed_spawns) for n in nodes),
        "cost": cost,
        "main_cost": cost - sub_cost,
        "reported_cost": root.reported_cost_usd,
        "reported_model_usage": root.reported_model_usage,
        "continued_from": root.continued_from,
        "subagent_cost": sub_cost,
        "unpriced_calls": unpriced,
        "totals": _totals_by_model(nodes, policy),
        "subagent_totals": _totals_by_model([n for n in nodes if n.depth], policy),
        "findings": [asdict(f) for f in findings],
        "policy": asdict(policy),
        "tree": _node_dict(root, policy),
    }
    return json.dumps(data, indent=2)
