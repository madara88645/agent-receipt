"""Rules a subagent tree is checked against, and the findings they produce."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from fnmatch import fnmatch
from pathlib import Path

from .tree import AgentNode, failure_summary


@dataclass
class Policy:
    cheap_models: list[str] = field(default_factory=lambda: ["claude-sonnet-*", "claude-haiku-*"])
    max_depth: int = 1
    max_agents: int = 0                     # 0 = no limit
    flag_model_switch: bool = True
    flag_resolved_mismatch: bool = True
    flag_missing_transcript: bool = True
    flag_failed_spawns: bool = True

    def is_cheap(self, model: str) -> bool:
        return any(fnmatch(model, pattern) for pattern in self.cheap_models)


@dataclass(frozen=True)
class Finding:
    rule: str
    agent_id: str | None
    message: str


def load_policy(path: Path | str | None) -> Policy:
    if path is None:
        return Policy()
    with Path(path).open("rb") as fh:
        raw = tomllib.load(fh)
    known = {f.name for f in fields(Policy)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"unknown policy key(s): {', '.join(unknown)}; known keys: {', '.join(sorted(known))}")
    return Policy(**raw)


def _label(node: AgentNode) -> str:
    if node.agent_id:
        return node.agent_id[:8]
    return "main" if node.depth == 0 else f"({node.description})"


def evaluate(root: AgentNode, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    for node in root.walk():
        if policy.flag_failed_spawns and node.failed_spawns:
            n = len(node.failed_spawns)
            findings.append(Finding(
                "failed-spawn", node.agent_id,
                f"{_label(node)}: {n} spawn attempt{'s' if n != 1 else ''} failed: "
                f"{failure_summary(node.failed_spawns)}"))
        if node.depth == 0:
            continue
        models = node.models()

        heavy = {m: n for m, n in models.items() if not policy.is_cheap(m)}
        for model, count in sorted(heavy.items()):
            findings.append(Finding(
                "heavy-model", node.agent_id,
                f"{_label(node)}: {count} calls on {model} (allowed: {', '.join(policy.cheap_models)})"))

        if node.depth > policy.max_depth:
            findings.append(Finding(
                "nested-spawn", node.agent_id,
                f"{_label(node)}: at depth {node.depth}, limit is {policy.max_depth}"))

        if policy.flag_model_switch and len(models) > 1:
            listing = ", ".join(f"{m} x{n}" for m, n in models.most_common())
            findings.append(Finding(
                "model-switch", node.agent_id,
                f"{_label(node)}: used {len(models)} models in one run: {listing}"))

        if policy.flag_resolved_mismatch and node.resolved_model:
            off = sum(n for m, n in models.items() if m != node.resolved_model)
            if off:
                findings.append(Finding(
                    "resolved-mismatch", node.agent_id,
                    f"{_label(node)}: resolved to {node.resolved_model} but {off} calls ran on another model"))

        if policy.flag_missing_transcript and not node.has_transcript:
            findings.append(Finding(
                "missing-transcript", node.agent_id,
                f"{_label(node)}: spawned but no transcript file was found"))

    total = root.subtree_agents()
    if policy.max_agents and total > policy.max_agents:
        findings.append(Finding(
            "too-many-agents", None, f"{total} agents were spawned, limit is {policy.max_agents}"))
    return findings
