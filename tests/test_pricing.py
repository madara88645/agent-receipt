from agent_receipt.parse import Usage
from agent_receipt.policy import Policy, load_policy
from agent_receipt.pricing import fmt_usd, price_for


def test_sonnet_5_cost_uses_all_four_rates():
    u = Usage(input=1_000_000, cache_create=1_000_000, cache_read=1_000_000, output=1_000_000)
    assert price_for("claude-sonnet-5").cost(u) == 2 + 2.5 + 0.2 + 10


def test_fable_5_1_cache_reads_are_cheaper_than_fable_5():
    u = Usage(cache_read=1_000_000)
    assert price_for("claude-fable-5-1").cost(u) == 0.25
    assert price_for("claude-fable-5").cost(u) == 1


def test_unknown_model_has_no_price():
    assert price_for("gpt-9") is None
    assert price_for("<synthetic>") is None


def test_policy_price_override_wins(tmp_path):
    p = tmp_path / "p.toml"
    p.write_text('[prices."claude-sonnet-*"]\ninput = 1\ncache_write = 1\ncache_read = 1\noutput = 1\n')
    policy = load_policy(p)
    assert policy.cost_of("claude-sonnet-5", Usage(output=1_000_000)) == 1
    assert Policy().cost_of("claude-sonnet-5", Usage(output=1_000_000)) == 10


def test_fmt_usd():
    assert fmt_usd(None) == "$?" and fmt_usd(0) == "$0.00"
    assert fmt_usd(0.0123) == "$0.012" and fmt_usd(1234.5) == "$1,234.50"
