"""Assemble parsed transcripts into a parent/child tree of agents."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterator

from .parse import Call, Spawn, Transcript, Usage, WorkflowRun

_MISSING = object()


@dataclass
class AgentNode:
    agent_id: str | None                 # None = the main session (root)
    description: str = "main session"
    subagent_type: str = "-"
    requested_model: str | None = None
    resolved_model: str | None = None
    depth: int = 0
    has_transcript: bool = True
    parent_known: bool = True
    kind: str = "agent"                  # agent | workflow | workflow-agent
    workflow_id: str | None = None
    spawn_duration_ms: int | None = None # reported by the launcher when the child finished
    spawn_tool_calls: int | None = None
    tool_stats: dict[str, int] = field(default_factory=dict)
    tool_calls: Counter = field(default_factory=Counter)   # from the agent's own transcript
    calls: list[Call] = field(default_factory=list)
    children: list["AgentNode"] = field(default_factory=list)
    failed_spawns: list[Spawn] = field(default_factory=list)
    reported_cost_usd: float | None = None   # root only: Claude Code's own cost-state figure
    reported_model_usage: dict[str, dict] = field(default_factory=dict)   # root only
    session_id: str | None = None            # root only
    continued_from: list[str] = field(default_factory=list)   # root only
    spawn_session_id: str | None = None      # session id of the line that launched this node

    def models(self) -> Counter:
        return Counter(call.model for call in self.calls)

    def usage(self) -> Usage:
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total

    def subtree_usage(self) -> Usage:
        total = self.usage()
        for child in self.children:
            total = total + child.subtree_usage()
        return total

    def subtree_agents(self) -> int:
        """Agents below this node; a workflow container is not itself an agent."""
        return sum((0 if child.kind == "workflow" else 1) + child.subtree_agents() for child in self.children)

    def walk(self) -> Iterator["AgentNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def started(self) -> str:
        stamps = [c.timestamp for c in self.calls if c.timestamp]
        return min(stamps) if stamps else ""

    @property
    def ended(self) -> str:
        stamps = [c.timestamp for c in self.calls if c.timestamp]
        return max(stamps) if stamps else ""

    @property
    def duration_ms(self) -> int | None:
        """Launcher-reported wall clock when available, else the span of the agent's own calls."""
        if self.spawn_duration_ms is not None:
            return self.spawn_duration_ms
        return _span_ms(self.started, self.ended)

    @property
    def tool_call_count(self) -> int | None:
        if self.tool_calls:
            return sum(self.tool_calls.values())
        return self.spawn_tool_calls


def _span_ms(start: str, end: str) -> int | None:
    if not start or not end:
        return None
    from datetime import datetime
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def failure_summary(spawns: list[Spawn]) -> str:
    """'reason (x3), other reason (x1)' with the most common reason first."""
    reasons = Counter((s.error or "unknown error") for s in spawns)
    parts = []
    for reason, n in reasons.most_common():
        short = reason if len(reason) <= 90 else reason[:89] + "…"
        parts.append(f"{short} (x{n})")
    return ", ".join(parts)


def _child_key(spawn: Spawn) -> str:
    return spawn.child_agent_id or f"unresolved:{spawn.tool_use_id}"


def _resolve_claims(spawns: list[Spawn], settled: set[str] = frozenset()) -> dict[str, Spawn]:
    """Decide, for every spawned child, the single spawn record that really created it.

    Two kinds of duplicate exist. A fork copies its parent's history, so it re-states the
    parent's spawn records (including its own launch and its siblings'); those copies are
    dropped because the claimant is itself a child of another claimant. And a copy taken
    before the result arrived has no child id; it is dropped when a resolved copy of the
    same tool use exists anywhere (``settled`` adds tool uses known to have failed).
    """
    resolved_tool_uses = {s.tool_use_id for s in spawns if s.child_agent_id} | set(settled)
    candidates: dict[str, list[Spawn]] = defaultdict(list)
    for spawn in spawns:
        if spawn.child_agent_id is None and spawn.tool_use_id in resolved_tool_uses:
            continue
        if spawn.child_agent_id is not None and spawn.child_agent_id == spawn.parent_agent_id:
            continue
        candidates[_child_key(spawn)].append(spawn)

    claims_of_parent: dict[str | None, set[str]] = defaultdict(set)
    for key, group in candidates.items():
        for spawn in group:
            claims_of_parent[spawn.parent_agent_id].add(key)

    chosen: dict[str, Spawn] = {}
    for key, group in candidates.items():
        claimants = {s.parent_agent_id for s in group}
        kept = [
            s for s in group
            if not any(other != s.parent_agent_id and s.parent_agent_id in claims_of_parent[other]
                       for other in claimants)
        ]
        pool = kept or group
        pool.sort(key=lambda s: (s.timestamp, s.tool_use_id))
        chosen[key] = pool[0]
    return chosen


def _is_descendant(agent: str | None, ancestor: str | None, parent_of: dict[str, str | None]) -> bool:
    seen: set[str] = set()
    while agent is not None and agent not in seen:
        seen.add(agent)
        parent = parent_of.get(agent, _MISSING)
        if parent is _MISSING:
            return False
        if parent == ancestor:
            return True
        agent = parent
    return False


def _assign_failed(failed: list[Spawn], parent_of: dict[str, str | None]) -> dict[str | None, list[Spawn]]:
    """Credit each failed tool use once, to the claimant that is not descended from another
    claimant (a fork re-states its parent's failed attempts too)."""
    by_tool_use: dict[str, list[Spawn]] = defaultdict(list)
    for s in failed:
        by_tool_use[s.tool_use_id].append(s)
    result: dict[str | None, list[Spawn]] = defaultdict(list)
    for group in by_tool_use.values():
        claimants = {s.parent_agent_id for s in group}
        kept = [s for s in group
                if not any(other != s.parent_agent_id and _is_descendant(s.parent_agent_id, other, parent_of)
                           for other in claimants)]
        pool = kept or group
        pool.sort(key=lambda s: (s.timestamp, s.tool_use_id))
        result[pool[0].parent_agent_id].append(pool[0])
    for group in result.values():
        group.sort(key=lambda s: (s.timestamp, s.tool_use_id))
    return result


def _node_from_transcript(node: AgentNode, transcript: Transcript | None) -> AgentNode:
    if transcript is not None:
        node.calls = list(transcript.calls)
        node.tool_calls = Counter(transcript.tool_calls)
    return node


def carried_over(root: AgentNode) -> tuple[list[Call], list[AgentNode]]:
    """Main calls and top-level children that a continued session inherited from an earlier one."""
    own = root.session_id
    if not own or not root.continued_from:
        return [], []
    calls = [c for c in root.calls if c.session_id and c.session_id != own]
    children = [c for c in root.children if c.spawn_session_id and c.spawn_session_id != own]
    return calls, children


def prune_carried_over(root: AgentNode) -> AgentNode:
    """A copy of the tree with only what this session itself did (for multi-session totals)."""
    from copy import copy
    calls, children = carried_over(root)
    if not calls and not children:
        return root
    pruned = copy(root)
    drop = {id(c) for c in calls}
    pruned.calls = [c for c in root.calls if id(c) not in drop]
    dropped = {id(c) for c in children}
    pruned.children = [c for c in root.children if id(c) not in dropped]
    return pruned


def build_tree(main: Transcript, subagents: list[Transcript],
               workflows: list[tuple[WorkflowRun, list[tuple[Transcript, dict]]]] | None = None) -> AgentNode:
    """``workflows`` pairs each Workflow run with its agents' (transcript, meta.json) list."""
    workflows = workflows or []
    wf_agents = [t for _, pairs in workflows for t, _ in pairs]
    by_id: dict[str, Transcript] = {t.agent_id: t for t in [*subagents, *wf_agents] if t.agent_id}
    all_spawns = list(main.spawns) + [s for t in [*subagents, *wf_agents] for s in t.spawns]
    failed = [s for s in all_spawns if s.error is not None]
    chosen = _resolve_claims([s for s in all_spawns if s.error is None], settled={s.tool_use_id for s in failed})
    parent_of = {key: spawn.parent_agent_id for key, spawn in chosen.items()}
    for run, pairs in workflows:
        for t, _ in pairs:
            if t.agent_id:
                parent_of.setdefault(t.agent_id, run.parent_agent_id)
    failed_of = _assign_failed(failed, parent_of)

    children_of: dict[str | None, list[tuple[str, Spawn]]] = defaultdict(list)
    for key, spawn in chosen.items():
        children_of[spawn.parent_agent_id].append((key, spawn))
    for group in children_of.values():
        group.sort(key=lambda item: (item[1].timestamp, item[1].tool_use_id))
    workflows_of: dict[str | None, list] = defaultdict(list)
    for run, pairs in workflows:
        workflows_of[run.parent_agent_id].append((run, pairs))

    root = AgentNode(agent_id=None, calls=list(main.calls), tool_calls=Counter(main.tool_calls),
                     failed_spawns=list(failed_of.get(None, [])), reported_cost_usd=main.reported_cost_usd,
                     reported_model_usage=dict(main.reported_model_usage), session_id=main.session_id, continued_from=list(main.continued_from))
    visited: set[str] = set()

    def attach(node: AgentNode) -> None:
        for key, spawn in children_of.get(node.agent_id, []):
            if key in visited:
                continue
            visited.add(key)
            transcript = by_id.get(key)
            child = AgentNode(
                agent_id=spawn.child_agent_id,
                description=spawn.description or "(no description)",
                subagent_type=spawn.subagent_type,
                requested_model=spawn.requested_model,
                resolved_model=spawn.resolved_model,
                depth=node.depth + 1,
                has_transcript=transcript is not None,
                spawn_duration_ms=spawn.duration_ms,
                spawn_tool_calls=spawn.tool_calls,
                tool_stats=dict(spawn.tool_stats),
                spawn_session_id=spawn.session_id,
                failed_spawns=list(failed_of.get(spawn.child_agent_id, [])) if spawn.child_agent_id else [],
            )
            node.children.append(_node_from_transcript(child, transcript))
            attach(child)
        for run, pairs in workflows_of.get(node.agent_id, []):
            wf = AgentNode(
                agent_id=run.run_id or f"workflow:{run.tool_use_id}",
                description=f"workflow {run.name}".strip() if run.name else (run.description or "workflow"),
                subagent_type="workflow",
                depth=node.depth + 1,
                kind="workflow",
                workflow_id=run.run_id,
                has_transcript=bool(pairs),
                spawn_session_id=run.session_id,
            )
            node.children.append(wf)
            for transcript, meta in pairs:
                if transcript.agent_id:
                    visited.add(transcript.agent_id)
                agent = AgentNode(
                    agent_id=transcript.agent_id,
                    description=str(meta.get("description") or transcript.first_prompt or run.description or "(workflow agent)"),
                    subagent_type=str(meta.get("agentType") or "workflow-subagent"),
                    requested_model=meta.get("model") if isinstance(meta.get("model"), str) else None,
                    depth=wf.depth,                   # same spawn depth as the workflow itself
                    kind="workflow-agent",
                    workflow_id=run.run_id,
                    failed_spawns=list(failed_of.get(transcript.agent_id, [])) if transcript.agent_id else [],
                )
                wf.children.append(_node_from_transcript(agent, transcript))
                attach(agent)

    attach(root)

    for agent_id, transcript in by_id.items():
        if agent_id in visited:
            continue
        visited.add(agent_id)
        orphan = AgentNode(
            agent_id=agent_id,
            description="(parent unknown)",
            depth=1,
            parent_known=False,
            failed_spawns=list(failed_of.get(agent_id, [])),
        )
        root.children.append(_node_from_transcript(orphan, transcript))
        attach(orphan)
    return root
