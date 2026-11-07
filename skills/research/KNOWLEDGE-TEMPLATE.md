# Knowledge: <research name>

Every item is a fact with a number and a source (script, result file, experiment id, review,
link). New sections go on top. Retrieved with `kb_rag.py`, so keep headings specific: one
experiment or one question per section, and say under each heading what it is and whether it
still holds (kinds: measurement, conclusion, conjecture; status: valid, invalid, superseded).
A section is never deleted: an invalid run's section gets `status: invalid` and the reason, a
replaced one `status: superseded · supersedes: <old heading>`.

## What we know now (<date>)
kind: conclusion · status: valid · scope: <data window, cost model> · date: <date>
- Best so far: <id, metric, window> (src: EXPERIMENTS.tsv <id>, reviews/R<NN>)
- Failed and why: <...>
- Suggests next: <...>

## Discrepancies: expected against actual
kind: measurement · status: valid
- <id> <stage>: expected <...>, actual <...> (results/<id>/trace.tsv rows, reviews/R<NN>) -> <H id>

## Rejected
- <direction>: "<user quote>" (<date>). Not to be revived.

## Data
- <quirks, coverage, formats> (src: <script>)

## Costs
- <fees, taxes, spreads> (src: <link or file>)
