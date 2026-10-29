# WindLens

Day-ahead wind forecast analysis for Great Britain using Elexon FUELHH and WINDFOR.

The product will show which forecast was available at a procurement decision cutoff and evaluate its historical error. National generation data alone cannot determine a utility's procurement requirement.

## Project status

The first research and data-validation module is complete. This repository does not yet contain a runnable web application or a deployed service.

## Research and data validation

Read [research and architecture decisions](docs/RESEARCH.md) before implementing the application. It explains the hourly/half-hourly resolution mismatch, January decision-date boundaries, two sources of look-ahead leakage, and the empirical uncertainty benchmark.

The live audit found 1,488 half-hour actual records and 744 native hourly forecast/actual pairs for decisions made 1–31 January 2026. The default cutoff is 09:20 Europe/London on the day before delivery.

Python 3.11+ is sufficient for the research tools; no third-party packages are needed:

```sh
python3 scripts/inspect_elexon.py
python3 -m scripts.benchmark_residuals
python3 -m unittest discover -s tests -v
```

The inspection command downloads public Elexon data once and caches it locally. Add `--refresh` to download again. The benchmark runs offline against the audited snapshot. These commands do not start a web application.

## Files and directories

- `scripts/inspect_elexon.py`: read-only API audit, validation and per-decision coverage.
- `scripts/benchmark_residuals.py`: chronological comparison of empirical uncertainty methods.
- `tests/`: offline checks for data integrity, temporal selection and DST boundaries.
- `docs/RESEARCH.md`: sourced findings, proposed architecture and implementation order.
- `docs/elexon-audit.json`: request provenance and live data-quality results.
- `docs/residual-benchmark.json`: retrospective accuracy and matched-sample uncertainty comparison.
- `.cache/elexon/`: ignored local response cache, created by the audit.

## Application delivery

React frontend, Python API, database ingestion, Docker Compose, deployment and video demonstration are the next stages. No live URL or video URL is available yet.

## AI assistance

AI tools were used for research synthesis, data inspection, validation scripts and review. Sources, executable checks and known limitations are included so the work can be inspected and independently reproduced.
