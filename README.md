# Auditing Completion Claims in Multi-Agent Systems

Synthetic evaluation apparatus for checking completion claims when a monitor cannot see all the evidence. Prepared for the [Swarm Alignment 2026 research sprint](https://apartresearch.com/sprints/swarm-alignment-2026).

**Status:** an offline, reproducible software demonstration. The fixtures are authored and the monitors are deterministic controls. There are no live model evaluations, trained monitors, real interacting-agent experiments, or empirical swarm-scale findings in this repository.

## What it demonstrates

A completion claim can remain visible after the receipt needed to verify it disappears. This apparatus compares full observations, fixed-channel receipt loss, and seeded random loss at the same retained-record count. It separates monitor-visible records from evaluator-owned state labels and measures unsupported clearance alongside abstention and coverage.

The bounded demonstration contains 18 authored variants from three dependent template families, three observation views, and four deterministic monitors. It runs **216 local mock generations**, with zero remote model calls and no inference charges.

- **Receipt-check:** decide only from current trusted receipts; otherwise abstain.
- **Claim-trusting:** accept the completion claim when a receipt is unavailable, deliberately exposing unsafe clearance.
- **Always-clear** and **always-abstain:** controls showing why contrasts need absolute error rates and coverage.

Single-receipt cases include identical monitor-visible inputs for different underlying states after receipt loss. Corroborated cases retain a second receipt. These are constructed controls, not discoveries about model behaviour.

## Reproduce

Python 3.12 is the tested runtime. Use a Git clone, because provenance records the checked-out revision. Create a new virtual environment and install the pinned dependencies:

```sh
git clone https://github.com/Azuremis/swarm-alignment-sprint-2026.git
cd swarm-alignment-sprint-2026
python3.12 -m venv .venv-research
.venv-research/bin/python -m pip install -r requirements-research-lock.txt
.venv-research/bin/python scripts/check_research.py
.venv-research/bin/python tools/absence-harness/unverified_success.py runs/demo-01
```

Choose a new output directory for each run. The runner refuses to overwrite an existing destination. Package installation needs network access; the demonstration uses Inspect AI's local mock provider and blocks socket connections during generation. It does not need provider credentials.

The dependency file is an installed-version freeze, not a cross-platform hash lock. The check script runs the released apparatus tests and scoped Ruff checks. It does not evaluate scientific validity or model quality.

## Outputs

A successful demonstration writes a run plan, authored fixture ledgers, explicit development assignments, saved Inspect logs, separate evaluator records, paired contrasts, Parquet tables, descriptive figures, a summary, and SHA-256 artifact manifests beneath the chosen output directory.

All 18 variants are development fixtures. Observation counts, repeated views and deterministic controls are not independent empirical samples. No confidence intervals are emitted for synthetic fixtures.

Generated files are not tracked. Inspect logs and provenance may contain machine information or trace content, so review generated artifacts before sharing them. The observer/evaluator separation is an application interface, not process isolation or authenticated logging.

## Repository map

- `tools/absence-harness/`: event and episode contracts, observation masks, authored fixtures, offline Inspect integration, deterministic controls, and regression tests.
- `tools/research-workflow/`: schema validation, exact saved-log joins, descriptive metrics, paired episode contrasts, figures, and provenance.
- `scripts/check_research.py`: offline verification of the released code.
- [Methods and limitations](docs/methods.md): observation contract and interpretation limits.
- [Data and dependencies](docs/data-and-dependencies.md): fixture provenance and third-party boundaries.

## Interpretation limits

The code checks apparatus behaviour on known cases. It does not establish monitor reliability, a causal effect of swarm topology, scaling behaviour, emergence, or a validated defense. Record-count matching does not match information content or token count. The fixed random draws in this demonstration retain the final receipt, so they cannot estimate a population effect of random loss.

Empirical use requires a justified task/environment, independently collected outcome labels, reviewed data rights, validated baselines, a dependence-aware sampling plan, and new held-out families with frozen prompts and masks.
