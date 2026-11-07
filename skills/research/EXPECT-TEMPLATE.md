# Expected behaviour: <experiment id>

Written before the run; never edited after it. The reviewer compares every row with the trace.
Hypothesis: <H id, one line>. Pass criterion: <copied from EXPERIMENTS.tsv>.

| Stage | Should look at | Should do | Shows in (trace field or metric) | Expected |
|---|---|---|---|---|
| signal | <inputs, e.g. order-book imbalance, last 5 s of trades> | <e.g. go long above 0.3> | <stage=signal, inputs, action> | <e.g. 40-60 signals a day> |
| decision | | | | |
| order | | | | |
| fill | | | | |
| position | | | | |
| exit | | | | |
| result | | | | |

For a model: data -> features -> prediction -> decision -> outcome, one row each.

## Must not happen
- <e.g. orders in the first minute after the open; a position held overnight; a feature from the future>

## Neighbours to watch
Parts not under test that this change may still move: <other instruments, sessions, sides, risk limits, latency>.
