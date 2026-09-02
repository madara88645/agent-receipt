import json

from agent_receipt.parse import Call, Usage
from agent_receipt.policy import Finding, Policy
from agent_receipt.report import fmt_tokens, render_json, render_text
from agent_receipt.tree import AgentNode


def _call(model, agent_id, i=0, out=100, read=1000):
    return Call(agent_id=agent_id, message_id=f"{agent_id}-{i}", model=model,
                timestamp=f"2026-09-01T18:0{i}:00.000Z", usage=Usage(cache_read=read, output=out))


def _tree():
    root = AgentNode(agent_id=None, calls=[_call("claude-fable-5-1", "main", out=2000)])
    a = AgentNode(agent_id="aaaa1111bbbb", description="Mine pain points", subagent_type="general-purpose",
                  requested_model="sonnet", resolved_model="claude-sonnet-5", depth=1,
                  calls=[_call("claude-sonnet-5", "a", i) for i in range(3)])
    f = AgentNode(agent_id="ffff2222cccc", description="Stickiness A", subagent_type="fork",
                  resolved_model="claude-sonnet-5", depth=2,
                  calls=[_call("claude-sonnet-5", "f", 0), _call("claude-fable-5-1", "f", 1, out=5000)])
    a.children.append(f)
    root.children.append(a)
    return root


FINDINGS = [Finding("heavy-model", "ffff2222cccc", "ffff2222: 1 calls on claude-fable-5-1 (allowed: x)"),
            Finding("nested-spawn", "ffff2222cccc", "ffff2222: at depth 2, limit is 1")]


def test_fmt_tokens_is_compact():
    assert [fmt_tokens(n) for n in (0, 999, 1000, 45120685, 2194838)] == ["0", "999", "1.0k", "45.1M", "2.2M"]


def test_text_report_shows_tree_models_totals_and_findings():
    text = render_text(_tree(), FINDINGS, Policy(), session_label="be176aa4")
    assert "be176aa4" in text
    assert "Mine pain points" in text and "Stickiness A" in text
    assert "sonnet" in text and "claude-fable-5-1" in text
    assert "└──" in text or "├──" in text          # tree drawing
    assert "heavy-model" in text and "nested-spawn" in text
    assert "2 finding" in text
    assert "claude-fable-5-1" in text.split("Totals")[1]  # totals section lists the heavy model


def test_text_report_marks_flagged_agents_inline():
    text = render_text(_tree(), FINDINGS, Policy(), session_label="s")
    flagged_line = next(line for line in text.splitlines() if "Stickiness A" in line)
    clean_line = next(line for line in text.splitlines() if "Mine pain points" in line)
    assert "!" in flagged_line and "!" not in clean_line


def test_clean_report_says_so():
    text = render_text(_tree(), [], Policy(), session_label="s")
    assert "no findings" in text.lower()


def test_json_report_is_machine_readable():
    data = json.loads(render_json(_tree(), FINDINGS, Policy(), session_label="be176aa4"))
    assert data["session"] == "be176aa4"
    assert data["agents"] == 2
    assert data["findings"][0]["rule"] == "heavy-model"
    assert data["totals"]["claude-fable-5-1"]["output"] == 2000 + 5000
    assert data["subagent_totals"]["claude-fable-5-1"]["output"] == 5000
    fork = data["tree"]["children"][0]["children"][0]
    assert fork["subagent_type"] == "fork" and fork["models"] == {"claude-sonnet-5": 1, "claude-fable-5-1": 1}


def test_text_report_groups_findings_by_rule_and_caps_examples():
    many = [Finding("nested-spawn", f"a{i:07d}", f"a{i:07d}: at depth 2, limit is 1") for i in range(12)]
    text = render_text(_tree(), many + FINDINGS[:1], Policy(), session_label="s")
    tail = text.split("finding")[-1]
    assert "nested-spawn (12)" in text and "heavy-model (1)" in text
    assert tail.count("at depth 2") <= 6 and "more" in tail


def test_text_report_shows_failed_spawn_attempts_compactly():
    from agent_receipt.parse import Spawn
    tree = _tree()
    tree.children[0].failed_spawns = [Spawn("a", f"tu{i}", "w", "fork", None, "", error="Fork is not available inside a forked worker.")
                                      for i in range(5)]
    text = render_text(tree, [], Policy(), session_label="s")
    assert "5 spawn attempts failed" in text and "Fork is not available" in text


def test_json_report_includes_failed_spawns():
    from agent_receipt.parse import Spawn
    tree = _tree()
    tree.children[0].failed_spawns = [Spawn("a", "tu1", "w", "fork", None, "", error="boom")]
    data = json.loads(render_json(tree, [], Policy(), session_label="s"))
    assert data["tree"]["children"][0]["failed_spawns"] == [{"description": "w", "subagent_type": "fork", "error": "boom"}]


def test_text_report_shows_dollar_cost_per_node_and_total():
    text = render_text(_tree(), [], Policy(), session_label="s")
    assert "Estimated cost: $" in text and "subagents $" in text
    tree_line = [l for l in text.splitlines() if "main session" in l][0]
    assert "$" in tree_line


def test_json_report_carries_cost_fields():
    data = json.loads(render_json(_tree(), [], Policy(), session_label="s"))
    assert isinstance(data["cost"], float) and data["cost"] > 0
    assert data["subagent_cost"] <= data["cost"] and data["unpriced_calls"] == 0
    assert "cost" in data["tree"] and all("cost" in r for r in data["totals"].values())
