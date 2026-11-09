# Auditing Completion Claims in Multi-Agent Systems

Research code and reviewed descriptive evidence for the [Swarm Alignment 2026 research sprint](https://apartresearch.com/sprints/swarm-alignment-2026): how completion claims and independently verifiable receipts affect decisions in a small interacting group.

## What is included

**Live-agent research:** R13 and R14 used real `gpt-6-luna` responses in deliberately constructed artifact-verification tasks. Eight logical agents retained their own histories across three rounds and could forward selected receipts to their successors. R14 separately enabled or disabled peer prose and receipt delivery. These agents did not edit or repair the artifact.

**Synthetic controls:** an earlier offline demonstration uses authored event traces and deterministic monitors to test observation masks, evaluator separation, abstention, coverage and paired analysis. Its 216 local mock generations are software checks, separate from the live-agent evidence.

**Observer replay:** Inspect AI runs deterministic local checks over saved agent packets. These observer outputs are not additional live agents, populations or independent experiments.

The original public release contained only the synthetic controls. This expanded release adds the actual R13/R14 runtime libraries, regression tests and sanitized descriptive summaries. It does not include a turnkey live launcher or the complete historical raw artifacts.

## Observed scope

- **R13:** four authored development populations at fixed N=8, three rounds, 95 valid responses of 96 planned slots. The final slot was stopped before dispatch by the combined attempt limit.
- **R14:** eight authored development populations at fixed N=8, three rounds, 192 valid responses of 192 planned slots. The design crosses correct/incorrect artifact state with neither channel, prose only, receipts only, or both channels.
- In these runs, receipt-enabled delivery expanded proof access from A0 to A1 and then A2. Prose alone did not confer authenticated proof. All valid responses agreed with the explicit receipt policy; no false or unsupported certifications were observed in these bounded cases.

These are correlated observations from one authored family, with one population per condition. They do not establish general model accuracy, reliable causal effect sizes, emergence, size scaling or adversarial robustness. Confidence was not measured. Three rounds allow at most two forwarding hops.

## Verify the released software

Python 3.12 is the tested runtime. Use a Git clone because provenance records the checked-out revision:

```sh
git clone https://github.com/Azuremis/swarm-alignment-sprint-2026.git
cd swarm-alignment-sprint-2026
python3.12 -m venv .venv-research
.venv-research/bin/python -m pip install -r requirements-research-lock.txt
.venv-research/bin/python scripts/check_research.py
```

These tests use authored fixtures and mocked providers. They do not spend inference credits or reproduce the historical live responses. The dependency file is an installed-version freeze, not a cross-platform hash lock. This release was checked in the existing tested environment; a fresh installation has not been verified.

To run the earlier synthetic demonstration:

```sh
.venv-research/bin/python tools/absence-harness/unverified_success.py runs/demo-01
```

Choose a fresh output directory. The demonstration refuses overwrites, uses Inspect's local mock provider and blocks socket connections during generation.

## Live research and evidence

- [Live research methods and setup boundaries](docs/live-research.md)
- [R13 descriptive evidence](evidence/r13-summary.json)
- [R14 descriptive evidence](evidence/r14-summary.json)
- [Evidence provenance and exclusions](evidence/README.md)
- [Synthetic-control methods](docs/methods.md)
- [Data and dependencies](docs/data-and-dependencies.md)

The live source is exposed as Python APIs: `ring_phase.py` and `channel_phase.py` prepare, execute and audit experiments; `ring_turn.py` supplies the bounded model interaction; `ring_inspect.py` and `channel_inspect.py` replay observer conditions. A live run requires an explicitly configured request gate, fresh budget/reconciliation inputs, the reader's own authentication, exact runtime dependencies, and the verified upstream protocol source. No credentials are included or loaded automatically by these libraries.

The release includes reviewable research logic, offline regression checks and categorical summaries. It does not yet provide one-command reproduction of live experiments or enough raw evidence to independently reconstruct every historical result. A rerun against a provider model alias may also produce different responses.
