# Backend

FastAPI owns authentication, account-scoped jobs/documents, matching, application
preparation, artifacts, history, and interview APIs. Celery runs background
tasks; PostgreSQL stores records/vectors; Redis supports task state and delivery.

See the [system overview](../README.md), [dashboard](../frontend/README.md), and
[extension](../extension/README.md). [RIGHTS.md](../RIGHTS.md) permits unmodified
local evaluation; editing instructions are for authorized contributors.

## Stack and prerequisites

- Python **3.13**, `uv`, Docker with Compose v2, and a running local Docker engine.
- FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL 17/pgvector, Redis 7.4, Celery.
  [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock) define dependencies.
- Node.js 22 and installed frontend dependencies for the portfolio browser harness.

Use Python 3.13 to match [Dockerfile](Dockerfile) and [CI](../.github/workflows/quality.yml).
The broader metadata constraint does not guarantee newer interpreter support
from all locked binary dependencies.

## Setup and start

Run commands from `backend/`, unless stated otherwise.

1. Copy [.env.example](.env.example) to `.env` **only if the local file does
   not already exist**. Preserve existing configuration. On PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

   On macOS/Linux:

   ```sh
   cp .env.example .env
   ```

   Keep `ENVIRONMENT=development`, replace `JWT_SECRET` with a unique local
   secret, and leave provider keys blank unless intentionally using a service.
2. Install locked host dependencies:

   ```sh
   uv sync --locked --dev --python 3.13
   ```

   This creates `backend/.venv` for tooling and frontend interoperability tests.
   The containers install their own dependencies, including the scraping extra.
3. Start local services:

   ```sh
   docker compose up -d --build
   docker compose ps
   docker compose logs --tail 100 api worker
   ```

   Compose starts `db`, `redis`, `api`, and `worker`. API startup applies
   `alembic upgrade head` before Uvicorn. This changes the persistent development
   database; do not point the topology at production. Wait for successful startup
   in the logs before opening the dashboard.
4. Open `http://localhost:8000/api/v1/health` for `{"status":"ok"}`.
   Check `http://localhost:8000/api/v1/health/detailed` and inspect
   `checks.database` for DB readiness; the basic endpoint alone does not verify
   dependencies. Detailed health checks the database, not Redis or the worker.
   Interactive API documentation is at `http://localhost:8000/docs`.
5. Continue with [frontend setup](../frontend/README.md#setup-and-start).
   Register an invented account; ordinary development does not assume a seeded user.

### Configuration boundaries

| Settings | Local use |
| --- | --- |
| `DATABASE_URL`, `REDIS_URL` | Example localhost values serve host processes; Compose overrides them with `db`/`redis` service addresses |
| `JWT_SECRET`, `ALLOWED_ORIGINS` | Unique local secret and dashboard origin, normally `http://localhost:3000` |
| `LLM_PROVIDER`, `LLM_MODEL`, provider keys, embedding settings | External generation/analysis; blank keys do not provide complete offline AI |
| `JSEARCH_API_KEY` and source settings | Optional external job-source configuration |
| `GOOGLE_CLIENT_ID` | Optional; must match the frontend's public Google client ID |
| `EMAIL_DELIVERY_MODE` | Console mode avoids external email; development reset links are returned in the API response and displayed by the dashboard, not printed to console logs |
| `PORTFOLIO_SYNTHETIC_PROVIDERS` | Keep off here; only the guarded isolated evaluation profile may enable it |

The [environment example](.env.example) is the setup reference;
[Settings](app/core/config.py) defines supported values. Never commit `.env`.
Default Compose publishes `5432`, `6379`, and `8000` without loopback-only
bindings; use a trusted local machine and do not expose the stack publicly.

## Source structure and request flow

```text
backend/
├── app/
│   ├── main.py              # App assembly, middleware, routes
│   ├── api/                 # HTTP endpoints and injected dependencies
│   ├── core/                # Settings, security, startup, logging, rate limits
│   ├── domain/              # Feature services, repositories, schemas, rules
│   ├── infrastructure/      # Database, Redis, AI, email, parser, job-source adapters
│   ├── models/              # SQLAlchemy persistence models
│   └── workers/             # Celery app, ingestion, ranking, maintenance
├── migrations/              # Alembic environment and schema revisions
├── scripts/                 # Guarded evaluation/test runners and validation helpers
├── tests/
│   ├── unit/                # Focused behavior and contract tests
│   ├── integration/         # Persistence, concurrency, migrations
│   └── fixtures/            # Synthetic evaluation inputs
├── docker-compose.yml       # Persistent local development services
└── pyproject.toml           # Dependencies and quality/test settings
```

Typical flow: route in `api/v1` → authenticated dependencies in `api/deps.py` →
domain service → repository → database. Infrastructure adapters supply external
capabilities. Background workflows dispatch through Redis to Celery, which uses
domain logic/persistence outside the request lifecycle.

Keep HTTP contracts in routes/schemas, business decisions in services, queries
in repositories, and provider-specific behavior in infrastructure. Preserve
account ownership and immutable package/submission provenance.

## Development operations

The current API command has **no automatic reload**. After authorized edits
to mounted source, restart the API and/or worker:

```sh
docker compose restart api worker
```

For dependency or Dockerfile changes, rebuild affected images:

```sh
docker compose up -d --build api worker
```

API restarts also run the startup migration command. Review and authorize
schema changes first. Alembic revisions are in [migrations/versions](migrations/versions);
do not replace migration history with automatic table creation.

Inspect services or stop without deleting persisted volumes:

```sh
docker compose ps
docker compose logs --tail 100 api worker
docker compose stop
```

Use the startup command again to resume. Do not delete volumes as routine
troubleshooting: `pgdata` contains development data.

## Tests and quality checks

Install host dependencies first. Run from `backend/`:

Use a normal Git clone with project history available. The full runner records
the local `main` ref for provenance; the format selector also needs its
historical audit commit. Verify those refs before checks:

```sh
git rev-parse --verify refs/heads/main
git rev-parse --verify 03c3c4279e18165dc5cc1b4f10d3ed6283710cbd
```

If a shallow or single-branch clone lacks them, obtain the required history
and local `main` ref from the trusted repository before running those tools;
do not change runner guards or fabricate a baseline to make checks pass.

| Check | Command |
| --- | --- |
| Lint | `uv run ruff check .` |
| Types | `uv run mypy app tests` |
| Selected formatting | `uv run python scripts/select_public_format.py --mode release --check` |
| Isolated DB smoke tests | `uv run python scripts/run_public_tests.py --smoke` |
| Full isolated suite | `uv run python scripts/run_public_tests.py` |

The format selector checks selected changed Python files against its audit
baseline, not every Python file.

Use the [public test runner](scripts/run_public_tests.py) for DB-backed tests.
It creates fresh application-test and migration-test PostgreSQL resources plus
Redis, verifies identities, runs tests, and checks ownership before cleanup.
It refuses inherited database/Compose overrides and remote Docker settings.
Never set test URLs to `job_app` or run destructive fixtures against a
development database. Smoke mode is not the full suite.

[CI](../.github/workflows/quality.yml) defines automated checks. For synthetic
browser evaluation, follow the [root walkthrough](../README.md#isolated-portfolio-walkthrough).
Browser QA is separate from pytest results.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Binary dependency fails to install | Use Python 3.13 and the lockfile; do not regenerate dependencies for an unintended interpreter |
| Docker command fails | Start the local engine and verify Compose v2 |
| Port conflict | Inspect `5432`, `6379`, `8000`; stop only services you own |
| Health works but data requests fail | Inspect detailed DB health, migration logs, authentication, and frontend API URL |
| Background task stays pending | Check worker/Redis status and logs; health does not verify the worker |
| Source edit has no effect | Restart API/worker; rebuild for image/dependency changes |
| Isolated runner refuses startup/cleanup | Inspect its guard report, inherited environment, and Docker context; do not bypass ownership checks |
| AI operation unavailable | Verify provider configuration; the isolated harness covers only its documented synthetic features |

## Workflow and verification status

Follow [AGENTS.md](../AGENTS.md) and the
[development workflow](../docs/development-workflow.md) for authorized changes.
Use repository Issues with sanitized diagnostics for support. This guide was
checked statically; no fresh install, services, migrations, or browser
walkthrough were executed for this documentation update.
