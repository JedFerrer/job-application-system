# Job application workspace

A source-visible portfolio project for organizing a job search. It combines a
FastAPI API, a Next.js dashboard, and a Chrome extension. Jobs, candidate
material, preparation decisions, and submission history belong to an account.
This is an engineering project, not a deployed job service.

## What it demonstrates

- Email/password registration and sign-in, session refresh, logout, and
  password reset. Google ID-token sign-in is optional.
- Job capture and import, duplicate identity handling, freshness and expiry,
  archived jobs, matching, and on-demand fit explanations.
- An application workspace to choose saved resume and cover-letter material,
  record manual questions, freeze a reviewable package, run advisory document
  review, and explicitly approve a package.
- Retained PDF/DOCX artifacts and downloads, submission confirmation with
  material provenance, timeline history, and interview preparation grounded in
  a historical package or explicit current-profile fallback.
- A Manifest V3 extension for supported job-page capture, account pairing, and
  safe autofill preview/action. It never submits applications automatically.

The user separately confirms an actual submission and the materials used.
Generated or reviewed documents do not imply submission.

## Architecture

| Part | Responsibility |
| --- | --- |
| Next.js 16, React 19, TypeScript | Dashboard and account UI; `/api/backend/*` proxies browser requests to FastAPI. |
| FastAPI, Python 3.13+ | Auth, owned jobs, matching, application preparation, package, review, artifact, and history APIs. Services hold domain rules; repositories handle persistence. |
| PostgreSQL 17 with pgvector | Account, job, application, and artifact metadata, plus vector matching; Alembic migrations. |
| Redis and Celery | Task state and background ingestion, ranking, maintenance, and document work. |
| Chrome Manifest V3 extension | Job capture, extension session pairing, form mapping, and safe fill. |

Package snapshots are immutable. Document review is advisory and cannot change
a package. Optional AI providers handle generation and analysis; the isolated
synthetic provider below covers document review and historical interview prep
only.

## Prerequisites and environment

Install Docker with Compose v2, Node.js 22, Python 3.13+, and `uv`. The
frontend and extension have committed lockfiles; use `npm ci`. The backend has
a committed `uv` lockfile. The guarded backend test runner needs a local Docker
engine.

Copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to
`frontend/.env.local`. Edit only the local copies, using invented identities
and obvious placeholders. For basic development, use the example's local
PostgreSQL/Redis URLs, `ENVIRONMENT=development`, a unique local JWT secret,
and the localhost origin. Set `BACKEND_INTERNAL_URL=http://localhost:8000` in
the frontend environment. Never commit environment files.

| Category | Settings |
| --- | --- |
| Basic local | `ENVIRONMENT`, `DATABASE_URL`, `REDIS_URL`, `ALLOWED_ORIGINS`, `JWT_SECRET`, `BACKEND_INTERNAL_URL`. |
| Optional AI | `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, embedding settings. Leave keys blank for offline checks. |
| Optional job source | `JSEARCH_API_KEY`, `ENABLE_JSEARCH`, source/search settings. Do not run external scraping during offline evaluation. |
| Optional sign-in and email | `GOOGLE_CLIENT_ID` and matching `NEXT_PUBLIC_GOOGLE_CLIENT_ID`; password reset defaults to local console delivery. Resend settings are for an intentional email integration test. |
| Isolated QA | `PORTFOLIO_SYNTHETIC_PROVIDERS` defaults off and requires `ENVIRONMENT=test` plus the `public_app_test` database name. Other `qa_*` flags are guarded internal tests. |
| Public deployment | Strong JWT secret, secure refresh cookies, HTTPS origins, and verified proxy settings. Deployment is outside this evaluation permission. |

## Run locally

From `backend/`, run `uv sync --dev` if dependencies are missing, then
`docker compose up -d`. The API container applies Alembic migrations on
startup. This default Compose stack uses persistent `job_app` for ordinary
development. **Never run destructive tests against it.** The API is at
`http://localhost:8000`.

From `frontend/`, run `npm ci` and `npm run dev`, then open
`http://localhost:3000`. Register a local account with an invented identity.
Without external AI keys, basic account and CRM surfaces remain available;
provider-backed generation and matching can report unavailable providers.

The extension is optional for the core dashboard. From `extension/`, run
`npm ci` and `npm run build`, then load `extension/dist` as an unpacked Chrome
extension. Pair it only with a local synthetic account and use an invented
local job page for capture. Do not fill or submit a real third-party form.

## Offline portfolio evaluation status

The `portfolio_synthetic_provider` returns deterministic, no-network
responses for **document review** and **application interview preparation**.
Its review has no fabricated findings; its interview questions cite source
locators without sending source text to an AI service. Normal development and
production startup never select it. Request data cannot activate it. An
explicit opt-in is refused outside `ENVIRONMENT=test` and the isolated
`public_app_test` database name. The ordinary auth and ownership checks remain.

The disposable test runner verifies fresh PostgreSQL and Redis resources and
cleans only resources whose ownership it can prove. The browser harness reuses
those checks for a local synthetic walkthrough. It refuses inherited database
and Compose overrides, verifies PostgreSQL cluster identity before migrations
or seeding, and binds its API and dashboard to loopback only. Do not set the
synthetic flag on `job_app` or an existing `job_app_test`.

From **`backend/`**, after installing the documented backend and frontend
dependencies, run:

```text
uv run --no-sync python scripts/run_portfolio_browser.py start
```

Wait for `READY http://127.0.0.1:18201`. Sign in there with the disposable
account `evaluator@example.com` and password `PortfolioEval2026!`. The harness
seeds Jordan Example's invented profile, a saved synthetic resume, and the
fictional Orbit Workshop job. It does not seed an application, package, review,
artifact, submission history, or interview questions. In the browser, open the
job, choose **Save & prepare**, acknowledge fit, select the saved resume, mark
the cover letter not needed, review and approve the package, run document
review, generate a resume artifact, record a **synthetic internal history entry**,
and prepare interview questions from that history. No external employer
application is submitted. The Chrome extension is optional for this core path.

In a second terminal in **`backend/`**, stop the exact active run using the ID
printed by `start`:

```text
uv run --no-sync python scripts/run_portfolio_browser.py stop RUN_ID
```

The owning process checks the API container, database, Redis, network, volumes,
and frontend process before removing only its own resources. If ownership or
cleanup verification fails, inspect the reported resources instead of running
an unguarded Compose teardown.

## Tests and quality checks

Run each command from the named directory. Database-backed backend tests are
guarded and must use the disposable runner. It checks the local Docker
context, Compose topology, resource identities, database names, and cleanup
ownership before destructive fixtures run. It refuses inherited database and
Compose overrides.

```text
backend: uv run ruff check .
backend: uv run mypy app tests
backend: uv run python scripts/select_public_format.py --mode release --check
backend: uv run python scripts/run_public_tests.py --smoke
backend: uv run python scripts/run_public_tests.py

frontend: npm ci
frontend: npm test
frontend: npm run lint
frontend: npx tsc --noEmit
frontend: npm run build

extension: npm ci
extension: npm test
extension: npx tsc --noEmit
extension: npm run lint
extension: npm run build
```

The format command checks selected changed backend Python files against the
repository audit baseline. Frontend lint runs with `npm run lint` and may
report warnings while exiting successfully. Extension lint is declared but its
ESLint setup is currently unreliable; do not treat it as
passing until verified. Browser E2E automation is not wired. Manual
desktop/mobile and extension Browser QA are separate evidence, not a result of
the commands above.

## Security, rights, and current limits

Account data is scoped by authenticated user ID. Browser refresh tokens are
httpOnly cookies; extension sessions use short-lived access tokens and
revocable refresh tokens. Autofill excludes passwords, files, hidden fields,
and unsafe fields without an approved mapping. Use synthetic data for
evaluation. Do not commit screenshots, cookies, tokens, or local test state.

The repository is not yet approved for publication. Hosted exposure review,
hosted QA fixture-name classification, hosted CI on cleanup commits, and a
fresh online Next.js security audit before visibility approval remain open.
Five extension Vitest-chain findings are accepted for development only.

[RIGHTS.md](RIGHTS.md) grants limited permission to download, configure,
build, test, and run an **unmodified local copy** solely to evaluate this
portfolio. **No open-source license is granted for this project.** Production,
business, hosted, commercial, derivative, and general modification or
redistribution uses require prior written permission. Third-party components
and assets retain their own licenses, including
[Noto Sans's SIL OFL notice](frontend/src/assets/fonts/LICENSE.txt).

The repository workflow contract is in [AGENTS.md](AGENTS.md) and
[docs/development-workflow.md](docs/development-workflow.md).
