# Candidate demo guide

Record your own unlisted YouTube walkthrough, under ten minutes total. Use your own words and verify the current numbers on screen. This is a recording guide, not a completed video.

## Part 1 Customer presentation about four and a half minutes

**0:00–0:40 — The decision.** Overview: delivery 22 January, cutoff 09:20 D-1. Explain that the analyst must commit before actual wind is known. The tool shows the available forecast and how much earlier forecasts missed.

**0:40–1:40 — The outlook.** Point out decision time, source publication, original curve and empirical P10–P90 range. Explain MW/GW, negative bias and downside exposure. Without demand or portfolio data, the tool cannot calculate how much to buy.

**1:40–2:20 — Historical context.** Show the deterministic brief and provenance. Only previously published outcomes calibrate the range. Pooled errors can produce tied exposures; the UI discloses them instead of inventing a ranking.

**2:20–3:30 — Replay.** Open Evolution at 18:00. Show the selected vintage and cutoff, reveal actuals, and compare later forecasts without crediting them to the decision. Reset replay. Select 2 January to show insufficient history.

**3:30–4:30 — Reliability and trust.** Explain MAE, RMSE and bias on Accuracy, then scatter and error distribution. Show Data Quality: 1,488 half-hours and 744 native pairs are expected because forecasts are hourly. State the operational-metering limitation.

## Part 2 Technical review about four and a half minutes

**4:30–5:15 — Architecture.** Show the README diagram. Explain snapshot ingestion, SQLite and separation from requests. Describe when PostgreSQL would become appropriate. Vercel's temporary database is a cache of the immutable snapshot.

**5:15–6:15 — Temporal correctness.** Open `backend/app/analytics.py`: cutoff construction, as-of selection, historical residual pool. Explain both leakage guards and why same-day/incomplete delivery histories are excluded.

**6:15–7:00 — Data integrity.** Show `sources.py`, `ingest.py`, `storage.py`: validation, hashes, publication versions, duplicates, conflicting keys and rollback. Explain `/ready` versus `/health` and the read-only HTTP surface.

**7:00–8:00 — Analytics choices.** Explain error signs and the independent research benchmark. Pooled residuals beat tested lead-time cohorts on common exploratory observations; this is not proof of future performance. Distinguish source point forecasts and empirical quantile estimates.

**8:00–9:00 — Tests and delivery.** Run pytest; show CI, frontend/API separation, replay payload exclusion, Docker Compose and production URL. State the hourly-sample/half-hour-average approximation and other data limits.

Aim to finish around nine minutes. Do not read every source file. Be ready to explain what each guardrail prevents and how a test would expose its removal.

## Before submitting

- Keep the recording unlisted and at most ten minutes.
- Put the real video URL in README.
- Open production app, public GitHub repository and video in a private session.
- Confirm that deployment protection does not block the production alias.
- Review the code yourself and retain the AI-assistance disclosure.
- Submit the required URLs yourself. No hiring form has been submitted by the app.
