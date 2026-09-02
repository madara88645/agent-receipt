"""USD price per million tokens, matched to model ids with fnmatch patterns (first match wins).

Source: https://platform.claude.com/docs/en/about-claude/pricing (read 2026-09-02). Cache
writes are priced at the 5-minute rate, which is what Claude Code uses. Override or extend
the table from the policy file with a ``[prices."<pattern>"]`` section.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from .parse import Usage


@dataclass(frozen=True)
class Price:
    input: float
    cache_write: float
    cache_read: float
    output: float

    def cost(self, u: Usage) -> float:
        return (u.input * self.input + u.cache_create * self.cache_write
                + u.cache_read * self.cache_read + u.output * self.output) / 1_000_000


DEFAULT_PRICES: list[tuple[str, Price]] = [
    ("claude-fable-5-1*", Price(10, 12.5, 0.25, 50)),
    ("claude-mythos-5-1*", Price(10, 12.5, 0.25, 50)),
    ("claude-fable-5*", Price(10, 12.5, 1, 50)),
    ("claude-mythos-5*", Price(10, 12.5, 1, 50)),
    ("claude-opus-4-1*", Price(15, 18.75, 1.5, 75)),
    ("claude-opus-4-2025*", Price(15, 18.75, 1.5, 75)),
    ("claude-opus-*", Price(5, 6.25, 0.5, 25)),
    ("claude-sonnet-5*", Price(2, 2.5, 0.2, 10)),
    ("claude-sonnet-*", Price(3, 3.75, 0.3, 15)),
    ("claude-haiku-3-5*", Price(0.8, 1, 0.08, 4)),
    ("claude-haiku-*", Price(1, 1.25, 0.1, 5)),
]


def price_for(model: str, overrides: dict[str, dict] | None = None) -> Price | None:
    for pattern, raw in (overrides or {}).items():
        if fnmatch(model, pattern):
            return Price(float(raw.get("input", 0)), float(raw.get("cache_write", 0)),
                         float(raw.get("cache_read", 0)), float(raw.get("output", 0)))
    for pattern, price in DEFAULT_PRICES:
        if fnmatch(model, pattern):
            return price
    return None


def fmt_usd(x: float | None) -> str:
    if x is None:
        return "$?"
    return f"${x:,.2f}" if x >= 0.1 or x == 0 else f"${x:.3f}"
