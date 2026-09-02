# Evidence

Numbers below come from running `scripts/corpus_check.py` on the author's own machine on
2026-09-02 (Claude Code 2.1.x transcripts, 67 sessions, largest file 32 MB). Nothing from
the transcripts is reproduced here except counts and totals. Run the script yourself to get
the same table for your data.

## Robustness

| check | result |
|---|---|
| sessions on disk | 67 |
| parsed without error | 67 (0 crashes) |
| wall time for all 67 | 3.9 s (slowest single session 0.16 s) |
| unit tests | 76, synthetic transcripts only |

## What the tool found that nothing else shows

| fact | count |
|---|---|
| sessions that spawned subagents | 27 of 67 |
| agents found in total | 700 |
| of which launched by the Workflow tool | 587 (invisible until the workflow directories were parsed) |
| Workflow runs | 59 |
| sessions where a subagent ran a heavier model than the cheap set | 20 |
| agents nested deeper than the allowed depth | 24 |
| failed spawn attempts (concurrency limit, fork inside a fork) | 41 in one session |
| resolved-model mismatches (launcher said Sonnet, calls ran on Fable) | 3 |
| continued sessions that would have been double-counted in a multi-session total | 1 (a $242 session counted twice: $115.62 of it was inherited history) |

## Why the model-mismatch check matters

These are open or recurring reports on the Claude Code issue tracker, all "I asked for model
X, model Y ran":

- [#43869](https://github.com/anthropics/claude-code/issues/43869) Subagent model routing is broken — all mechanisms resolve to parent model (open, 17 comments)
- [#44385](https://github.com/anthropics/claude-code/issues/44385) agent definition frontmatter `model:` field is ignored
- [#47488](https://github.com/anthropics/claude-code/issues/47488) Agent tool `model` parameter silently ignored — all sub-agents routed to Haiku
- [#18346](https://github.com/anthropics/claude-code/issues/18346) Claude Code does not respect agent model definition
- [#5680](https://github.com/anthropics/claude-code/issues/5680), [#5456](https://github.com/anthropics/claude-code/issues/5456), [#13434](https://github.com/anthropics/claude-code/issues/13434) earlier variants of the same bug

And the requests for what this tool prints:

- [#22625](https://github.com/anthropics/claude-code/issues/22625) Per-subagent token usage tracking (closed, not planned)
- [#24537](https://github.com/anthropics/claude-code/issues/24537) Agent hierarchy view (open)
- [#48040](https://github.com/anthropics/claude-code/issues/48040) Aggregate cost across sub-agent sessions in the status line (closed); the status line documentation states it covers the main conversation only

None of the surveyed tools (ccusage, claude-code-usage-tracker, agents-observe, cccost,
claude-usage) reports which model a subagent actually ran versus what was requested.

## How accurate is the dollar figure

Two sessions on this machine carry Claude Code's own `cost-state` record. One is empty. The
other reads:

| model | our estimate | Claude Code's figure |
|---|---|---|
| claude-fable-5-1 | $24.09 | $22.05 |
| claude-sonnet-5 | $16.77 | $28.42 |
| claude-haiku-4-5 | $0.00 | $9.41 (never appears in any transcript) |
| **total** | **$40.86** | **$59.88** |

So: the per-token rates agree (the Fable line is within 10%), but Claude Code makes calls
that are never written to the transcript files, on Haiku and on Sonnet, for its own
housekeeping (titles, summaries, classifiers). **Treat the tool's figure as a lower bound of
what Claude Code would bill at list price.** The receipt prints both numbers side by side
whenever a `cost-state` record exists.

Other caveats, all deliberate:

- list prices, 5-minute cache-write rate, no batch or regional multipliers, no subscription
  plans;
- a `<synthetic>` assistant message is a Claude Code placeholder and is skipped;
- de-duplication keeps the largest `usage` seen per `message.id` (streaming writes one line
  per content block with the same id). Summing every line instead overstates Fable output
  by 2.8x on the session above; the `usage.iterations` breakdown was checked and never
  exceeds the top-level figure.
