"""Command-line entry point: ``agent-receipt [SESSION] [--policy FILE] [--json] [--no-fail] [--hook]``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parse import parse_transcript
from .policy import evaluate, load_policy
from .report import render_json, render_text, session_cost
from .pricing import fmt_usd
from .digest import list_sessions, parse_since, render_digest_json, render_digest_text, summarize
from .parse import Transcript, WorkflowRun
from .session import (DEFAULT_CLAUDE_HOME, SessionFiles, find_agent_transcript, find_project_dir, read_meta,
                      resolve_session, session_files, workflow_agent_files)
from .tree import build_tree


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-receipt",
        description="A receipt for your Claude Code subagents: who spawned whom, which model actually ran, "
                    "what it cost in tokens, and which of your rules were broken.",
    )
    parser.add_argument("session", nargs="?",
                        help="path to a <session>.jsonl, a session id (prefix is enough), or nothing for the "
                             "latest session of the current directory")
    parser.add_argument("--policy", type=Path, help="TOML file with rules; defaults are used when omitted")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    parser.add_argument("--no-fail", action="store_true", help="exit 0 even when there are findings")
    parser.add_argument("--claude-home", type=Path, default=DEFAULT_CLAUDE_HOME,
                        help="Claude Code home directory (default: ~/.claude)")
    parser.add_argument("--since", metavar="WHEN",
                        help="digest of every session touched since WHEN across all projects: 7d, 36h, 2w or a date")
    parser.add_argument("--all", action="store_true", help="digest of every session on disk")
    parser.add_argument("--hook", action="store_true",
                        help="run as a Claude Code SessionEnd hook: read the hook JSON from stdin, write the "
                             "receipt to <claude-home>/agent-receipt/<session>.txt, print a one-line summary, exit 0")
    parser.add_argument("--print-hook-config", action="store_true",
                        help="print the settings.json snippet that registers the SessionEnd hook")
    return parser


LoadedSession = tuple[Transcript, list[Transcript], list[tuple[WorkflowRun, list[tuple[Transcript, dict]]]]]


def load_session(files: SessionFiles) -> LoadedSession:
    """Parse the main transcript, every subagent it (transitively) spawned, and the agents of
    every Workflow run. Children whose files live under a sibling session directory are
    followed too (a continued session keeps its old id's directory)."""
    main = parse_transcript(files.main)
    subagents = [parse_transcript(p) for p in files.subagents]
    known = {t.agent_id for t in subagents if t.agent_id}
    workflows: list[tuple[WorkflowRun, list[tuple[Transcript, dict]]]] = []
    seen_runs: set[str] = set()
    while True:
        for t in [main, *subagents, *[a for _, pairs in workflows for a, _ in pairs]]:
            for run in t.workflows:
                if run.run_id is None or run.run_id in seen_runs:
                    continue
                seen_runs.add(run.run_id)
                pairs = []
                for path in workflow_agent_files(files.main, run.run_id, run.transcript_dir):
                    agent = parse_transcript(path)
                    if agent.agent_id:
                        known.add(agent.agent_id)
                    pairs.append((agent, read_meta(path)))
                workflows.append((run, pairs))
        everyone = [main, *subagents, *[a for _, pairs in workflows for a, _ in pairs]]
        wanted = {s.child_agent_id for t in everyone for s in t.spawns
                  if s.child_agent_id and s.error is None} - known
        found = [(aid, find_agent_transcript(files.main, aid)) for aid in sorted(wanted)]
        found = [(aid, p) for aid, p in found if p is not None]
        if not found and all(r.run_id is None or r.run_id in seen_runs for t in everyone for r in t.workflows):
            return main, subagents, workflows
        for aid, path in found:
            known.add(aid)
            subagents.append(parse_transcript(path))


def run_digest(args, policy) -> int:
    try:
        since = parse_since(args.since) if args.since else None
    except ValueError:
        print(f"agent-receipt: cannot read --since {args.since!r}; use 7d, 36h, 2w or YYYY-MM-DD", file=sys.stderr)
        return 2
    rows = []
    for path in list_sessions(args.claude_home, since):
        files = session_files(path)
        main, subagents, workflows = load_session(files)
        root = build_tree(main, subagents, workflows)
        rows.append(summarize(root, evaluate(root, policy), policy, path,
                              title=main.title or main.first_prompt, cwd=main.cwd))
        if main.continued_from:
            rows[-1].title += " (continued)"
    rows.sort(key=lambda r: r.started, reverse=True)
    label = f"since {since.date().isoformat()} ({args.since})" if since else "(all sessions)"
    render = render_digest_json if args.json else render_digest_text
    sys.stdout.write(render(rows, policy, label))
    return 0 if (args.no_fail or not any(r.findings for r in rows)) else 1


HOOK_CONFIG = {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "agent-receipt --hook"}]}]}}


def run_hook(args, stdin) -> int:
    """Hooks must never block a session: any failure is reported on stderr and exit is 0."""
    try:
        payload = json.loads(stdin.read() or "{}")
        main_path = Path(payload["transcript_path"])
        policy = load_policy(args.policy)
        files = session_files(main_path)
        root = build_tree(*load_session(files))
        findings = evaluate(root, policy)
        out_dir = Path(args.claude_home) / "agent-receipt"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{files.session_id}.txt"
        out.write_text(render_text(root, findings, policy, session_label=files.session_id))
        cost, _ = session_cost(list(root.walk()), policy)
        print(f"agent-receipt: {root.subtree_agents()} agents · {fmt_usd(cost)} · "
              f"{len(findings)} finding{'s' if len(findings) != 1 else ''} → {out}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - a hook must not take the session down
        print(f"agent-receipt hook: {exc}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.print_hook_config:
        print(json.dumps(HOOK_CONFIG, indent=2))
        return 0
    if args.hook:
        return run_hook(args, sys.stdin)
    try:
        policy = load_policy(args.policy)
    except (ValueError, OSError) as exc:
        print(f"agent-receipt: bad policy file: {exc}", file=sys.stderr)
        return 2
    if args.since or args.all:
        return run_digest(args, policy)
    try:
        main_path = resolve_session(args.session, cwd=Path.cwd(), claude_home=args.claude_home)
    except FileNotFoundError as exc:
        fallback = list_sessions(args.claude_home, None)[:1] if not args.session else []
        if not fallback:
            print(f"agent-receipt: {exc}", file=sys.stderr)
            return 2
        main_path = fallback[0]
        print(f"agent-receipt: no session for this directory; showing the latest one on disk ({main_path.stem[:8]})",
              file=sys.stderr)

    files = session_files(main_path)
    root = build_tree(*load_session(files))
    findings = evaluate(root, policy)
    render = render_json if args.json else render_text
    sys.stdout.write(render(root, findings, policy, session_label=files.session_id))
    return 0 if (not findings or args.no_fail) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
