# Phase 5E1 — Fit-ranking rollout and QA runbook

## Purpose and scope

This is a local or staging-like rollout and manual-QA runbook for
`MATCH_RANKING_MODE=fit`. The default ranking mode remains `shadow`. Phase 5E1
adds documentation only; it does not change runtime behavior, defaults, APIs,
schemas, or the frontend. Execute the checks in a separate Phase 5E2 QA pass.

## Ranking settings

Set these values in the environment used by both the API and worker. Keep the
candidate limits, RRF value, final limit, and scorer version fixed while
comparing modes.

```text
MATCH_RANKING_MODE=vector|shadow|fit   # default: shadow
MATCH_VECTOR_CANDIDATE_LIMIT=400
MATCH_TEXT_CANDIDATE_LIMIT=400
MATCH_SKILL_CANDIDATE_LIMIT=400
MATCH_FINAL_LIMIT=200
MATCH_RRF_K=60
MATCH_SCORER_VERSION=fit-scorer-v1
```

Changing the mode requires restarting both API and worker processes. Do not
make `fit` the default as part of QA.

## Safe setup

- Use a dedicated local/staging synthetic account.
- Use fictional profiles and fictional jobs only. Never use a real resume,
  real job description, copied posting, contact data, or personal history.
- Seed enough active, embedded fictional jobs for multiple pages. Include:
  vector-versus-fit order differences, at least three distinct ranking scores,
  an equal-score job-ID tie, one manual match, and archived, private, and
  inactive negative cases.
- Keep the synthetic profile and jobs disposable; do not include their text in
  tickets, screenshots, logs, or copied API output.

## Preflight checks

Run the backend checks before browser QA:

```powershell
cd backend
docker compose run --rm test sh -c "alembic upgrade head && python -m pytest tests/unit/test_match_ranking_config.py tests/unit/test_match_ranking_retention.py tests/unit/test_match_pagination.py tests/unit/test_match_endpoint_contract.py tests/unit/test_match_authorization.py tests/unit/test_hybrid_retrieval.py tests/unit/test_match_scorer.py tests/unit/test_match_ranking_evaluation.py -v --tb=short --no-header -o 'addopts='"
docker compose run --rm test
docker compose run --rm test ruff check .
docker compose run --rm test mypy app tests
```

Run frontend checks before browser execution:

```powershell
cd frontend
npm test
npx tsc --noEmit
npm run build
```

Repository-wide Ruff and mypy may report documented baseline violations. Record
exact files and errors; classify them as Phase 5E failures only when this phase
changed the affected file or behavior. Do not suppress, skip, or reclassify a
new error as baseline without evidence.

Start a focused local environment without beat to avoid scheduled interference:

```powershell
cd backend
docker compose up -d db redis api worker
cd ..\frontend
npm run dev
```

## Mode expectations

| Mode | Candidate source | Listing and pruning | Cursor | Visible results and logs |
| --- | --- | --- | --- | --- |
| `vector` | Vector retrieval only | `vector_score` descending; `MATCH_FINAL_LIMIT`; manual rows retained | Legacy vector cursor | Vector-visible rows only; `matches.ranked` |
| `shadow` | Vector-visible results plus private hybrid computation | Vector ordering/pruning unchanged | Legacy vector cursor | Only vector rows are persisted/surfaced; one aggregate `matches.shadow_metrics` event plus safe degraded-branch events if applicable |
| `fit` | Hybrid/RRF candidate pool | `ranking_score` descending, job-ID tie-break; `MATCH_FINAL_LIMIT`; null ranking scores excluded from listing; manual rows retained | Namespaced `fit|score|job_id` cursor | Eligible hybrid-only rows may surface; `fit_score` powers Fit display and `vector_score` remains diagnostic; `matches.ranked` and safe degraded-branch events |

For every mode, authorization, ownership, archived, inactive, and embedding
eligibility filters must remain enforced.

## API QA checklist

Use an authenticated synthetic account and repeat the relevant checks after a
fresh ranking in each mode:

- `POST /api/v1/matches/rank` returns `202` and a task ID.
- `GET /api/v1/matches/tasks/{task_id}` reaches the completed state.
- `GET /api/v1/matches?limit=...` returns correct ordering, totals, `has_more`,
  and next-cursor behavior.
- Fetching a second page produces no duplicates and preserves tie ordering.
- `GET /api/v1/matches/{job_id}/explain` remains authorized and compatible.
- `GET /api/v1/matches/{job_id}/evidence` remains authorized and compatible.
- `GET /api/v1/matches/skills-gap-summary` remains compatible.
- Hidden, private-to-another-user, archived, inactive, and unembedded jobs are
  not returned.
- Fit cursors are rejected after switching to vector or shadow; vector cursors
  are rejected in fit mode. Reload from page one after a mode change.

## Browser QA checklist

At `http://localhost:3000/dashboard/matches`, verify in desktop and narrow
viewports:

- loading, empty, ranking-in-progress, completed, and ranking-failure states;
- **Refresh Match Rankings** starts task polling and shows the success toast;
- deterministic **Fit** display remains visible where `fit_score` exists;
- diagnostic **Relevance** remains visible from `vector_score`;
- detail/evidence navigation, skill-gap display, and archive action;
- infinite-scroll pagination, no duplicate cards, and all-matches-loaded state;
- no unauthorized or lifecycle-ineligible cards;
- browser console has no new errors; Network requests have no unexpected 4xx/5xx.

Capture only synthetic IDs and scores. Do not capture profile/job text.

## Safe structured-log review

Review only structured fields for these events:

- `matches.shadow_metrics`
- `matches.hybrid_retrieval_branch_failed`
- `matches.hybrid_vector_enrichment_failed`
- `matches.ranked`

Logs may contain event names, mode/branch, safe error class names, aggregate
counts, and other explicitly safe numeric fields. They must not contain profile
text, job text, retrieval query text, exception messages, SQL parameters,
evidence excerpts, tracebacks, or personal data.

## Rollback to vector

1. Save a fit-mode next cursor from a test response.
2. Set `MATCH_RANKING_MODE=vector`.
3. Restart both API and worker.
4. Reuse the saved fit cursor and verify `400 Invalid cursor`.
5. Hard-reload `/dashboard/matches` from page one.
6. Trigger a fresh vector ranking; persisted fit rows may otherwise remain until
   the next ranking/pruning cycle.
7. Verify vector-score ordering, legacy cursors, manual-row retention, and that
   hybrid-only fit candidates are not surfaced.

Rollback requires no database migration or rollback.

## Evidence template

```text
Environment / commit:
Synthetic account and data shape:
Automated backend checks: PASS / FAIL / BLOCKED
Frontend tests/typecheck/build: PASS / FAIL / BLOCKED
Vector API QA: PASS / FAIL / BLOCKED
Shadow API/log QA: PASS / FAIL / BLOCKED
Fit API QA: PASS / FAIL / BLOCKED
Browser QA: PASS / FAIL / BLOCKED
Rollback QA: PASS / FAIL / BLOCKED
Screenshots or synthetic IDs/notes:
Console/network findings:
Blockers and exact errors:
Final recommendation: proceed / hold / investigate
```

## Known caveats

- The backend suite may emit the existing Starlette/httpx `TestClient` deprecation
  warning.
- Repository-wide Ruff and mypy have documented baseline failures; report exact
  output and do not attribute unchanged baseline files to Phase 5E.
- Targeted mypy may still report the established untyped-test baseline in
  matching pagination/retention tests. This is not a Phase 5E failure unless the
  runbook or QA changes those files.
- A mode switch changes how new rankings are produced; always run a fresh
  ranking after switching modes and reload the first page before comparing.
