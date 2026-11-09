# Data and dependencies

## Included data

This repository includes Python-authored synthetic fixtures and test inputs. It contains no downloaded gated dataset, real agent-traffic corpus, provider output, or model checkpoint. The bounded completion-claim demonstration derives its evaluator labels from authored state ledgers.

The optional Colosseum adapter describes the shape of exported tool events. Its test records and outcomes are authored fixtures. It has not been validated against a real export and does not include upstream source code or collected episodes. Source schema reference:

- [Colosseum export implementation](https://github.com/umass-ai-safety/colosseum/blob/26ad60beb4006e6101477820d4b19b43a9c86033/experiments/collusion/run.py)
- [Colosseum event recording](https://github.com/umass-ai-safety/colosseum/blob/26ad60beb4006e6101477820d4b19b43a9c86033/experiments/common/local_protocol.py)

Use of real external datasets or traces requires checking their applicable access, use and redistribution terms. This release does not grant rights to third-party data.

## Dependencies

Third-party libraries are installed as dependencies and are not vendored in this repository. Their own licenses and terms apply. The principal components are:

- [Inspect AI](https://inspect.aisi.org.uk/) for local mock evaluation and saved evaluation logs.
- [pandas](https://pandas.pydata.org/) and [PyArrow](https://arrow.apache.org/docs/python/) for tables and Parquet.
- [scikit-learn](https://scikit-learn.org/) for descriptive metric helpers.
- [NumPy](https://numpy.org/), [Matplotlib](https://matplotlib.org/) and [Seaborn](https://seaborn.pydata.org/) for computation and figures.
- [Ruff](https://docs.astral.sh/ruff/) for scoped source checks.

`requirements-research.txt` records direct research-tool dependencies; `requirements-research-lock.txt` records the tested installed versions, including transitive dependencies. Installing dependencies is a separate step from running the offline demonstration.
