---
applyTo: "backend/**"
---

# Backend Engineering Instructions

> Stack: Python 3.13 · FastAPI 0.136.x · PostgreSQL 17 · pgvector · SQLAlchemy 2.0 · Pydantic v2 · Celery 5.4 · Redis 7.4 · Alembic

---

## 1. Python & Language Standards

- **Python 3.13+** minimum. Use modern syntax: `match`, `|` union types, `X | None` over `Optional[X]`.
- All functions, arguments, and return values must be **fully type-annotated**.
- Use `from __future__ import annotations` only when needed for forward references; prefer native PEP 604 syntax.
- Use `dataclasses` or Pydantic models for structured data — never raw `dict`.
- Prefer `pathlib.Path` over `os.path`. Prefer `datetime.UTC` over `datetime.utcnow()` (deprecated).

---

## 2. Project Structure (Clean Architecture)

```
backend/
├── app/
│   ├── api/                  # Route handlers only — no business logic
│   │   ├── v1/
│   │   │   ├── jobs.py
│   │   │   ├── applications.py
│   │   │   ├── resume.py
│   │   │   └── auth.py
│   │   └── deps.py           # Shared FastAPI dependencies
│   ├── core/                 # Config, security, startup
│   │   ├── config.py
│   │   ├── security.py
│   │   └── lifespan.py
│   ├── domain/               # Business logic — pure functions, no framework deps
│   │   ├── jobs/
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   ├── applications/
│   │   ├── resume/
│   │   └── ai/
│   ├── infrastructure/       # External systems: DB, Redis, AI APIs, email
│   │   ├── database.py
│   │   ├── redis.py
│   │   ├── openai_client.py
│   │   └── job_sources/
│   │       ├── jsearch.py
│   │       ├── adzuna.py
│   │       └── jobspy.py
│   ├── workers/              # Celery tasks
│   │   ├── celery_app.py
│   │   ├── job_ingestion.py
│   │   ├── email_monitor.py
│   │   └── reminders.py
│   └── models/               # SQLAlchemy ORM models
│       ├── user.py
│       ├── job.py
│       └── application.py
├── migrations/               # Alembic
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conftest.py
├── pyproject.toml
└── Dockerfile
```

### Core Principles

- **Separation of Concerns**: Route handlers (`api/`) call services (`domain/`). Services call repositories (`domain/*/repository.py`). Repositories call `infrastructure/`.
- **Dependency Inversion**: Services depend on repository interfaces (protocols), not concrete implementations.
- **Single Responsibility**: Each module has one reason to change. A job service should not know about HTTP or database connection pooling.
- **Open/Closed**: Extend behavior via composition and protocols, not inheritance chains.

---

## 3. FastAPI Conventions

### Route Handlers

Route handlers must be thin — validate input, call service, return response. No business logic inside routes.

```python
# ✅ CORRECT — thin handler
@router.post("/applications", response_model=ApplicationResponse, status_code=201)
async def create_application(
    payload: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationResponse:
    return await service.create(user_id=current_user.id, payload=payload)

# ❌ WRONG — business logic in route
@router.post("/applications")
async def create_application(payload: ApplicationCreate, db: AsyncSession = Depends(get_db)):
    # querying, validating, sending emails... all in route handler
    existing = await db.execute(select(Application).where(...))
    ...
```

### Versioning

- All routes live under `/api/v1/`. Never expose unversioned paths.
- Use `APIRouter` with prefix and tags for every domain module.

### Response Models

- Always declare `response_model` on all endpoints.
- Use separate `Create`, `Update`, `Response` Pydantic schemas. Never expose ORM models directly.
- Use `response_model_exclude_unset=True` for PATCH endpoints.

### Error Handling

- Raise `HTTPException` only in route handlers or exception handlers — never deep in services.
- Services raise domain exceptions (e.g., `JobNotFoundError`, `DuplicateApplicationError`).
- Register global exception handlers in `lifespan.py`.

```python
# domain exception
class JobNotFoundError(Exception):
    def __init__(self, job_id: int) -> None:
        self.job_id = job_id

# global handler in app setup
@app.exception_handler(JobNotFoundError)
async def job_not_found_handler(request: Request, exc: JobNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"Job {exc.job_id} not found"})
```

### Lifespan Events

Use the `lifespan` context manager (not deprecated `on_event`):

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    yield
    await database.disconnect()

app = FastAPI(lifespan=lifespan)
```

---

## 4. Pydantic v2 Models

- Use `model_config = ConfigDict(...)` — not deprecated `class Config`.
- Use `model_validator(mode="before" | "after")` for cross-field validation.
- Use `field_validator` with `@classmethod` decorator.
- Sensitive fields (passwords, tokens) must use `SecretStr`.

```python
from pydantic import BaseModel, ConfigDict, Field, SecretStr

class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(..., pattern=r"^[^@]+@[^@]+\.[^@]+$")
    password: SecretStr = Field(..., min_length=8)
```

---

## 5. SQLAlchemy 2.0 (Async)

- Use `async_sessionmaker` and `AsyncSession` exclusively.
- Never use raw SQL strings — always use ORM or Core expression language.
- Define models using the **declarative mapped column** style (not Column):

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime, func

class Base(DeclarativeBase):
    pass

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- Use `INSERT ... ON CONFLICT DO UPDATE` (upsert) for job deduplication — never `SELECT` then `INSERT`.
- Always use `index=True` on columns used in `WHERE` clauses.

### Session Management

```python
# ✅ Use dependency injection for session scoping
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

---

## 6. Authentication & Security (OWASP)

### JWT

- Tokens must be short-lived: access token 15 minutes, refresh token 7 days.
- Store refresh tokens in **httpOnly, Secure, SameSite=Strict** cookies — never localStorage.
- Use `python-jose` or `PyJWT` with RS256 (asymmetric) for production; HS256 acceptable for MVP.
- Always verify `exp`, `iat`, `aud` claims on every request.
- Use `secrets.compare_digest` for any string comparison that should be timing-safe.

```python
# ✅ Timing-safe token comparison
import secrets
if not secrets.compare_digest(provided_token, stored_token):
    raise HTTPException(status_code=401)
```

### Password Hashing

- Use **argon2-cffi** (preferred) or `bcrypt` via `passlib`. Never SHA-256 or MD5.

```python
from argon2 import PasswordHasher
ph = PasswordHasher()
hashed = ph.hash(password.get_secret_value())
ph.verify(hashed, plain_password)  # raises VerifyMismatchError on fail
```

### Input Validation (OWASP A03: Injection)

- All user inputs must be validated by Pydantic models at the API boundary.
- Never use string interpolation in SQL — only parameterized queries via SQLAlchemy ORM.
- Sanitize file uploads: validate MIME type server-side (not just extension), enforce size limits, store outside web root.
- For PDF parsing: run in a sandboxed subprocess or isolated container to prevent malicious PDF exploits.

### Rate Limiting (OWASP A05: Security Misconfiguration)

- Apply `slowapi` (rate limiting) on all auth endpoints: 5 attempts/minute per IP.
- Apply stricter limits on AI-heavy endpoints to prevent abuse and cost exhaustion.

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, payload: LoginRequest) -> TokenResponse:
    ...
```

### CORS

- Never use `allow_origins=["*"]` in production.
- Explicitly list allowed origins from environment config.

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,  # from env
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### Security Headers

Apply `secure` middleware or manually set:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Strict-Transport-Security` (HTTPS only)
- `Content-Security-Policy`
- `Referrer-Policy: strict-origin-when-cross-origin`

### Secrets Management (OWASP A02: Cryptographic Failures)

- All secrets via environment variables. Never hardcode credentials.
- Use `pydantic-settings` with `BaseSettings` — validates env vars on startup.
- Rotate secrets regularly. Log a warning if default/placeholder secrets are detected at startup.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str
    REDIS_URL: str
    OPENAI_API_KEY: SecretStr
    JWT_SECRET_KEY: SecretStr
    ALLOWED_ORIGINS: list[str] = []
```

---

## 7. Celery Workers

- Use `celery[redis]>=5.4` with Redis as broker and result backend.
- Define tasks in dedicated `workers/` modules — never inline with business logic.
- Always set task timeouts: `soft_time_limit` and `time_limit`.
- Implement exponential backoff with jitter for retries:

```python
@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=120,
    time_limit=180,
)
def ingest_jobs_task(self, user_id: int, search_config: dict) -> None:
    try:
        _run_ingestion(user_id, search_config)
    except RateLimitError as exc:
        # Exponential backoff with jitter
        delay = (2 ** self.request.retries) + random.uniform(0, 1)
        raise self.retry(exc=exc, countdown=delay)
    except Exception as exc:
        logger.exception("Job ingestion failed for user %s", user_id)
        raise self.retry(exc=exc, countdown=60)
```

- Use **task routing** to separate I/O-heavy tasks (ingestion) from quick tasks (reminders).
- Use **Celery Beat** for scheduled tasks. Store schedule in database (not in code) for runtime adjustability.
- Avoid passing large objects as task arguments — pass IDs and fetch from DB inside the task.

---

## 8. Race Conditions & Concurrency

### Database-Level Locking

For operations that must not run concurrently per user (e.g., two ingestion jobs for the same user):

```python
# Advisory lock: prevents concurrent execution per user_id
async with session.begin():
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": user_id}
    )
    # safe to proceed
```

### Optimistic Locking

For entities updated by multiple actors (e.g., application status):

```python
class Application(Base):
    version: Mapped[int] = mapped_column(default=1)

# On update:
result = await session.execute(
    update(Application)
    .where(Application.id == app_id, Application.version == expected_version)
    .values(status=new_status, version=Application.version + 1)
)
if result.rowcount == 0:
    raise ConcurrentUpdateError("Application was modified by another request")
```

### Redis Distributed Locks

For cross-process coordination (e.g., prevent duplicate Celery tasks):

```python
import redis.asyncio as aioredis
from contextlib import asynccontextmanager

@asynccontextmanager
async def distributed_lock(redis: aioredis.Redis, key: str, timeout: int = 30):
    acquired = await redis.set(f"lock:{key}", "1", nx=True, ex=timeout)
    if not acquired:
        raise LockNotAcquiredError(f"Could not acquire lock for {key}")
    try:
        yield
    finally:
        await redis.delete(f"lock:{key}")
```

---

## 9. External API Resilience (Job Sources)

- Every external API call must have a **timeout** (default: 10s), **retry logic** (max 3), and **circuit breaker** pattern.
- Use `tenacity` for declarative retry policies:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
)
async def fetch_jobs_from_jsearch(query: str, location: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://jsearch.p.rapidapi.com/search",
            params={"query": query, "location": location},
            headers={"X-RapidAPI-Key": settings.JSEARCH_API_KEY.get_secret_value()},
        )
        response.raise_for_status()
        return response.json()["data"]
```

- Log every external API failure with enough context to debug (URL, status code, latency).
- Never let a failed external API call crash the application or user-facing request. Fail gracefully.
- Store raw API responses in a `raw_responses` column for debugging and re-processing without re-fetching.

---

## 10. Database Migrations

- Use **Alembic** for all schema changes. Never modify schema in code at runtime.
- Every migration must have a `downgrade()` function.
- Migration file names must be descriptive: `2026_05_29_add_pgvector_index_to_jobs.py`.
- Test migrations in CI: `alembic upgrade head && alembic downgrade -1`.
- Never run `alembic upgrade head` in production without a tested rollback plan.

---

## 11. Testing

### Test Structure

```
tests/
├── unit/          # Pure function tests, no DB, no HTTP
├── integration/   # Tests with real DB (test container), no mocking infra
└── conftest.py    # Fixtures: test DB, test Redis, authenticated test client
```

### Principles

- Use `pytest-asyncio` for async tests.
- Use `httpx.AsyncClient` + `app` for integration tests — never `requests`.
- Use `pytest-docker` or `testcontainers-python` for ephemeral PostgreSQL/Redis in CI.
- Mock only external APIs (JSearch, OpenAI). Never mock your own infrastructure in integration tests.
- Every service function must have a unit test covering: happy path, not-found, validation failure, and concurrent access.
- Test security: unauthorized access returns 401, forbidden access returns 403, invalid input returns 422.

### Coverage

- Minimum 80% coverage required. CI fails below threshold.
- Use `pytest-cov` with `--cov-fail-under=80`.

---

## 12. Logging & Observability

- Use `structlog` for structured JSON logging in production.
- Log levels: `DEBUG` (dev only), `INFO` (normal operations), `WARNING` (recoverable issues), `ERROR` (failures requiring attention).
- Every log entry must include: `request_id`, `user_id` (if authenticated), `service`, `action`.
- Never log: passwords, tokens, full PII (email is acceptable if required for debugging; use a truncated form in logs).
- Add a `request_id` middleware that injects a UUID into every request context.

---

## 13. Environment & Dependencies

### Dependency Management

Use **uv** (fast Python package manager) with `pyproject.toml`:

```toml
[project]
name = "job-application-api"
requires-python = ">=3.13"
dependencies = [
    "fastapi[standard]>=0.136.0",
    "pydantic>=2.10",
    "pydantic-settings>=2.8",
    "sqlalchemy[asyncio]>=2.0.40",
    "asyncpg>=0.30",
    "pgvector>=0.3",
    "alembic>=1.14",
    "celery[redis]>=5.4",
    "redis>=5.2",
    "tenacity>=9.1",
    "httpx>=0.28",
    "python-jose[cryptography]>=3.3",
    "argon2-cffi>=23.1",
    "pdfplumber>=0.11",
    "python-docx>=1.1",
    "openai>=1.60",
    "slowapi>=0.1.9",
    "structlog>=25.1",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "pytest-cov>=6.1",
    "httpx>=0.28",
    "ruff>=0.9",
    "mypy>=1.14",
]
```

### Code Quality Tools

- **Ruff** for linting and formatting (replaces Flake8, isort, Black).
- **mypy** with `strict=true` for type checking.
- **pre-commit** hooks: ruff + mypy run on every commit.

```toml
[tool.ruff]
target-version = "py313"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "S", "B", "A"]
ignore = ["S101"]  # allow assert in tests

[tool.mypy]
python_version = "3.13"
strict = true
```

---

## 14. Code Style Quick Reference

```python
# ✅ DO: Early returns, no else after return
def get_salary_tier(salary: int) -> str:
    if salary < 50_000:
        return "entry"
    if salary < 120_000:
        return "mid"
    return "senior"

# ❌ DON'T: Nested else chains
def get_salary_tier(salary: int) -> str:
    if salary < 50_000:
        return "entry"
    else:
        if salary < 120_000:
            return "mid"
        else:
            return "senior"

# ✅ DO: Descriptive names, no magic numbers
MAX_RESUME_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# ✅ DO: Explicit keyword arguments for clarity
await job_service.search(
    query="software engineer",
    location="San Francisco",
    is_remote=True,
    max_results=50,
)

# ✅ DO: Context managers for resources
async with aiofiles.open(path, "rb") as f:
    content = await f.read()
```
