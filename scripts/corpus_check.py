"""Run agent-receipt over every Claude Code session on this machine and print anonymous
statistics: file counts, crashes, timing, agents found, findings by rule, and how our cost
estimate compares with Claude Code's own ``cost-state`` figure where one exists.

Nothing from the transcripts is printed except counts and dollar totals. Use it to
reproduce the numbers in docs/evidence.md on your own data:

    uv run python scripts/corpus_check.py
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))


def main() -> int:
    files = sorted((HOME / "projects").glob("*/*.jsonl"))
    ok = crash = 0
    rules: collections.Counter = collections.Counter()
    kinds: collections.Counter = collections.Counter()
    types: collections.Counter = collections.Counter()
    errors: collections.Counter = collections.Counter()
    sessions_with_agents = sessions_with_workflows = continued = 0
    total_cost = 0.0
    biggest_mb = 0.0
    slowest = 0.0
    cross: list[tuple[float, float]] = []
    t0 = time.time()
    for f in files:
        t = time.time()
        r = subprocess.run([sys.executable, "-m", "agent_receipt", str(f), "--json", "--no-fail"],
                           capture_output=True, text=True)
        slowest = max(slowest, time.time() - t)
        biggest_mb = max(biggest_mb, f.stat().st_size / 1e6)
        if r.returncode != 0 or not r.stdout.strip():
            crash += 1
            errors[(r.stderr.strip().splitlines() or ["?"])[-1][:100]] += 1
            continue
        ok += 1
        d = json.loads(r.stdout)
        total_cost += d["cost"]
        for fi in d["findings"]:
            rules[fi["rule"]] += 1

        def walk(n):
            yield n
            for c in n["children"]:
                yield from walk(c)
        nodes = list(walk(d["tree"]))[1:]
        for n in nodes:
            kinds[n["kind"]] += 1
            if n["kind"] != "workflow":
                types[n["subagent_type"]] += 1
        if d["agents"]:
            sessions_with_agents += 1
        if any(n["kind"] == "workflow" for n in nodes):
            sessions_with_workflows += 1
        if d.get("continued_from"):
            continued += 1
        if d.get("reported_cost"):
            cross.append((d["cost"], d["reported_cost"]))
    print(f"sessions: {len(files)}  parsed: {ok}  crashed: {crash}  wall: {time.time() - t0:.1f}s  "
          f"slowest: {slowest:.2f}s  largest file: {biggest_mb:.0f} MB")
    if errors:
        print("errors:", dict(errors))
    print(f"sessions with subagents: {sessions_with_agents}  with Workflow runs: {sessions_with_workflows}  "
          f"continued sessions: {continued}")
    print(f"agents found: {sum(v for k, v in kinds.items() if k != 'workflow')}  "
          f"(workflow-launched: {kinds['workflow-agent']}, workflow runs: {kinds['workflow']})")
    print("subagent types:", dict(types.most_common()))
    print("findings by rule:", dict(rules.most_common()))
    print(f"estimated list-price cost of everything: ${total_cost:,.2f}")
    if cross:
        print("cross-check vs Claude Code's own cost-state (whole session; theirs includes calls absent from transcripts):")
        for ours, theirs in cross:
            pct = (ours - theirs) / theirs * 100 if theirs else float("nan")
            print(f"  ours ${ours:.2f}  claude code ${theirs:.2f}  delta {pct:+.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
