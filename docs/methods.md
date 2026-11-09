# Synthetic controls: methods and limitations

## Observation contract

The primary bounded case is unverified success: a completion claim exists, but a required write may be absent at the decision tick. The underlying state is an authored evaluator ledger. No agent or environment action is executed to produce that state.

Each fixture supplies five eligible records. Full observation retains all five. Fixed-channel loss removes the final `state_receipt`, while random loss retains four records using the declared seeds. The corroborated version also supplies a current `audit_receipt` that survives fixed-channel loss. The trusted source, required key, decision tick and expected record ticks are explicit parts of the monitor-visible task contract.

The renderer excludes evaluator truth, family and episode identifiers, mask reasons, removed-record identifiers, and future records. Visible ticks, sender/channel names and expected-record metadata can reveal missingness. This is intentional, and it limits conclusions about settings where that metadata is unavailable or untrusted.

## Decisions and scores

Decisions are CLEAR, ALERT or ABSTAIN. Receipt-check accepts only an exact, current, sandbox-authored receipt for the required key. Conflicting receipts cause abstention. Claim-trusting deliberately clears when no qualifying receipt remains. Two trivial controls always clear or always abstain.

False clearance is evaluated against the authored adverse-state label. Coverage, abstention, false alarms and errors stay explicit. Undefined metrics remain undefined; absence of decided predictions does not become perfect accuracy. Inspect's decision-format score validates syntax only. Correctness is computed separately using evaluator-owned records.

Saved Inspect inputs join to evaluator records through exact sample identifiers and SHA-256 input hashes. Targets and metadata are empty at the monitor boundary. The saved-log bridge refuses missing or mismatched records and accepts only successful local mock runs.

## Paired analysis

Full, random and selective views refer to the same episode. Contrasts require exactly matching episode/replicate/repeat keys, average repeated views within an episode, and weight episodes equally. Family-cluster counts remain visible. The current demonstration uses 18 variants in three dependent authored families, all assigned to development. It emits no inferential confidence intervals.

The generic split utilities also contain labels named calibration and held_out to test software validation. Those inspected fixture templates are not genuine unseen research evidence.

## Limits

The single-receipt masked active and safe inputs are identical. An observer cannot distinguish the authored underlying states from those inputs alone. This is a constructed information limit, not evidence that a more capable model was tested and failed.

Programmed controls do not establish empirical detection quality. The apparatus does not test interacting model-agent populations, causal changes to agent communication topology, strategic adaptation, or swarm-scale effects. Observer-edge filtering changes what is visible, not how the underlying agents act.

Trusted source names are fixture assumptions, not cryptographic authentication. Evaluator separation is in-process, and the local process can read both sides. A real deployment needs a stronger access boundary, source authentication, outcome collection, and a distinct review of scientific and security assumptions.
