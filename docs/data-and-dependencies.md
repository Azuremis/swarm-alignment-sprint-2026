# Data and dependencies

## Included evidence

This repository contains Python-authored synthetic fixtures, live-experiment runtime source, regression tests, and reviewed categorical summaries of R13/R14 live model responses in authored development environments. Synthetic fixtures and live responses are labelled separately.

The R13/R14 summaries retain the design, execution denominator, per-cell decisions and proof-holder labels. They omit raw request/response bodies, provider identifiers, execution ledgers, authentication material and host-specific metadata. No downloaded gated dataset or model checkpoint is bundled.

The historical evidence is not complete enough here for independent request-by-request reconstruction. Source hashes identify the reviewed original summary/configuration/manifests, but those originals are not published and the hashes alone do not validate their contents. See [evidence provenance](../evidence/README.md).

## External source

The small source-shaped Colosseum adapter uses authored test inputs; it is distinct from the later pinned source runtime. No upstream implementation is vendored in this repository. The live invoker expects an independently acquired, byte-verified Colosseum protocol file. The exact protocol download and its MIT license were verified at the pinned revision; see [live setup](live-research.md). Retain upstream license notices if copying it. This release grants no additional rights to external data or source.

## Dependencies

Third-party libraries are installed separately under their own licenses and terms:

- [Inspect AI](https://inspect.aisi.org.uk/) for saved logs and local observer replay.
- [OpenAI Python](https://github.com/openai/openai-python) and Terrarium for the live runtime.
- [pandas](https://pandas.pydata.org/), [PyArrow](https://arrow.apache.org/docs/python/) and [scikit-learn](https://scikit-learn.org/) for tables and descriptive analysis.
- [NumPy](https://numpy.org/), [Matplotlib](https://matplotlib.org/) and [Seaborn](https://seaborn.pydata.org/) for computation and figures.
- [Ruff](https://docs.astral.sh/ruff/) for scoped source checks.

`requirements-research-lock.txt` freezes the previously installed research environment. `requirements-live.txt` adds OpenAI 3.46.0 and Terrarium 0.2.1 as direct pins. Neither file is a cross-platform hash lock, and a fresh combined installation has not been verified. The tests run without live provider authentication.
