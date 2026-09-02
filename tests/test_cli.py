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


def test_hook_mode_writes_receipt_and_never_fails(tmp_path, capsys, monkeypatch):
    import io
    path = _session(tmp_path, child_model="claude-fable-5-1")   # has a finding
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(path), "hook_event_name": "SessionEnd"})))
    assert main(["--hook", "--claude-home", str(tmp_path / "home")]) == 0
    err = capsys.readouterr().err
    assert "1 agents" in err and "$" in err and "2 findings" in err
    written = tmp_path / "home" / "agent-receipt" / f"{path.stem}.txt"
    assert written.exists() and "heavy-model" in written.read_text()


def test_hook_mode_swallows_bad_input(tmp_path, capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["--hook", "--claude-home", str(tmp_path)]) == 0
    assert "agent-receipt hook:" in capsys.readouterr().err


def test_print_hook_config_is_valid_json(capsys):
    assert main(["--print-hook-config"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == "agent-receipt --hook"


def test_child_transcript_under_a_sibling_session_dir_is_found(tmp_path, capsys):
    """A continued session keeps spawning into the previous session id's directory."""
    new_sid, old_sid = "22222222-2222-2222-2222-222222222222", "11111111-1111-1111-1111-111111111111"
    write_jsonl(tmp_path / f"{new_sid}.jsonl", [
        assistant_line("m1", "claude-fable-5-1", usage(out=10), content=[agent_tool_use("tu1", "worker", model="sonnet")]),
        agent_result_line("tu1", "child001", resolved_model="claude-sonnet-5")])
    write_jsonl(tmp_path / old_sid / "subagents" / "agent-child001.jsonl",
                [assistant_line("c1", "claude-sonnet-5", usage(out=5), agent_id="child001",
                                content=[agent_tool_use("tu2", "grandchild", model="sonnet")]),
                 agent_result_line("tu2", "child002", resolved_model="claude-sonnet-5", agent_id="child001")])
    write_jsonl(tmp_path / old_sid / "subagents" / "agent-child002.jsonl",
                [assistant_line("g1", "claude-sonnet-5", usage(out=5), agent_id="child002")])
    main([str(tmp_path / f"{new_sid}.jsonl"), "--json", "--no-fail"])
    data = json.loads(capsys.readouterr().out)
    assert data["agents"] == 2
    (child,) = data["tree"]["children"]
    assert child["has_transcript"] and child["children"][0]["has_transcript"]
    assert not any(f["rule"] == "missing-transcript" for f in data["findings"])


def test_workflow_agents_are_loaded_from_the_run_directory(tmp_path, capsys):
    from helpers import meta_json, workflow_result_line, workflow_tool_use
    sid = "33333333-3333-3333-3333-333333333333"
    write_jsonl(tmp_path / f"{sid}.jsonl", [
        assistant_line("m1", "claude-fable-5-1", usage(out=10), content=[workflow_tool_use("tw1", "fan out")]),
        workflow_result_line("tw1", "wf_run-1", name="fanout")])
    d = tmp_path / sid / "subagents" / "workflows" / "wf_run-1"
    for aid, model in (("w1", "claude-sonnet-5"), ("w2", "claude-opus-5")):
        write_jsonl(d / f"agent-{aid}.jsonl", [assistant_line(f"{aid}-1", model, usage(out=5), agent_id=aid)])
        meta_json(d / f"agent-{aid}.meta.json", agentType="workflow-subagent", spawnDepth=1, model="sonnet")
    code = main([str(tmp_path / f"{sid}.jsonl"), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["agents"] == 2
    (wf,) = data["tree"]["children"]
    assert wf["kind"] == "workflow" and wf["workflow_id"] == "wf_run-1"
    assert {c["agent_id"]: c["requested_model"] for c in wf["children"]} == {"w1": "sonnet", "w2": "sonnet"}
    assert [f["rule"] for f in data["findings"]] == ["heavy-model"]     # w2 ran opus although sonnet was asked
