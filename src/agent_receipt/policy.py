"""Rules a subagent tree is checked against, and the findings they produce."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from fnmatch import fnmatch
from pathlib import Path

from .parse import Usage
from .pricing import Price, fmt_usd, price_for
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
    prices: dict[str, dict] = field(default_factory=dict)   # [prices."pattern"] input/cache_write/cache_read/output
    max_agent_cost: float = 0.0             # USD per subagent, 0 = no limit
    max_session_cost: float = 0.0           # USD for the whole session, 0 = no limit

    def price_for(self, model: str) -> Price | None:
        return price_for(model, self.prices)

    def cost_of(self, model: str, usage: Usage) -> float | None:
        price = self.price_for(model)
        return price.cost(usage) if price else None

    def is_cheap(self, model: str) -> bool:
        return any(fnmatch(model, pattern) for pattern in self.cheap_models)


@dataclass(frozen=True)
class Finding:
    rule: str
    agent_id: str | None
    message: str


_VALUE_TYPES: dict[str, tuple[type, ...]] = {
    "cheap_models": (list,),
    "max_depth": (int,),
    "max_agents": (int,),
    "flag_model_switch": (bool,),
    "flag_resolved_mismatch": (bool,),
    "flag_missing_transcript": (bool,),
    "flag_failed_spawns": (bool,),
    "prices": (dict,),
    "max_agent_cost": (int, float),
    "max_session_cost": (int, float),
}
_PRICE_KEYS = ("input", "cache_write", "cache_read", "output")


def _check_type(key: str, value: object, expected: tuple[type, ...]) -> None:
    """A policy file is hand-written, so a wrong type is a usage error, not a crash."""
    wants_bool = expected == (bool,)
    if isinstance(value, bool) != wants_bool or not isinstance(value, expected):
        names = " or ".join(t.__name__ for t in expected)
        raise ValueError(f"key {key!r} must be {names}, got {type(value).__name__}")


def _check(raw: dict) -> None:
    known = {f.name for f in fields(Policy)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"unknown policy key(s): {', '.join(unknown)}; known keys: {', '.join(sorted(known))}")
    for key, value in raw.items():
        _check_type(key, value, _VALUE_TYPES[key])
    for i, pattern in enumerate(raw.get("cheap_models", [])):
        _check_type(f"cheap_models[{i}]", pattern, (str,))
    for pattern, table in raw.get("prices", {}).items():
        _check_type(f'prices."{pattern}"', table, (dict,))
        for key, value in table.items():
            if key not in _PRICE_KEYS:
                raise ValueError(f'unknown key {key!r} in [prices."{pattern}"]; '
                                 f"known keys: {', '.join(_PRICE_KEYS)}")
            _check_type(f'prices."{pattern}".{key}', value, (int, float))


def load_policy(path: Path | str | None) -> Policy:
    if path is None:
        return Policy()
    with Path(path).open("rb") as fh:
        raw = tomllib.load(fh)
    _check(raw)
    return Policy(**raw)


def _label(node: AgentNode) -> str:
    if node.agent_id:
        # agent ids are long opaque handles and are cut down for readability; a workflow
        # container is identified by its run id, which has to stay whole to be matched
        # against the run directory on disk
        return node.agent_id if node.kind == "workflow" else node.agent_id[:8]
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

    if policy.max_agent_cost:
        for node in root.walk():
            if node.depth == 0 or node.kind == "workflow":
                continue
            cost = sum((policy.cost_of(c.model, c.usage) or 0.0) for c in node.calls)
            if cost > policy.max_agent_cost:
                findings.append(Finding(
                    "over-budget", node.agent_id,
                    f"{_label(node)}: cost {fmt_usd(cost)} exceeds the per-agent budget of "
                    f"{fmt_usd(policy.max_agent_cost)}"))
    if policy.max_session_cost:
        session = sum((policy.cost_of(c.model, c.usage) or 0.0) for n in root.walk() for c in n.calls)
        if session > policy.max_session_cost:
            findings.append(Finding(
                "over-budget", None,
                f"session cost {fmt_usd(session)} exceeds the budget of {fmt_usd(policy.max_session_cost)}"))
    total = root.subtree_agents()
    if policy.max_agents and total > policy.max_agents:
        findings.append(Finding(
            "too-many-agents", None, f"{total} agents were spawned, limit is {policy.max_agents}"))
    return findings
