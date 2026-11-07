# Research program: <goal in one line>

## Goal (since <date>)
<What counts as success, in the user's terms.>
Deliverable: <e.g. a config in the standard pipeline plus predictions in the simulator format>.

## Hard rules from the user
- "<verbatim quote>" (<date>) -> <what it means for the loop>

## Out of scope
- <rejected directions, with a pointer to KNOWLEDGE.md "Rejected">
- Other people's runs and folders are never touched.
- Thresholds and parameters are never tuned on the holdout; it is looked at only as ENVIRONMENT.md says.

## Pipeline
<The project's standard tooling: dataset builder, features, model step, export format.
Framework functions to reuse, with paths.>

## Metrics (in order of importance)
- <primary money or quality metric, net of costs>
- <stability: share of positive days, per window>
- <tail: worst day, worst trade>
- <diagnostics: R2, corr; never the goal>

## Data splits (fixed before round 1)
- Instruments / datasets: <...>
- Train: <dates or ids>. Validation (selection, early stopping, thresholds): <dates or ids>.
  Holdout: in ENVIRONMENT.md, never here in numbers.
- Feature or alpha selection period: <dates> (in-sample by selection; outside validation and holdout).
- Evaluation windows inside validation: <e.g. two-week windows W_A, W_B, ...>
- Costs: <fees, taxes, spreads, latency>

## Environments
Simulation and verification: ENVIRONMENT.md (requirements S1-S7, V1-V5 with evidence).

## Resource limits
<cores, memory, disk, hours per experiment>

## Project review checklist
<Domain checks the reviewer must run on top of its protocol: leakage traps specific to this
data, fill-side conventions, units, known data quirks.>

## Round 1
| id | hypothesis | rationale | expected effect | cost (h) | pass criterion | status |
|---|---|---|---|---|---|---|
| H1.1 | | | | | | pending |
