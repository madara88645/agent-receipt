from agent_receipt.parse import parse_transcript
from helpers import agent_result_line, agent_tool_use, assistant_line, usage, write_jsonl


def test_blocks_of_one_api_call_collapse_into_one_call_with_final_usage(tmp_path):
    # one API response streamed as three blocks; output_tokens grows block by block
    lines = [
        assistant_line("msg_1", "claude-sonnet-5", usage(inp=2, read=100, out=5), block_index=0),
        assistant_line("msg_1", "claude-sonnet-5", usage(inp=2, read=100, out=40), block_index=1),
        assistant_line("msg_1", "claude-sonnet-5", usage(inp=2, read=100, out=90), block_index=2),
        assistant_line("msg_2", "claude-sonnet-5", usage(inp=1, create=30, read=150, out=10)),
    ]
    t = parse_transcript(write_jsonl(tmp_path / "a.jsonl", lines))

    assert [c.message_id for c in t.calls] == ["msg_1", "msg_2"]
    assert t.calls[0].usage.output == 90
    assert t.calls[0].usage.cache_read == 100
    assert t.total_usage().output == 100
    assert t.total_usage().cache_read == 250


def test_transcript_agent_id_comes_from_lines_and_main_session_has_none(tmp_path):
    sub = parse_transcript(write_jsonl(tmp_path / "sub.jsonl",
                                       [assistant_line("m", "claude-sonnet-5", usage(), agent_id="abc123")]))
    main = parse_transcript(write_jsonl(tmp_path / "main.jsonl",
                                        [assistant_line("m", "claude-fable-5-1", usage())]))
    assert sub.agent_id == "abc123"
    assert main.agent_id is None


def test_spawn_records_pair_tool_use_with_result_and_resolved_model(tmp_path):
    lines = [
        assistant_line("m1", "claude-sonnet-5", usage(),
                       content=[agent_tool_use("tu_1", "Research A", model="sonnet")]),
        agent_result_line("tu_1", "child0001", resolved_model="claude-sonnet-5"),
        assistant_line("m2", "claude-sonnet-5", usage(),
                       content=[agent_tool_use("tu_2", "Fork B", subagent_type="fork")]),
        agent_result_line("tu_2", "child0002", resolved_model=None),
    ]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))

    assert len(t.spawns) == 2
    a, b = t.spawns
    assert (a.child_agent_id, a.description, a.requested_model, a.resolved_model) == \
        ("child0001", "Research A", "sonnet", "claude-sonnet-5")
    assert (b.child_agent_id, b.subagent_type, b.requested_model, b.resolved_model) == \
        ("child0002", "fork", None, None)


def test_spawn_child_id_falls_back_to_result_text_when_structured_field_missing(tmp_path):
    lines = [
        assistant_line("m1", "claude-sonnet-5", usage(), content=[agent_tool_use("tu_1", "X")]),
        agent_result_line("tu_1", "deadbeef01", with_structured=False),
    ]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))
    assert t.spawns[0].child_agent_id == "deadbeef01"


def test_spawn_without_result_is_kept_with_unknown_child(tmp_path):
    lines = [assistant_line("m1", "claude-sonnet-5", usage(), content=[agent_tool_use("tu_9", "Lost")])]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))
    assert t.spawns[0].child_agent_id is None


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"type":"assistant"\nnot json at all\n' +
                 __import__("json").dumps(assistant_line("m", "claude-sonnet-5", usage(out=3))) + "\n")
    t = parse_transcript(p)
    assert t.total_usage().output == 3


def test_string_tool_use_result_does_not_crash_and_falls_back_to_text(tmp_path):
    line = agent_result_line("tu_1", "cafe0001", with_structured=False)
    line["toolUseResult"] = "Error: agent failed to launch"  # some tools store a plain string here
    lines = [assistant_line("m1", "claude-sonnet-5", usage(), content=[agent_tool_use("tu_1", "X")]), line]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))
    assert t.spawns[0].child_agent_id == "cafe0001"
    assert t.spawns[0].resolved_model is None


def test_error_result_marks_spawn_as_failed_with_reason(tmp_path):
    line = agent_result_line("tu_1", "ignored", with_structured=False)
    line["message"]["content"][0]["is_error"] = True
    line["message"]["content"][0]["content"] = "Error: Concurrent subagent limit reached. You can run 20 subagents at once."
    line["toolUseResult"] = "Error: Concurrent subagent limit reached. You can run 20 subagents at once."
    lines = [assistant_line("m1", "claude-sonnet-5", usage(), content=[agent_tool_use("tu_1", "X")]), line]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))
    s = t.spawns[0]
    assert s.child_agent_id is None
    assert s.error.startswith("Concurrent subagent limit reached")


def test_synthetic_placeholder_messages_are_not_calls(tmp_path):
    lines = [assistant_line("m1", "claude-sonnet-5", usage(out=5)),
             assistant_line("s1", "<synthetic>", usage())]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))
    assert [c.model for c in t.calls] == ["claude-sonnet-5"]


def test_finished_agent_result_gives_duration_and_tool_counts(tmp_path):
    lines = [assistant_line("m1", "claude-sonnet-5", usage(), content=[agent_tool_use("tu_1", "X")]),
             agent_result_line("tu_1", "kid", resolved_model="claude-sonnet-5",
                               finished=dict(duration_ms=4200, tool_calls=7, tool_stats={"readCount": 5, "bashCount": 2}))]
    s = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines)).spawns[0]
    assert (s.status, s.duration_ms, s.tool_calls, s.tool_stats) == ("completed", 4200, 7, {"readCount": 5, "bashCount": 2})


def test_workflow_tool_use_is_recorded_with_run_id_and_dir(tmp_path):
    from helpers import workflow_result_line, workflow_tool_use
    lines = [assistant_line("m1", "claude-fable-5-1", usage(), content=[workflow_tool_use("tw1", "review PRs")]),
             workflow_result_line("tw1", "wf_abc-123", name="review", transcript_dir="/x/y")]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))
    (run,) = t.workflows
    assert (run.run_id, run.name, run.description, run.transcript_dir, run.tool_use_id) == \
        ("wf_abc-123", "review", "review PRs", "/x/y", "tw1")
    assert t.spawns == [] and t.tool_calls == {"Workflow": 1}


def test_tool_calls_title_cwd_and_cost_state_are_collected(tmp_path):
    lines = [
        {"type": "custom-title", "customTitle": "My session", "sessionId": "s"},
        {"type": "cost-state", "totalCostUSD": 1.5, "modelUsage": {"claude-sonnet-5": {"costUSD": 1.5}}},
        dict(assistant_line("m1", "claude-sonnet-5", usage(), content=[
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "Read", "input": {}},
            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {}}]), cwd="/tmp/proj", gitBranch="main", version="2.1.0"),
    ]
    t = parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines))
    assert t.title == "My session" and t.cwd == "/tmp/proj" and t.git_branch == "main" and t.version == "2.1.0"
    assert t.tool_calls == {"Bash": 2, "Read": 1}
    assert t.reported_cost_usd == 1.5 and "claude-sonnet-5" in t.reported_model_usage


def test_continued_session_marks_lines_carried_over_from_the_old_session(tmp_path):
    old, new = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    lines = [dict(assistant_line("m1", "claude-fable-5-1", usage(out=5)), sessionId=old),
             dict(assistant_line("m2", "claude-fable-5-1", usage(out=5)), sessionId=new)]
    t = parse_transcript(write_jsonl(tmp_path / f"{new}.jsonl", lines))
    assert t.session_id == new and t.continued_from == [old]
    assert [c.session_id for c in t.calls] == [old, new]


def test_slash_commands_are_not_used_as_the_first_prompt(tmp_path):
    lines = [{"type": "user", "message": {"role": "user", "content": "/model"}},
             {"type": "user", "message": {"role": "user", "content": "Refactor the auth module please"}}]
    assert parse_transcript(write_jsonl(tmp_path / "p.jsonl", lines)).first_prompt == "Refactor the auth module please"
