import pytest

from agent_receipt.parse import Call, Usage
from agent_receipt.policy import Policy, evaluate, load_policy
from agent_receipt.tree import AgentNode


def _call(model, agent_id="x", i=0):
    return Call(agent_id=agent_id, message_id=f"{agent_id}-{i}", model=model, timestamp="", usage=Usage(output=1))


def _node(agent_id, model="claude-sonnet-5", n=2, depth=1, **kw):
    return AgentNode(agent_id=agent_id, depth=depth, calls=[_call(model, agent_id, i) for i in range(n)], **kw)


def _root(*children):
    root = AgentNode(agent_id=None, calls=[_call("claude-fable-5-1", "main")])
    root.children.extend(children)
    return root


def _rules(findings):
    return sorted(f.rule for f in findings)


def test_clean_tree_has_no_findings():
    assert evaluate(_root(_node("a"), _node("b", model="claude-haiku-4-5")), Policy()) == []


def test_heavy_model_in_subagent_is_flagged_but_main_session_is_exempt():
    findings = evaluate(_root(_node("a", model="claude-fable-5-1")), Policy())
    assert _rules(findings) == ["heavy-model"]
    assert findings[0].agent_id == "a"
    assert "claude-fable-5-1" in findings[0].message and "2 calls" in findings[0].message


def test_nested_spawn_beyond_max_depth_is_flagged():
    grand = _node("g", depth=2)
    child = _node("c")
    child.children.append(grand)
    assert _rules(evaluate(_root(child), Policy())) == ["nested-spawn"]
    assert evaluate(_root(child), Policy(max_depth=2)) == []


def test_model_switch_inside_one_agent_is_flagged_with_counts():
    node = _node("f", n=0, resolved_model=None)
    node.calls = [_call("claude-sonnet-5", "f", 0), _call("claude-fable-5-1", "f", 1), _call("claude-fable-5-1", "f", 2)]
    findings = [f for f in evaluate(_root(node), Policy()) if f.rule == "model-switch"]
    assert len(findings) == 1
    assert "claude-sonnet-5 x1" in findings[0].message and "claude-fable-5-1 x2" in findings[0].message


def test_resolved_model_mismatch_is_flagged():
    node = _node("f", model="claude-fable-5-1", n=3, resolved_model="claude-sonnet-5")
    findings = [f for f in evaluate(_root(node), Policy(cheap_models=["claude-*"])) if f.rule == "resolved-mismatch"]
    assert len(findings) == 1
    assert "claude-sonnet-5" in findings[0].message and "3 calls" in findings[0].message


def test_missing_transcript_is_flagged():
    ghost = AgentNode(agent_id="ghost", depth=1, has_transcript=False)
    assert _rules(evaluate(_root(ghost), Policy())) == ["missing-transcript"]
    assert evaluate(_root(ghost), Policy(flag_missing_transcript=False)) == []


def test_too_many_agents_is_flagged_only_when_limit_set():
    root = _root(_node("a"), _node("b"), _node("c"))
    assert evaluate(root, Policy()) == []
    findings = evaluate(root, Policy(max_agents=2))
    assert _rules(findings) == ["too-many-agents"] and "3" in findings[0].message


def test_load_policy_reads_toml_and_keeps_defaults_for_missing_keys(tmp_path):
    p = tmp_path / "policy.toml"
    p.write_text('cheap_models = ["claude-haiku-*"]\nmax_depth = 3\n')
    policy = load_policy(p)
    assert policy.cheap_models == ["claude-haiku-*"]
    assert policy.max_depth == 3
    assert policy.flag_model_switch is True


def test_load_policy_rejects_unknown_keys(tmp_path):
    p = tmp_path / "policy.toml"
    p.write_text("max_dept = 3\n")
    with pytest.raises(ValueError, match="max_dept"):
        load_policy(p)


def test_load_policy_without_path_returns_defaults():
    assert load_policy(None) == Policy()


@pytest.mark.parametrize("body, match", [
    ('max_depth = "two"', "max_depth"),
    ("max_depth = true", "max_depth"),
    ('max_agent_cost = "1"', "max_agent_cost"),
    ("flag_failed_spawns = 1", "flag_failed_spawns"),
    ('cheap_models = "claude-sonnet-5"', "cheap_models"),
    ("cheap_models = [1, 2]", r"cheap_models\[0\]"),
    ("prices = 5", "prices"),
    ('[prices."claude-opus-*"]\ninput = "abc"', "input"),
    ('[prices."claude-opus-*"]\ncache_writes = 1', "cache_writes"),
])
def test_load_policy_rejects_wrongly_typed_values(tmp_path, body, match):
    """A hand-written policy file used to crash deep inside a rule instead of exiting 2."""
    p = tmp_path / "policy.toml"
    p.write_text(body + "\n")
    with pytest.raises(ValueError, match=match):
        load_policy(p)


def test_workflow_container_findings_keep_the_whole_run_id():
    wf = AgentNode(agent_id="wf_run-12345678", depth=1, kind="workflow", has_transcript=False)
    (finding,) = evaluate(_root(wf), Policy())
    assert "wf_run-12345678" in finding.message


def test_budget_findings_do_not_round_small_amounts_to_zero():
    node = _node("a", model="claude-sonnet-5", n=1)
    node.calls[0].usage = Usage(output=1_000)             # $0.01 on sonnet-5
    findings = evaluate(_root(node), Policy(max_agent_cost=0.001))
    assert [f.rule for f in findings] == ["over-budget"]
    assert "$0.010" in findings[0].message and "$0.001" in findings[0].message


def test_failed_spawns_are_flagged_with_count_and_reason():
    from agent_receipt.parse import Spawn
    node = _node("p")
    node.failed_spawns = [Spawn("p", f"tu{i}", "w", "general-purpose", None, "", error="Concurrent subagent limit reached. Foo")
                          for i in range(3)]
    findings = evaluate(_root(node), Policy())
    assert _rules(findings) == ["failed-spawn"]
    assert "3" in findings[0].message and "Concurrent subagent limit reached" in findings[0].message
    assert evaluate(_root(node), Policy(flag_failed_spawns=False)) == []


def test_unresolved_spawn_finding_does_not_pretend_to_be_main():
    ghost = AgentNode(agent_id=None, depth=1, has_transcript=False, description="lost")
    (finding,) = evaluate(_root(ghost), Policy())
    assert finding.rule == "missing-transcript" and "main" not in finding.message


def test_budget_rules_flag_expensive_agents_and_sessions():
    big = _node("big", model="claude-sonnet-5", n=1)
    big.calls[0].usage = Usage(output=1_000_000)          # $10 on sonnet-5
    small = _node("small")
    assert _rules(evaluate(_root(big, small), Policy())) == []
    findings = evaluate(_root(big, small), Policy(max_agent_cost=5, max_session_cost=1))
    assert _rules(findings) == ["over-budget", "over-budget"]
    assert findings[0].agent_id == "big" and "$10.00" in findings[0].message
    assert findings[1].agent_id is None and "session cost" in findings[1].message
