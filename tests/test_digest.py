import json
from datetime import datetime, timezone

from agent_receipt.cli import main
from agent_receipt.digest import parse_since
from helpers import agent_result_line, agent_tool_use, assistant_line, usage, write_jsonl


def _session(home, project, sid, ts, child_model="claude-sonnet-5", title=None):
    d = home / "projects" / project
    lines = [{"type": "custom-title", "customTitle": title}] if title else []
    lines += [dict(assistant_line("m1", "claude-fable-5-1", usage(out=1000), ts=ts,
                                  content=[agent_tool_use("tu1", "worker", model="sonnet")]), cwd=f"/Users/x/{project}"),
              agent_result_line("tu1", f"c{sid}", resolved_model="claude-sonnet-5", ts=ts)]
    write_jsonl(d / f"{sid}.jsonl", lines)
    write_jsonl(d / sid / "subagents" / f"agent-c{sid}.jsonl",
                [assistant_line("c1", child_model, usage(out=100_000), agent_id=f"c{sid}", ts=ts)])


def test_parse_since_accepts_durations_and_dates():
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    assert parse_since("7d", now) == datetime(2026, 8, 26, tzinfo=timezone.utc)
    assert parse_since("36h", now) == datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    assert parse_since("2w", now) == datetime(2026, 8, 19, tzinfo=timezone.utc)
    assert parse_since("2026-08-01").isoformat() == "2026-08-01T00:00:00+00:00"


def test_digest_folds_sessions_across_projects(tmp_path, capsys):
    _session(tmp_path, "proj-a", "aaaa1111", "2026-09-01T10:00:00.000Z", title="Refactor auth")
    _session(tmp_path, "proj-b", "bbbb2222", "2026-09-02T10:00:00.000Z", child_model="claude-opus-5")
    code = main(["--all", "--claude-home", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1                                        # opus child → heavy-model finding
    assert "2 sessions" in out and "Refactor auth" in out and "proj-b" in out
    assert out.index("2026-09-02") < out.index("2026-09-01")   # newest first
    assert "heavy-model 1" in out and "Subagents by type:" in out and "general-purpose" in out
    assert "Most expensive sessions:" in out


def test_digest_json_and_since_filter(tmp_path, capsys):
    _session(tmp_path, "proj-a", "aaaa1111", "2026-09-01T10:00:00.000Z")
    import os, time
    old = tmp_path / "projects" / "proj-a" / "aaaa1111.jsonl"
    os.utime(old, (time.time() - 40 * 86400, time.time() - 40 * 86400))
    _session(tmp_path, "proj-a", "cccc3333", "2026-09-02T10:00:00.000Z")
    assert main(["--since", "7d", "--json", "--claude-home", str(tmp_path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [s["session"] for s in data["sessions"]] == ["cccc3333"]
    assert data["agents"] == 1 and data["total_cost"] > data["subagent_cost"] > 0


def test_bad_since_is_a_usage_error(tmp_path, capsys):
    assert main(["--since", "yesterday", "--claude-home", str(tmp_path)]) == 2
    assert "--since" in capsys.readouterr().err


def test_continued_session_is_not_double_counted_in_the_digest(tmp_path, capsys):
    old, new = "aaaa1111-0000-0000-0000-000000000000", "bbbb2222-0000-0000-0000-000000000000"
    d = tmp_path / "projects" / "proj-a"
    old_lines = [dict(assistant_line("m1", "claude-fable-5-1", usage(out=100_000), ts="2026-09-01T10:00:00.000Z",
                                     content=[agent_tool_use("tu1", "worker", model="sonnet")]), sessionId=old),
                 dict(agent_result_line("tu1", "kid", resolved_model="claude-sonnet-5"), sessionId=old)]
    write_jsonl(d / f"{old}.jsonl", old_lines)
    write_jsonl(d / old / "subagents" / "agent-kid.jsonl", [assistant_line("k1", "claude-sonnet-5", usage(out=100_000), agent_id="kid")])
    new_lines = old_lines + [dict(assistant_line("m2", "claude-fable-5-1", usage(out=100_000), ts="2026-09-01T11:00:00.000Z"), sessionId=new)]
    write_jsonl(d / f"{new}.jsonl", new_lines)
    main(["--all", "--json", "--no-fail", "--claude-home", str(tmp_path)])
    data = json.loads(capsys.readouterr().out)
    rows = {s["session"]: s for s in data["sessions"]}
    assert rows[old]["agents"] == 1 and rows[old]["main_cost"] == 5.0 and rows[old]["subagent_cost"] == 1.0
    assert rows[new]["agents"] == 0 and rows[new]["main_cost"] == 5.0 and rows[new]["subagent_cost"] == 0
    assert data["total_cost"] == 11.0 and rows[new]["title"].endswith("(continued)")
    # the single-session receipt still shows the whole story, but says what was inherited
    main([str(d / f"{new}.jsonl"), "--no-fail"])
    text = capsys.readouterr().out
    assert "Continues session aaaa1111: 1 calls, 1 agents and $6.00" in text
