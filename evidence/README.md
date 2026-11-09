# Reviewed descriptive evidence

`r13-summary.json` and `r14-summary.json` are allowlisted transformations of saved descriptive summaries from the live-agent development runs. They include categorical outputs, not raw provider response bodies.

Included fields describe the design, model, execution denominator, artifact state, delivery condition, round-by-round decisions, proof holders and aggregate agreement/error counts. Agent labels A0–A7 are experimental roles. The underlying artifact conditions and completion claims were authored; the agent responses were live model outputs.

Each file includes SHA-256 hashes of the source summary, source configuration and evidence manifest, plus recorded dependency versions and source-file hashes. These identify the original artifacts used for review. The originals are not bundled, so these hashes do not make the historical results independently verifiable from this repository alone. The transformation intentionally omits raw messages, request/provider identifiers, machine paths, authentication and accounting records.

R13 has 95 valid replies out of 96 planned slots; its final slot was blocked before dispatch by the combined attempt limit. R14 has 192 valid replies out of 192. Related actor turns and repeated packet views must not be treated as independent samples. These are development cases with one population per cell, not a held-out benchmark or a population-level effect estimate.

No new paid calls were made to prepare this release. The publication does not establish raw-trace redistribution rights or provide a complete historical replication package.

Historical source hashes describe the code used for each original run. All R14 hashed runtime files match this release. R13 used an earlier `recovery_reservations.py`; the released file is the later R14 version, so this checkout is not a byte-identical reconstruction of the historical R13 runtime.
