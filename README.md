# WindLens

Day-ahead wind analysis and renewable procurement estimates.

[Live app](https://gdscokbirl.vercel.app/) · [API docs](https://gdscokbirl.vercel.app/api/docs)

## Run

```sh
git clone https://github.com/adityaapraveen/gdscokbirl.git
cd gdscokbirl
docker compose up --build
```

Open [localhost:8080](http://localhost:8080); no API key is required.

## Features

- Wind replay uses January 2026 decision dates and publication-time checks.
- Procurement accepts daily totals or hourly demand, firm contracts, existing purchases and renewable allocations.
- Wind, solar, non-pumped hydro and biomass come from Elexon APIs.
- January is the default; future mode supports tomorrow's delivery date.
- Renewable allocations are percentages of national output, not asset forecasts or nameplate capacity.
- Contracts, existing purchases and renewable allocations must not overlap.
- Daily totals are spread evenly across 24 editable hours; surplus stays separate from shortfalls.
- Daily stress scenarios retain historical dependencies between sources and hours.
- Future demand is supplied by the user; no customer demand model is claimed.
- Future quantities are estimates, not exact needs or a price-optimized buying schedule.

## Models

- Trained LightGBM on Elexon history from 2023, with 827,156 normalized records.
- Compared 12 configurations per source, including one-year and longer training windows.
- Training ends August 2025; September stops training and October selects models.
- November–December calibrates scenarios; January is a benchmark and February is a separate confirmation test.
- February MAE improves about 39% for wind, 5% for hydro and 20% for biomass against their baselines.
- Solar keeps its baseline because the model did not clear the validation threshold.
- Models train offline; checksum-verified exports run inside FastAPI without native ML dependencies.
- See [model results](docs/MODEL-EVALUATION.json) and [source audit](docs/renewables-source-audit.json).
- Future mode uses the historical models and does not claim the same accuracy on newer dates.

## Development

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
.venv/bin/python -m backend.app.ingest
.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8000
```

```sh
npm ci --prefix frontend
npm --prefix frontend run dev
```

Open [localhost:5173](http://localhost:5173); backend settings are listed in `.env.example`.

## Train and verify

```sh
.venv/bin/pip install -r backend/requirements-training.txt
.venv/bin/python -m scripts.acquire_renewables --from-date 2023-01-01 --to-date 2026-03-03
.venv/bin/python -m scripts.train_procurement
.venv/bin/python -m scripts.evaluate_procurement
.venv/bin/python -m pytest -q
npm run build
```

Raw downloads stay in the ignored local cache; models, checksums and audit results are committed.

## Implementation

- `backend/app/`: source validation, SQLite storage, forecasts, procurement calculations and FastAPI routes.
- `backend/models/`: exported models and checksums.
- `frontend/src/`: React 19, TypeScript, Vite and Recharts.
- `scripts/`: acquisition, training and backtesting.
- `docs/`: research, source audits and verification results.
- Runtime refreshes use concurrent requests, bounded retries, one-hour caching and a 60-second cooldown.
- Docker persists source data; Vercel uses an ephemeral SQLite cache with a bundled fallback.
- Portfolio inputs are not stored, logged as request bodies or sent to Elexon.
- The pilot supports 24-hour days; 23/25-hour DST transition days are explicitly rejected.
- Solar outcomes may be estimated; original wind research is in [RESEARCH.md](docs/RESEARCH.md).
- AI-assisted tools were used for research, implementation, debugging and review.
