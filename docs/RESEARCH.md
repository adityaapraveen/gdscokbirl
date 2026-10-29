# Research and architecture decisions

Research date: 29 October 2026. The supplied challenge document and the longer product brief were reviewed separately. The document defines the assessment constraints; the brief proposes additional product features. Nothing has been submitted to the hiring company.

## Findings that change the design

### 1. Scope decisions by January rather than deliveries

The challenge specifies purchase decisions made in January 2026. For a D-1 decision, the primary date range is **1–31 January decisions, covering 2 January–1 February delivery**. Filtering only January delivery would omit the last decision day and include a December decision. Make decision date explicit in the interface. An optional January-delivery view must identify its different scope.

The proposed 09:20 Europe/London cutoff is an application assumption, configurable per analysis. An [EPEX trading brochure](https://www.epexspot.com/sites/default/files/2023-01/22-10-25_TradingBrochure.pdf) documents a GB hourly auction at that time. That older brochure is context for the default, not verification of all current trading rules. The challenge names no specific auction or internal desk deadline.

### 2. Forecasts are hourly in the real responses

The challenge describes half-hour actuals and similar forecast timestamps. The inspected WINDFOR records are **hourly**, whereas FUELHH contains half-hour averages in MW. The [API schema](https://data.elexon.co.uk/swagger/v1/swagger.json) describes the fields; the [NESO interface specification, section 14](https://www.neso.energy/document/34076/download) also describes hourly forecasts, although that older specification's horizon differs from the current responses.

Use native timestamp matches for primary accuracy scores. There are 744 such matches in the selected window, against 1,488 actual half-hours. The other 744 half-hours are a resolution mismatch, not missing actuals or failed ingestion. Native matching still compares an hourly forecast sample to the half-hour outturn average starting at the same timestamp; disclose that approximation rather than claim perfect interval equivalence.

If a half-hour forecast curve is added, mark derived values explicitly. Interpolate only between adjacent hourly points from the **same eligible publication**, never across publication versions, missing hours, or past the last source point. Keep derived accuracy separate from native accuracy. Do not fill missing actuals or claim interpolation improves predictive accuracy without testing it.

### 3. Preserve actual publication time too

Forecast selection is the latest source `publishTime <= decision_cutoff` for the delivery timestamp. Exact equality is eligible. No eligible record means unavailable, never a later substitute.

The uncertainty model introduces a second temporal constraint: training outcomes must themselves have been published by the replay cutoff. FUELHH includes `publishTime`; retain it. Store actual versions under `(start_time, publish_time)`, rather than silently overwriting a single row per delivery. Historical revision availability cannot be reconstructed if the upstream service no longer exposes old versions. Source publication time is a proxy for availability, not proof of a particular trader's receipt time.

Use complete earlier delivery days for the first calibration baseline, plus the actual-publication check. This conservatively avoids partially observed days. January-only calibration necessarily has a cold start; show insufficient history at the beginning of the month.

### 4. Keep the empirical method simple until evidence supports more

Use signed errors `actual_mw - forecast_mw`. MAE measures typical absolute magnitude, RMSE gives large misses more influence, and bias reveals systematic over/underforecasting. Keep raw Elexon forecasts visible; an empirical P50 estimate is a separate historical adjustment, not the provider forecast or a guaranteed expected value.

The initial benchmark compares expanding pooled residual quantiles with six-hour lead-time cohorts. Both use at least seven completed historical days and 30 cohort observations. These are declared engineering guardrails, not statistical guarantees. The benchmark uses identical evaluation observations for both candidates and excludes outcomes unavailable at each cutoff. [Forecasting Principles and Practice](https://otexts.com/fpp3/toolbox.html#time-series-cross-validation) explains chronological evaluation; its distributional-accuracy section describes interval scoring.

On the common 532 observations, pooled P10–P90 ranges covered **78.95%** of outcomes, versus **76.32%** for lead-time cohorts. Mean 80% Winkler score was **7,403.90 MW** for pooled and **7,832.06 MW** for cohorts; lower is better. The narrower cohort bands did not improve this comparison. Use pooled residuals as the initial baseline, and expose measured coverage and training counts. This is exploratory evidence from one correlated month, not an independent holdout or proof of generalisation. Do not tune additional methods against the same month and present the winning result as independently validated.

For each point `f`, derive lower/median/upper estimates by adding signed residual quantiles. Floor wind estimates at zero, document that transformation, and calculate downside exposure as `max(0, f - lower)`. This is a historical empirical range, not an assured 80% future coverage statement. Do not turn observed coverage into an individual forecast's confidence probability.

### 5. Do not manufacture a high-risk-period ranking

A pooled residual distribution gives the same residual spread to every period. It cannot justify distinct uncertainty rankings unless the zero floor changes exposure. Show tied values as tied; do not arbitrarily select a top three and imply one period is more uncertain. Conditional ranking can be added after a sufficiently supported method improves chronological validation. Forecast revisions remain useful descriptive information, but large revisions alone do not establish low accuracy.

Do not sum marginal P10 errors to estimate a daily portfolio P10. Errors across delivery periods are correlated. Additional procurement also depends on unavailable demand, positions, contracts and prices.

### 6. Describe the generation population accurately

The [Elexon wind page](https://bmrs.elexon.co.uk/wind-generation) describes forecasts for wind farms visible through operational metering. The [generation-by-fuel explanation](https://bmrs.elexon.co.uk/generation-by-fuel-type) identifies GB coverage and excluded embedded/unmetered wind. Use **GB operationally metered wind**, not all UK wind or all renewable energy. Error against realised generation includes effects not attributable purely to meteorological forecast quality. These two datasets alone cannot isolate those causes.

## Verified API contract

Base URL: `https://data.elexon.co.uk/bmrs/api/v1`.

- `GET /datasets/FUELHH/stream`: publication-window or settlement-date filters, plus `fuelType=WIND`. Do not combine publication and settlement-date filters. Settlement-date queries for an individual delivery day returned exactly 48 WIND rows in January.
- `GET /datasets/WINDFOR/stream`: `publishDateTimeFrom` and `publishDateTimeTo`. These restrict **publication**, not delivery. Each sampled daily window returned 584 records: eight publications of 73 hourly delivery timestamps, including timestamps before publication. Filter delivery scope independently.
- Both return a top-level JSON array, not a `data` envelope. The OpenAPI definition supplies no pagination parameters for these stream routes. Use daily windows and validate counts/coverage rather than assume arbitrarily large responses are complete.
- Observed fields: both have `dataset`, `publishTime`, `startTime`, `generation`; actuals also have `settlementDate`, `settlementPeriod`, `fuelType`.
- Timestamps in the audited data use UTC `Z`. Generation is integer MW in these responses. The schema permits null generation: reject and report it rather than replacing it with zero.
- The sampled FUELHH publication query included both publication boundaries. Daily-window overlap therefore needs duplicate protection. No duplicate or conflicting keys occurred in the full audit's settlement-date actual queries or WINDFOR daily publication windows.
- At the default January cutoff, the latest eligible publication for every native target was **07:30Z**. Do not hard-code the brief's illustrative 08:30 publication schedule.

## Reproducible audit results

[elexon-audit.json](elexon-audit.json) records request URLs, retrieval times, SHA-256 hashes, samples, counts and daily coverage. [residual-benchmark.json](residual-benchmark.json) records the chronological method comparison.

The live audit retrieved 1,488 actual records and 20,440 forecast records. The latter includes supporting publication windows for evolution; 18,104 forecast records fall within the scoped delivery dates. No source rows were null/rejected and no natural keys conflicted in this snapshot. All 744 native delivery timestamps had an eligible forecast and actual.

Native timestamp accuracy at 09:20 D-1: MAE **2,022.47 MW**, RMSE **2,497.41 MW**, bias **−1,745.94 MW**, P90 absolute error **4,154.40 MW**. The negative bias means realised output was lower on average than the eligible forecasts. These are full-window retrospective point-forecast statistics; do not show them as already known in an early-January replay.

Run from repository root with Python 3.11+:

```sh
python3 scripts/inspect_elexon.py
python3 -m scripts.benchmark_residuals
python3 -m unittest discover -s tests -v
```

The first command makes public HTTPS GET requests and caches responses in ignored `.cache/elexon/`. Subsequent runs reuse the cache; use `--refresh` explicitly to retrieve a new snapshot. The benchmark verifies cached bodies against the audit hashes. The live application does not yet exist.

## Proposed application architecture

Use a modular monolith: a command-line ingestion job, one FastAPI API, PostgreSQL, and React/TypeScript/Vite/Recharts. PostgreSQL works locally through Compose and can use a persistent managed database in deployment. The dataset is small; no queue, distributed cache, worker system or new prediction model is justified.

```mermaid
flowchart LR
    E[Elexon FUELHH and WINDFOR] --> I[Explicit ingestion command]
    I --> V[Validation and provenance]
    V --> D[(PostgreSQL)]
    D --> S[As-of selection and analytics services]
    S --> A[FastAPI typed endpoints]
    A --> R[React interface]
```

Ingestion uses bounded timeouts and limited retries for transient network errors, 429 and server errors. Validate schema, timestamps, fuel and units. Count identical duplicates; reject conflicting values for one natural key. Record request hashes and ingestion status. Commit a batch transaction only after validation, and keep a failed run distinguishable from complete data. Do not fetch Elexon during browser refresh or have multiple web workers race to bootstrap data.

Store timestamps as timezone-aware UTC values. Build the London decision cutoff with calendar-date arithmetic, then convert to UTC; do not subtract a fixed 24 hours through DST. Index forecast `(start_time, publish_time DESC)` and preserve actual publication versions. A window query or indexed maximum gives one eligible forecast per target; every additional vintage is not a separate procurement decision sample.

Keep HTTP routes thin. Services own cutoff selection and joins; pure analytics functions own metrics and quantiles; the browser formats units and charts. API responses carry cutoff, source publication, native/derived status, sample counts and calibration window. Replay responses must omit future vintages and actual outcomes until reveal, not merely hide them with CSS.

Suggested folders: `backend/app/{api,clients,services,analytics,models,schemas,core}` and `frontend/src/{pages,components,api,types}`. Combine small related files rather than create a layer for each trivial helper.

Use [Vercel's supported FastAPI deployment](https://vercel.com/docs/frameworks/backend/fastapi) alongside the React frontend with a persistent managed PostgreSQL database. If using Heroku instead, its [ephemeral filesystem](https://help.heroku.com/K1PPS2WM/why-are-my-file-uploads-missing-deleted-from-the-application) makes a mutable local SQLite database unsuitable for durable ingestion. Hosting account access and provisioning remain unverified. Docker Compose remains the reproducible local entry point.

## Scope and implementation order

The four-page product brief is a useful direction, but the challenge's 16-hour limit means correctness comes first. A proposed time budget, including work already spent: research/data audit 2h; ingestion/database 2h; as-of analytics and tests 3h; typed API 1h; overview/accuracy UI 3h; evolution and simple replay 1.5h; Docker/deployment 1.5h; README/video preparation 2h. If the core runs late, omit categorical confidence, the morning brief and conditional risk rankings before cutting tests, deployment or the required demo.

Next module: build ingestion and persistence, prove idempotency, then implement and test decision-time selection before starting UI styling. The mentoring preference in this chat leaves application implementation mode open; no full application solution has been generated in this research module.

Required later checks include cutoff equality/future exclusion, missing forecasts, actual publication availability, error signs, quantiles, duplicate conflicts, DST days, replay payload leakage, ingestion rollback, and clean Docker startup. Restrict CORS to intended origins, validate date/cutoff inputs, keep database secrets in environment variables, expose read-only public analytics, and keep ingestion inaccessible from public request routes. Record structured ingestion errors and readiness separately from process liveness.

Deployment, screenshots, the candidate's two-part unlisted video (maximum ten minutes), README URLs, anonymous-access checks and submission are outstanding. They are document requirements, not actions already completed or authority to submit on the candidate's behalf.
