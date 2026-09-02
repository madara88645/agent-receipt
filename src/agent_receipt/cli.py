"""Command-line entry point: ``agent-receipt [SESSION] [--policy FILE] [--json] [--no-fail]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .parse import parse_transcript
from .policy import evaluate, load_policy
from .report import render_json, render_text
from .session import DEFAULT_CLAUDE_HOME, resolve_session, session_files
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    root = build_tree(parse_transcript(files.main), [parse_transcript(p) for p in files.subagents])
    findings = evaluate(root, policy)
    render = render_json if args.json else render_text
    sys.stdout.write(render(root, findings, policy, session_label=files.session_id))
    return 0 if (not findings or args.no_fail) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
