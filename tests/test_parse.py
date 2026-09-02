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
