import json

from agent_receipt.cli import main
from helpers import agent_result_line, agent_tool_use, assistant_line, usage, write_jsonl


def _session(tmp_path, child_model="claude-sonnet-5"):
    sid = "11111111-2222-3333-4444-555555555555"
    main_lines = [
        assistant_line("m1", "claude-fable-5-1", usage(out=10),
                       content=[agent_tool_use("tu1", "worker", model="sonnet")]),
        agent_result_line("tu1", "child001", resolved_model="claude-sonnet-5"),
    ]
    write_jsonl(tmp_path / f"{sid}.jsonl", main_lines)
    write_jsonl(tmp_path / sid / "subagents" / "agent-child001.jsonl",
                [assistant_line("c1", child_model, usage(out=5), agent_id="child001")])
    return tmp_path / f"{sid}.jsonl"


def test_clean_session_exits_zero_and_prints_tree(tmp_path, capsys):
    code = main([str(_session(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0
    assert "worker" in out and "no findings" in out.lower()


def test_violating_session_exits_one_unless_no_fail(tmp_path, capsys):
    path = _session(tmp_path, child_model="claude-fable-5-1")
    assert main([str(path)]) == 1
    assert "heavy-model" in capsys.readouterr().out
    assert main([str(path), "--no-fail"]) == 0


def test_json_flag_prints_json(tmp_path, capsys):
    main([str(_session(tmp_path)), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["agents"] == 1


def test_policy_file_is_honoured(tmp_path, capsys):
    path = _session(tmp_path, child_model="claude-fable-5-1")
    policy = tmp_path / "p.toml"
    policy.write_text('cheap_models = ["claude-*"]\nflag_resolved_mismatch = false\n')
    assert main([str(path), "--policy", str(policy)]) == 0


def test_missing_session_is_a_usage_error(tmp_path, capsys):
    code = main([str(tmp_path / "nope.jsonl"), "--claude-home", str(tmp_path)])
    assert code == 2
    assert "not found" in capsys.readouterr().err.lower()
