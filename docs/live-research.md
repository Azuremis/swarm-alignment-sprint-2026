# Live research: R13 and R14

## Authored task, real model responses

The task deliberately introduces a completion claim about a sealed artifact. The host chooses whether the artifact matches its required contents, checks the actual bytes, and provides an authenticated receipt to A0. The environment and starting claim are designed controls. The acting agents' responses were obtained from the model provider, not prewritten by a rule.

Each population contains eight logical actors, A0 through A7. Own history and held receipts persist in explicit packets across three synchronous rounds. Transport calls are executed serially. Each invocation creates a fresh framework agent with the actor's supplied packet; no hidden shared model context is assumed. Agents can classify the completion claim and select receipts to forward, but cannot modify the artifact.

R13 compares directed-ring forwarding against no peer delivery for correct and incorrect artifact states. Its recovery run records 95 valid replies; the last of 96 planned slots was stopped before dispatch by the combined attempt ceiling. This is missing execution, not a model answer.

R14 crosses artifact state with four delivery conditions: neither channel, prose only, receipts only and both. This yields eight populations and 192 completed actor turns. A prose description of a receipt never becomes an authenticated receipt. The source audit reconstructs received packets from earlier saved responses and checks host-owned truth separately.

Here, authenticated means validated against the host-owned registry and captured state inside the test harness. It does not mean a deployed external signature or trust infrastructure.

## Runtime

The live runs used `gpt-6-luna`, low reasoning, standard service, explicit prompt-cache mode, at most 512 output tokens per request and no automatic retries. A provider alias is not an immutable model-weight identifier.

Recorded package versions were OpenAI 3.46.0, Terrarium 0.2.1 and Inspect AI 0.3.288. `requirements-live.txt` adds the two live direct dependencies to the research environment. It is not a complete independently tested transitive lock; fresh live installation has not been verified for this release.

`PinnedRingInvoker` also verifies the exact bytes of the upstream Colosseum local protocol. The required SHA-256 is `3fad6a5e79f5fe48ffac50d3f46a1c4f379f77c431db4983341fa59208486c11`. The upstream file is not bundled. The [exact protocol file](https://raw.githubusercontent.com/umass-ai-safety/colosseum/26ad60beb4006e6101477820d4b19b43a9c86033/experiments/common/local_protocol.py) was retrieved and its bytes matched that hash during release review. That revision carries an [MIT license](https://github.com/umass-ai-safety/colosseum/blob/26ad60beb4006e6101477820d4b19b43a9c86033/LICENSE). Obtain the file separately, retain applicable notices, and verify its hash before use. A fresh end-to-end runtime setup is still unverified.

## Code entry points

- `tools/research-workflow/ring_phase.py`: R13 preparation, source execution and audit.
- `tools/research-workflow/channel_phase.py`: R14 four-channel preparation, source execution and audit.
- `tools/absence-harness/ring_turn.py`: one bounded model turn through the pinned source runtime.
- `tools/absence-harness/relay_contract.py` and `verification_state.py`: receipt semantics, packet validation and independently checked artifact state.
- `tools/research-workflow/comparison_request_gate.py`, `request_gate.py` and reservation modules: explicit attempt recording, spending bounds, stops and accounting.
- `tools/research-workflow/ring_inspect.py` and `channel_inspect.py`: deterministic local observer replay with source review and exact joins.

The phase modules are library APIs, not command-line launchers. Execution requires an injected accounted gate and pinned invoker. Historical launchers are not included; a portable opt-in launcher, fresh reconciliation configuration and user-supplied authentication still need to be assembled and tested before another live experiment. Do not treat unit-test fixture authorization or accounting records as permission for paid calls.

## Results and limits

The published JSON summaries preserve per-cell categorical decisions and proof holders. In receipt-enabled conditions, correctly resolved decisions grew from one to two to three actors across rounds. Prose-only and no-delivery conditions retained authenticated proof at A0. All valid replies followed the explicit rule in these bounded runs.

One authored family, one population per condition, fixed N=8, stochastic model calls and related actor turns limit inference. The experiment does not estimate population prevalence, general accuracy, scale effects or a reliable causal effect size. It does not test free-form collaborative production, agent repair, natural mistakes or adversarial persuasion. The source assertions and artifact states were introduced by design.

Inspect observer replay is deterministic local analysis, not additional paid model evaluation. Historical raw source artifacts are not bundled, so readers cannot independently reconstruct every request, response, receipt and audit join from this release. The sanitized summaries are descriptive evidence, not a substitute for a complete replication package.
