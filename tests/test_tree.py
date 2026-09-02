from agent_receipt.parse import parse_transcript
from agent_receipt.tree import build_tree
from helpers import agent_result_line, agent_tool_use, assistant_line, usage, write_jsonl


def _main_with_children(tmp_path, children):
    """main session spawning `children` = [(tool_use_id, child_id, requested, resolved, type)]"""
    lines = []
    for i, (tu, cid, req, res, typ) in enumerate(children):
        lines.append(assistant_line(f"m{i}", "claude-fable-5-1", usage(out=10),
                                    content=[agent_tool_use(tu, f"job {cid}", subagent_type=typ, model=req)]))
        lines.append(agent_result_line(tu, cid, resolved_model=res))
    return parse_transcript(write_jsonl(tmp_path / "main.jsonl", lines))


def _sub(tmp_path, agent_id, model="claude-sonnet-5", n_calls=2, extra_lines=()):
    lines = [assistant_line(f"{agent_id}-m{i}", model, usage(out=7, read=50), agent_id=agent_id)
             for i in range(n_calls)]
    lines.extend(extra_lines)
    return parse_transcript(write_jsonl(tmp_path / "subagents" / f"agent-{agent_id}.jsonl", lines))


def test_children_attach_to_parent_with_depth_and_spawn_metadata(tmp_path):
    main = _main_with_children(tmp_path, [("tu1", "aaa", "sonnet", "claude-sonnet-5", "general-purpose")])
    child = _sub(tmp_path, "aaa", extra_lines=[
        assistant_line("aaa-x", "claude-sonnet-5", usage(), agent_id="aaa",
                       content=[agent_tool_use("tu2", "grandchild", subagent_type="fork")]),
        agent_result_line("tu2", "bbb", resolved_model="claude-sonnet-5", agent_id="aaa"),
    ])
    grand = _sub(tmp_path, "bbb", n_calls=1)

    root = build_tree(main, [child, grand])

    assert root.agent_id is None and root.depth == 0
    (a,) = root.children
    assert (a.agent_id, a.depth, a.description, a.requested_model, a.resolved_model) == \
        ("aaa", 1, "job aaa", "sonnet", "claude-sonnet-5")
    (b,) = a.children
    assert (b.agent_id, b.depth, b.subagent_type) == ("bbb", 2, "fork")


def test_fork_inherited_spawn_records_are_not_counted_as_its_own_children(tmp_path):
    # parent P spawns forks F1 and F2. Each fork's transcript carries a copy of P's history,
    # so F1's file also "claims" F1 and F2. Only P may be their parent.
    main = _main_with_children(tmp_path, [("tu0", "ppp", "sonnet", "claude-sonnet-5", "general-purpose")])
    spawn_block = assistant_line("ppp-s", "claude-sonnet-5", usage(), agent_id="ppp", content=[
        agent_tool_use("tuF1", "fork one", subagent_type="fork"),
        agent_tool_use("tuF2", "fork two", subagent_type="fork"),
    ])
    results = [agent_result_line("tuF1", "fff1", resolved_model="claude-sonnet-5", agent_id="ppp"),
               agent_result_line("tuF2", "fff2", resolved_model="claude-sonnet-5", agent_id="ppp")]
    p = _sub(tmp_path, "ppp", extra_lines=[spawn_block] + results)
    inherited = [dict(spawn_block, agentId="fff1")] + [dict(r, agentId="fff1") for r in results]
    f1 = _sub(tmp_path, "fff1", extra_lines=inherited)
    f2 = _sub(tmp_path, "fff2", extra_lines=[dict(spawn_block, agentId="fff2")])  # results not yet copied

    root = build_tree(main, [p, f1, f2])

    (pn,) = root.children
    assert sorted(c.agent_id for c in pn.children) == ["fff1", "fff2"]
    assert all(c.children == [] for c in pn.children)
    assert root.subtree_agents() == 3


def test_claimed_child_without_transcript_still_appears(tmp_path):
    main = _main_with_children(tmp_path, [("tu1", "ghost", "sonnet", "claude-sonnet-5", "general-purpose")])
    root = build_tree(main, [])
    (g,) = root.children
    assert g.agent_id == "ghost" and g.has_transcript is False and g.calls == []


def test_orphan_transcript_attaches_to_root_and_is_marked(tmp_path):
    main = _main_with_children(tmp_path, [])
    orphan = _sub(tmp_path, "zzz")
    root = build_tree(main, [orphan])
    (z,) = root.children
    assert z.agent_id == "zzz" and z.parent_known is False and z.depth == 1


def test_node_aggregates_models_and_usage_over_subtree(tmp_path):
    main = _main_with_children(tmp_path, [("tu1", "aaa", "sonnet", "claude-sonnet-5", "general-purpose")])
    child = _sub(tmp_path, "aaa", n_calls=3, extra_lines=[
        assistant_line("aaa-f", "claude-fable-5-1", usage(out=100), agent_id="aaa")])
    root = build_tree(main, [child])
    (a,) = root.children

    assert a.models() == {"claude-sonnet-5": 3, "claude-fable-5-1": 1}
    assert a.usage().output == 3 * 7 + 100
    assert root.usage().output == 10            # main's own call
    assert root.subtree_usage().output == 10 + 121
    assert root.subtree_agents() == 1


def _failed_result(tu, reason, agent_id):
    line = agent_result_line(tu, "x", with_structured=False, agent_id=agent_id)
    line["message"]["content"][0]["is_error"] = True
    line["message"]["content"][0]["content"] = f"Error: {reason}"
    line["toolUseResult"] = f"Error: {reason}"
    return line


def test_failed_spawn_attempts_are_recorded_on_the_parent_not_as_children(tmp_path):
    main = _main_with_children(tmp_path, [("tu0", "ppp", "sonnet", "claude-sonnet-5", "general-purpose")])
    p = _sub(tmp_path, "ppp", extra_lines=[
        assistant_line("ppp-s", "claude-sonnet-5", usage(), agent_id="ppp", content=[
            agent_tool_use("tuA", "worker A"), agent_tool_use("tuB", "worker B"), agent_tool_use("tuC", "worker C")]),
        agent_result_line("tuA", "aaa", resolved_model="claude-sonnet-5", agent_id="ppp"),
        _failed_result("tuB", "Concurrent subagent limit reached. You can run 20 subagents at once.", "ppp"),
        _failed_result("tuC", "Concurrent subagent limit reached. You can run 20 subagents at once.", "ppp"),
    ])
    a = _sub(tmp_path, "aaa")
    root = build_tree(main, [p, a])
    (pn,) = root.children
    assert [c.agent_id for c in pn.children] == ["aaa"]
    assert len(pn.failed_spawns) == 2
    assert pn.failed_spawns[0].error.startswith("Concurrent subagent limit")
    assert root.subtree_agents() == 2


def test_fork_own_launch_copy_and_its_failed_refork_attempts_do_not_become_children(tmp_path):
    main = _main_with_children(tmp_path, [("tu0", "ppp", "sonnet", "claude-sonnet-5", "general-purpose")])
    launch = assistant_line("ppp-s", "claude-sonnet-5", usage(), agent_id="ppp",
                            content=[agent_tool_use("tuF1", "fork one", subagent_type="fork")])
    p = _sub(tmp_path, "ppp", extra_lines=[launch, agent_result_line("tuF1", "fff1", resolved_model="claude-sonnet-5", agent_id="ppp")])
    f1 = _sub(tmp_path, "fff1", extra_lines=[
        dict(launch, agentId="fff1"),                                   # copy of its own launch, no result
        assistant_line("fff1-r", "claude-sonnet-5", usage(), agent_id="fff1",
                       content=[agent_tool_use("tuF2", "fork two", subagent_type="fork")]),
        _failed_result("tuF2", "Fork is not available inside a forked worker.", "fff1"),
    ])
    root = build_tree(main, [p, f1])
    (pn,) = root.children
    (fn,) = pn.children
    assert fn.agent_id == "fff1" and fn.children == []
    assert len(fn.failed_spawns) == 1


def test_fork_copies_of_the_parents_failed_attempts_stay_on_the_parent(tmp_path):
    # P spawns fork F1 and worker X; X fails. F1 inherits P's history, so F1's file also
    # carries the failed X record. It must be counted once, on P, never on F1.
    main = _main_with_children(tmp_path, [("tu0", "ppp", "sonnet", "claude-sonnet-5", "general-purpose")])
    spawn_block = assistant_line("ppp-s", "claude-sonnet-5", usage(), agent_id="ppp", content=[
        agent_tool_use("tuF1", "fork one", subagent_type="fork"), agent_tool_use("tuX", "worker X")])
    ok = agent_result_line("tuF1", "fff1", resolved_model="claude-sonnet-5", agent_id="ppp")
    bad = _failed_result("tuX", "Concurrent subagent limit reached.", "ppp")
    p = _sub(tmp_path, "ppp", extra_lines=[spawn_block, ok, bad])
    f1 = _sub(tmp_path, "fff1", extra_lines=[dict(spawn_block, agentId="fff1"), dict(ok, agentId="fff1"),
                                             dict(bad, agentId="fff1")])
    root = build_tree(main, [p, f1])
    (pn,) = root.children
    (fn,) = pn.children
    assert len(pn.failed_spawns) == 1 and fn.failed_spawns == []
    assert root.subtree_agents() == 2
