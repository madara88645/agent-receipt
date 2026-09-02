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
from .parse import Transcript
from .session import DEFAULT_CLAUDE_HOME, SessionFiles, find_agent_transcript, resolve_session, session_files
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
    parser.add_argument("--hook", action="store_true",
                        help="run as a Claude Code SessionEnd hook: read the hook JSON from stdin, write the "
                             "receipt to <claude-home>/agent-receipt/<session>.txt, print a one-line summary, exit 0")
    parser.add_argument("--print-hook-config", action="store_true",
                        help="print the settings.json snippet that registers the SessionEnd hook")
    return parser


def load_session(files: SessionFiles) -> tuple[Transcript, list[Transcript]]:
    """Parse the main transcript and every subagent it (transitively) spawned, following
    children whose files live under a sibling session directory."""
    main = parse_transcript(files.main)
    subagents = [parse_transcript(p) for p in files.subagents]
    known = {t.agent_id for t in subagents if t.agent_id}
    while True:
        wanted = {s.child_agent_id for t in [main, *subagents] for s in t.spawns
                  if s.child_agent_id and s.error is None} - known
        found = [(aid, find_agent_transcript(files.main, aid)) for aid in sorted(wanted)]
        found = [(aid, p) for aid, p in found if p is not None]
        if not found:
            return main, subagents
        for aid, path in found:
            known.add(aid)
            subagents.append(parse_transcript(path))


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
        main_path = resolve_session(args.session, cwd=Path.cwd(), claude_home=args.claude_home)
    except FileNotFoundError as exc:
        print(f"agent-receipt: {exc}", file=sys.stderr)
        return 2
    try:
        policy = load_policy(args.policy)
    except (ValueError, OSError) as exc:
        print(f"agent-receipt: bad policy file: {exc}", file=sys.stderr)
        return 2

    files = session_files(main_path)
    root = build_tree(*load_session(files))
    findings = evaluate(root, policy)
    render = render_json if args.json else render_text
    sys.stdout.write(render(root, findings, policy, session_label=files.session_id))
    return 0 if (not findings or args.no_fail) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
