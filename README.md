# AI Job Application CRM

An AI-assisted job search workspace with a FastAPI backend, a Next.js dashboard,
and a Chrome extension. The system imports and deduplicates jobs, ranks matches
against a parsed resume profile, tracks applications, generates cover letters
and interview prep, and supports extension-based job import and safe autofill.

## Current Codebase

- `backend/` - FastAPI API, SQLAlchemy models, Alembic migrations, Celery
  workers, domain services, repositories, LLM integrations, resume parsing,
  job scraping, and Redis-backed task state.
- `frontend/` - Next.js App Router dashboard with auth, jobs, matches,
  applications, resume/profile, preferences, settings, and extension pairing UI.
- `extension/` - Manifest V3 Chrome extension for detecting job pages, importing
  jobs, tracking application events, and previewing/applying approved autofill
  mappings.
- `docs/` - planning, audit, and implementation notes.

## Implemented Features

- Email/password auth, Google ID-token login, refresh-token cookies, logout, and
  password reset request/reset endpoints.
- Authenticated job ingestion from enabled job sources, deduplication, filtering,
  cursor pagination, manual/imported jobs, archive support, and Redis task
  polling.
- User job preferences with on-demand refresh, shared scrape queries, scheduled
  Celery scraping, match ranking, and old-job archival.
- Resume upload for PDF/DOCX, async parsing, profile read/update, embeddings, and
  resume tailoring for a job.
- Match ranking, match explanation, and skills-gap summary endpoints.
- Application CRM records with application status updates and extension-recorded
  application timelines/events.
- AI-generated cover letters and interview prep using the configured LLM
  provider chain.
- Chrome extension pairing, short-lived extension access tokens, refresh token
  rotation, job import by URL, application event logging, and safe form-field
  mapping/fill logging.

Not currently implemented as first-class product surfaces: inbox monitoring,
email classification, analytics endpoints, calendar integrations, or production
deployment automation.

## Tech Stack

### Backend

| Technology | Purpose |
|---|---|
| Python 3.13+ | Backend runtime |
| FastAPI | REST API |
| SQLAlchemy async + Alembic | Database access and migrations |
| PostgreSQL 17 + pgvector | Primary data store and vector search |
| Redis | Celery broker/result backend and task state |
| Celery | Scheduled/background scraping, maintenance, and ranking |
| OpenAI-compatible clients | LLM calls and embeddings |
| python-jobspy | Optional scraping dependency |
| Ruff, mypy, pytest | Linting, typing, tests |
| uv | Python dependency management |

### Frontend

| Technology | Purpose |
|---|---|
| Next.js 16 App Router | Web dashboard |
| React 19 | UI |
| TypeScript | Type safety |
| Tailwind CSS 4 | Styling |
| Radix UI primitives | Dialogs, menus, labels, scroll areas, tooltips |
| TanStack Query | Server state |
| React Hook Form + Zod | Forms and validation |
| Vitest | Unit tests |

### Extension

| Technology | Purpose |
|---|---|
| Chrome Manifest V3 | Extension platform |
| Vite | Extension build/watch |
| React | Popup UI |
| TypeScript | Shared popup/background/content-script types |
| Vitest | Unit tests |

## Prerequisites

- Docker 24+ and Docker Compose v2
- Node.js 22 LTS
- Python 3.13+
- `uv`
- Optional LLM key: `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, or
  `ANTHROPIC_API_KEY`
- Optional job API key: `JSEARCH_API_KEY`

## Environment

Create local env files from the examples:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

Important backend settings:

```env
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/job_app
REDIS_URL=redis://localhost:6379/0
ALLOWED_ORIGINS=["http://localhost:3000"]

JWT_SECRET=insecure-dev-secret-change-me
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7
REFRESH_COOKIE_SECURE=false
GOOGLE_CLIENT_ID=

LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
ANTHROPIC_API_KEY=
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536

JSEARCH_API_KEY=
ENABLE_JSEARCH=false
ENABLED_JOB_SOURCES=["indeed","linkedin","glassdoor","zip_recruiter"]
JOB_SEARCH_COUNTRY=USA
JOB_RESULTS_WANTED=50
```

Frontend settings:

```env
BACKEND_INTERNAL_URL=http://localhost:8000
NEXT_PUBLIC_GOOGLE_CLIENT_ID=
```

Staging and production are treated as public deployments. They require a
non-default JWT secret of at least 32 characters, secure refresh cookies, a
refresh cooldown of at least 15 minutes, and an explicit HTTPS-only
`ALLOWED_ORIGINS` list without localhost, private-network, or wildcard origins.
Development keeps the local HTTP defaults shown above.

## Run Locally

### Backend services

The Docker Compose file lives in `backend/` and starts PostgreSQL, Redis, the
FastAPI API, and a Celery worker with beat enabled.

```bash
cd backend
uv sync --dev
docker compose up -d
```

The API container runs `alembic upgrade head` on startup. To run migrations
manually:

```bash
cd backend
docker compose exec api alembic upgrade head
```

Authentication abuse controls use the existing Redis service under the
`auth-rate-limit:` key namespace. Redis-backed login, registration, password
reset, Google sign-in, and extension pairing limits fail closed with a generic
503 if Redis is unavailable; refresh-token rate checks alone fail open while
token validation and rotation remain enforced. Forwarded client IP headers are
ignored by default. Before a public deployment, set `TRUSTED_PROXY_CIDRS` only
to verified proxy networks and confirm Redis availability and eviction policy
are suitable for short-lived limiter keys. Do not copy provider-specific CIDRs
without verifying the selected deployment topology. The login identifier
bucket counts requests before password verification and is cleared only after
an uncontended successful login; the IP bucket is never cleared.

Local URLs:

| Service | URL |
|---|---|
| FastAPI | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

### Frontend dashboard

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000.

Browser API calls go through the Next.js proxy at `/api/backend/*`, which
forwards to `BACKEND_INTERNAL_URL/api/v1/*`.

### Chrome extension

```bash
cd extension
npm install
npm run dev
```

For a production build:

```bash
cd extension
npm run build
```

Load the generated `extension/dist` directory in Chrome as an unpacked
extension. The extension currently targets LinkedIn Jobs, Indeed, Greenhouse,
Lever, and Workday job pages.

## Test and Quality Commands

### Backend

```bash
cd backend
uv run pytest
uv run pytest tests/unit/ -v
uv run ruff check .
uv run ruff format .
uv run mypy app tests
docker compose run --rm test
```

The backend pytest config enforces `--cov=app --cov-report=term-missing
--cov-fail-under=80` by default. Integration tests are guarded so they do not
run against the application database by mistake.

### Frontend

```bash
cd frontend
npm test
npm run test:watch
npm run build
npx tsc --noEmit
```

There is no committed frontend lint script.

### Extension

```bash
cd extension
npm test
npm run lint
npm run build
```

The repository contains an extension E2E spec under `extension/tests/e2e`, but
there is no wired Playwright script in `extension/package.json`.

## API Quick Reference

All routes are mounted under `/api/v1`.

### Auth

```text
POST /auth/register
POST /auth/login
POST /auth/google
POST /auth/refresh
POST /auth/logout
GET  /auth/me
POST /auth/forgot-password
POST /auth/reset-password
```

### Jobs, Matches, Preferences

```text
POST  /jobs/ingest
GET   /jobs/tasks/{task_id}
GET   /jobs
GET   /jobs/imported
POST  /jobs/import
GET   /jobs/{job_id}
PATCH /jobs/{job_id}/archive

POST /matches/rank
GET  /matches
GET  /matches/skills-gap-summary
GET  /matches/{job_id}/explain

GET   /preferences
PUT   /preferences
PATCH /preferences
POST  /preferences/refresh
```

### Resume, Cover Letters, Interview Prep, Applications

```text
POST /resume
GET  /resume/tasks/{task_id}
GET  /resume/profile
PUT  /resume/profile
GET  /resume/tailor/{job_id}

GET   /cover-letters/by-job/{job_id}
POST  /cover-letters
GET   /cover-letters/{cover_letter_id}
PATCH /cover-letters/{cover_letter_id}

POST /interview-prep/{job_id}

POST  /applications
GET   /applications
PATCH /applications/{application_id}
```

### Extension

```text
POST /extension/auth/start
POST /extension/auth/complete
POST /extension/auth/refresh
POST /extension/auth/logout
GET  /extension/sessions

POST /extension/jobs/import
GET  /extension/jobs/by-url

POST /extension/applications/events
POST /extension/applications/mark-applied
GET  /extension/applications/{application_id}/timeline

GET  /extension/profile/autofill
POST /extension/forms/map-fields
POST /extension/forms/fill-log
```

### Health

```text
GET /health
GET /health/detailed
```

## Architecture Notes

- Routes are thin and delegate to domain services.
- Services contain business behavior and raise domain exceptions.
- Repositories own database access.
- Infrastructure modules own external clients such as Redis, LLMs, embeddings,
  resume parsing, and job sources.
- Workers run background ingestion, maintenance, and ranking using IDs and
  repositories/services.
- Frontend browser calls use `frontend/src/lib/api.ts` and the
  `/api/backend/*` proxy.
- Extension shared code stays side-effect-light and is reused across popup,
  background, and content scripts.

## Security Notes

- Do not commit `.env` files.
- Browser access tokens are held in memory; refresh tokens are httpOnly cookies.
- Extension sessions use short-lived access tokens and revocable refresh tokens.
- Passwords are hashed with Argon2.
- Auth and extension pairing routes are rate-limited.
- User-owned resources are scoped by authenticated user ID.
- Autofill must not fill passwords, files, hidden fields, or unsafe/sensitive
  fields without explicit safe mapping.

## Development Workflow

Codex follows the canonical contract in [AGENTS.md](AGENTS.md). The reusable
workflow, handoff templates, validation guidance, Browser QA contract, and
review verdicts are in
[docs/development-workflow.md](docs/development-workflow.md).

Browser QA is a separate manual/browser-capable stage when acceptance criteria
require real browser interaction or visual verification. Runnable browser E2E
automation is not currently wired in this repository.
