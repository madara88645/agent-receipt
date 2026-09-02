# agent-receipt

A receipt for your Claude Code subagents: who spawned whom, which model actually ran, what it cost in tokens, and which of your rules were broken.

![agent-receipt](assets/hero.png)

## Why

You can ask a subagent to run on a cheap model. You cannot see whether it did, or whether it quietly spawned five more agents on the expensive default. The transcript on disk knows. `agent-receipt` reads it and prints the bill. Nothing leaves your machine.

## Install

```bash
uv tool install git+https://github.com/madara88645/agent-receipt
```

Python 3.11+, no dependencies. `pipx` works too.

## Use

```bash
agent-receipt                  # latest session of the current directory
agent-receipt be176aa4         # any session, id prefix is enough
agent-receipt --json           # machine-readable
agent-receipt --policy my.toml # your own rules
```

Exit code is `1` when there are findings, so it works in CI on exported transcripts.

Every row carries a dollar estimate, priced from the public Claude API rates (input, cache write, cache read, output) baked into the tool. Unknown models show `$?`. Override any rate in the policy file.

## Run it on every session

Register it as a Claude Code `SessionEnd` hook and a receipt is written for every session you close:

```bash
agent-receipt --print-hook-config
```

Paste that into `~/.claude/settings.json` (or merge it into your existing `hooks`). Each session then leaves `~/.claude/agent-receipt/<session>.txt` and prints one line:

```
agent-receipt: 25 agents · $40.86 · 37 findings → ~/.claude/agent-receipt/be176aa4-….txt
```

The hook never blocks or fails a session; problems go to stderr and the exit code stays 0.

![demo](assets/demo.gif)

## Rules

| rule | fires when |
|---|---|
| `heavy-model` | a subagent ran on a model outside `cheap_models` |
| `nested-spawn` | an agent sits deeper than `max_depth` (default 1) |
| `failed-spawn` | Agent calls that errored: concurrency limit, fork inside a fork |
| `model-switch` | one agent used more than one model |
| `resolved-mismatch` | launcher said one model, calls ran on another |
| `missing-transcript` | spawn recorded, no transcript file found |
| `too-many-agents` | more agents than `max_agents` (off by default) |

Policy file, every key optional:

```toml
cheap_models = ["claude-sonnet-*", "claude-haiku-*"]
max_depth = 1
max_agents = 0
flag_failed_spawns = true

[prices."claude-sonnet-5*"]      # USD per million tokens, fnmatch pattern
input = 2
cache_write = 2.5
cache_read = 0.2
output = 10
```

## Notes

- Forks copy their parent's history. Each child is credited to one parent, each failed attempt counted once.
- Dollar figures are estimates at list price: 5-minute cache-write rate, no batch or regional multipliers, no subscription plans. Your invoice is the truth.
- Claude Code's `<synthetic>` placeholder messages are ignored; they are not API calls.

## Develop

```bash
uv run pytest -q
```

MIT
