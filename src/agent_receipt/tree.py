"""Assemble parsed transcripts into a parent/child tree of agents."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterator

from .parse import Call, Spawn, Transcript, Usage


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
    calls: list[Call] = field(default_factory=list)
    children: list["AgentNode"] = field(default_factory=list)
    failed_spawns: list[Spawn] = field(default_factory=list)   # Agent calls that returned an error

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
        return sum(1 + child.subtree_agents() for child in self.children)

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


_MISSING = object()


def failure_summary(spawns: list[Spawn], width: int = 90) -> str:
    """'reason (x3), other reason (x1)' with the most frequent reason first."""
    counts = Counter(s.error or "unknown error" for s in spawns)
    parts = []
    for reason, n in counts.most_common():
        reason = reason if len(reason) <= width else reason[: width - 1] + "…"
        parts.append(f"{reason} (x{n})")
    return ", ".join(parts)


def _child_key(spawn: Spawn) -> str:
    return spawn.child_agent_id or f"unresolved:{spawn.tool_use_id}"


def _resolve_claims(spawns: list[Spawn], settled: set[str] = frozenset()) -> dict[str, Spawn]:
    """Decide, for every spawned child, the single spawn record that really created it.

    Two kinds of duplicate exist. A fork copies its parent's history, so it re-states the
    parent's spawn records (including its own launch and its siblings'); those copies are
    dropped because the claimant is itself a child of another claimant. And a copy taken
    before the result arrived has no child id; it is dropped when a resolved copy of the
    same tool use exists anywhere, or when that tool use is known to have failed (``settled``).
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
        # one record per parent is enough; earliest wins
        pool.sort(key=lambda s: (s.timestamp, s.tool_use_id))
        chosen[key] = pool[0]
    return chosen


def _is_descendant(agent: str | None, ancestor: str | None, parent_of: dict[str, str | None]) -> bool:
    seen: set[str | None] = set()
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
    """Credit every failed Agent call to exactly one parent.

    A fork inherits its parent's history, so the fork's file re-states the parent's failed
    attempts under the fork's own id. Among the claimants of one tool use, any claimant that
    descends from another claimant is a copy and is dropped.
    """
    by_tool_use: dict[str, list[Spawn]] = defaultdict(list)
    for spawn in failed:
        by_tool_use[spawn.tool_use_id].append(spawn)
    credited: dict[str | None, list[Spawn]] = defaultdict(list)
    for group in by_tool_use.values():
        claimants = {s.parent_agent_id for s in group}
        kept = [
            s for s in group
            if not any(other != s.parent_agent_id and _is_descendant(s.parent_agent_id, other, parent_of)
                       for other in claimants)
        ]
        pool = kept or group
        pool.sort(key=lambda s: (s.timestamp, s.tool_use_id))
        credited[pool[0].parent_agent_id].append(pool[0])
    for group in credited.values():
        group.sort(key=lambda s: (s.timestamp, s.tool_use_id))
    return credited


def build_tree(main: Transcript, subagents: list[Transcript]) -> AgentNode:
    by_id: dict[str, Transcript] = {t.agent_id: t for t in subagents if t.agent_id}
    all_spawns = list(main.spawns) + [s for t in subagents for s in t.spawns]
    failed = [s for s in all_spawns if s.error is not None]
    chosen = _resolve_claims([s for s in all_spawns if s.error is None],
                             settled={s.tool_use_id for s in failed})
    failed_of = _assign_failed(failed, {key: spawn.parent_agent_id for key, spawn in chosen.items()})

    children_of: dict[str | None, list[tuple[str, Spawn]]] = defaultdict(list)
    for key, spawn in chosen.items():
        children_of[spawn.parent_agent_id].append((key, spawn))
    for group in children_of.values():
        group.sort(key=lambda item: (item[1].timestamp, item[1].tool_use_id))

    root = AgentNode(agent_id=None, calls=list(main.calls), failed_spawns=list(failed_of.get(None, [])))
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
                calls=list(transcript.calls) if transcript else [],
                failed_spawns=list(failed_of.get(spawn.child_agent_id, [])) if spawn.child_agent_id else [],
            )
            node.children.append(child)
            attach(child)

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
            calls=list(transcript.calls),
            failed_spawns=list(failed_of.get(agent_id, [])),
        )
        root.children.append(orphan)
        attach(orphan)
    return root
