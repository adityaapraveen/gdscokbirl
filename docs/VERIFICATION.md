# Verification record

Date: 29 October 2026. This record distinguishes local, container, browser and deployed checks. The candidate video and hiring-form submission are not completed by these checks.

## Backend

- 83 pytest checks pass, including original research checks, runtime Elexon refresh, failure handling and response-cache isolation. The full run completed in 37.41 seconds.
- Native real-source metrics agree with the separate research benchmark: n=744, MAE 2,022.47 MW, bias −1,745.94 MW.
- Snapshot import inserts 19,592 records; repeating the same import inserts none and counts identical records as duplicates.
- Conflicting natural keys roll back the whole transaction. Actual publication revisions are preserved.
- API replay checks exclude future publication values and actual outcomes until reveal.
- Cold-start calibration emits null ranges, rather than fabricated values.
- Two concurrent real Elexon requests retrieved 1,488 actual rows and 20,440 forecast rows. All audited publication keys remain present. The complete runtime fetch, validation, import and read-back took 2.65 seconds in a temporary local database, and all 744 native accuracy matches remained unchanged.
- Mocked network tests cover deadlines, retries, status errors, response-size limits, wrong datasets, incomplete archives, conflicting versions, unchanged-data freshness, cooldown and concurrent refresh coalescing. Upstream failures retain the prior usable data.
- The serverless adapter reconstructs and rereads a checksum-named database from the bundled source without network calls.
- The test dependency emits one Starlette/httpx deprecation warning. It does not fail the suite or affect the deployed HTTP paths. The sandbox's thread/event-loop restriction stalled TestClient startup; the complete suite passes with normal host execution.

## Frontend and browser

- TypeScript and the Vite production build pass. Chart code is separated into its own chunk.
- Production npm dependency audit reports zero known vulnerabilities at verification time; this is not a complete security audit.
- All four views render using real API responses.
- Browser checks cover reveal/reset, publication selection, cutoff changes, delivery-date changes, January cold start, accuracy metrics, provenance and data-health counts.
- Desktop and narrow-screen layouts were inspected. A control/table-label overflow found during testing was corrected. At the narrowest measured browser content width of 277 CSS pixels, document scroll width equals viewport width; wide tables scroll inside their containers.
- The source refresh control was checked in the local React UI: manual refresh updates the successful-check time and starts the cooldown while charts remain available. At a measured 341-pixel content viewport, the new status bar causes no horizontal page overflow.
- Screenshots in `docs/screenshots/` capture overview, accuracy, data quality and mobile layout. Chart animations are disabled; reduced-motion preferences are respected.

## Docker

- `docker compose up --build -d` builds and starts frontend and backend.
- Backend readiness becomes healthy after importing the frozen snapshot.
- HTTP requests through port 8080 return the frontend, health/readiness and typed analytics successfully.
- Containerized accuracy reproduces the 744-match research results.
- Both application containers run without root privileges. The database remains in a named volume across recreation.
- Runtime images use the pinned production dependency file; test dependencies remain in the development lockfile.
- The rebuilt Docker application fetched and persisted both Elexon datasets in 2.32 seconds. Accuracy returned 744 matches from the stored API data. Restarting the backend preserved its successful-check timestamp in the database volume.
- API responses through the proxy include `Cache-Control: no-store`; larger analytical responses use gzip.

## Performance

- Before optimization, the full-month accuracy calculation took approximately 350–450 ms locally. Calibration was recomputed for every observation despite only 31 distinct decision cutoffs.
- Calibration now caches each dataset/cutoff result. Validated JSON is cached with a bounded 128-entry limit; keys include dataset generation, query parameters, replay reveal state and effective source freshness. A refresh invalidates analytical and response caches.
- Fifteen warm localhost HTTP requests per view measured median latency of 2.23 ms for Overview, 4.52 ms for Accuracy, 2.28 ms for Evolution and 2.49 ms for Data Quality. The first accuracy request took 54.74 ms in that run.
- Gzip reduced the accuracy response from 208,284 bytes to 20,710 bytes, approximately 90%. These development-machine measurements exclude public network distance and Vercel cold-start effects.

## Vercel

- Deployment was created in the candidate's authorized Vercel account and linked to the public GitHub repository.
- Production alias: https://gdscokbirl.vercel.app.
- Unauthenticated HTTPS requests to `/`, `/health`, `/ready`, `/api/overview` and `/api/accuracy` return 200. Production accuracy agrees with the local dataset.
- The production alias opens in the in-app browser without a Vercel authentication gate. Preview deployment protection is separate; share the production alias with reviewers.
- The serverless cache reconstructs from the source snapshot; no persistent write to Vercel's source filesystem is required.

## Remaining candidate work

Record and upload the required unlisted video, add its real URL to README, verify all three submission URLs in a private browser session, and submit the hiring form yourself. PostgreSQL integration and out-of-month forecast calibration are not claimed as verified.
