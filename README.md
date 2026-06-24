# AI Job Application Operations System

> Automate and optimize your entire job search — from discovery to offer.

---

## The Problem

Job searching in 2026 is broken. A serious job seeker manages:

- **12+ job boards** with overlapping, duplicate listings
- A **custom resume** tailored to each job description
- **Cover letters** that take 30–45 minutes each to write well
- **Application tracking** scattered across spreadsheets, sticky notes, and browser tabs
- **Inbox chaos** — offer emails, rejection emails, recruiter outreach, and follow-up reminders all mixed together
- **Zero visibility** into application pipeline status or response rates by company/role

The result: hundreds of hours wasted on manual, repetitive work. Promising roles slip through the cracks. The job seeker is reactive instead of strategic.

## How We Solve It

A centralized **AI-powered job application CRM** that:

1. **Ingests jobs automatically** from LinkedIn, Indeed, Glassdoor, ZipRecruiter, and more
2. **Deduplicates** across sources — see each role once, regardless of how many boards post it
3. **Scores job-resume match** using AI vector similarity (JD vs. your resume)
4. **Tailors your resume** for each job with one click (preserves your authentic voice)
5. **Generates cover letters** grounded in your experience and the job description
6. **Tracks every application** with status, notes, and timeline
7. **Monitors your inbox** for recruiter replies, interviews, offers, and rejections
8. **Sends you reminders** to follow up before you go cold

---

## Tech Stack

### Backend

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.13+ | Runtime |
| FastAPI | 0.136.x | REST API framework |
| PostgreSQL | 17 | Primary database |
| pgvector | 0.8.0+ | Vector similarity search (JD ↔ resume matching) |
| SQLAlchemy | 2.0+ | Async ORM |
| Alembic | 1.14+ | Database migrations |
| Celery | 5.4+ | Background task workers |
| Redis | 7.4 | Cache + Celery broker/result backend |
| OpenAI SDK | 1.x | GPT-4o (tailoring) + text-embedding-3-small (embeddings) |
| python-jobspy | 1.1.x | Open-source job scraper (LinkedIn, Indeed, Glassdoor, ZipRecruiter) |
| JSearch API | RapidAPI | Commercial job search API (Google for Jobs aggregator) |
| tenacity | 9.x | Retry with exponential backoff for external APIs |
| Ruff | 0.12+ | Linter + formatter |
| uv | latest | Python package manager |

### Frontend

| Technology | Version | Purpose |
|---|---|---|
| Next.js | 16.2 | App Router, SSR, Server Actions |
| React | 19.2 | UI library (React Compiler enabled) |
| TypeScript | 5.x | Type safety |
| TailwindCSS | 4.x | Utility-first styling |
| shadcn/ui | latest | Component library |
| TanStack Query | v5 | Server state, caching, optimistic updates |
| Zustand | 5.x | Client-side UI state |
| Zod | 3.x | Schema validation |
| React Hook Form | 7.x | Form management |

### Infrastructure

| Technology | Purpose |
|---|---|
| Docker Compose | Local development orchestration |
| Nginx | Reverse proxy |
| Railway / Render | Initial production deployment |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                           │
│           Next.js 16.2 (App Router + React 19.2)               │
│   Server Components → RSC Payload → Client Hydration           │
└────────────────────────┬───────────────────────────────────────┘
                         │ HTTPS / httpOnly cookies
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   FastAPI 0.136.x (Python 3.13)                 │
│  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌───────────┐  │
│  │ /auth    │  │ /jobs        │  │/apply    │  │ /resume   │  │
│  │ JWT      │  │ search/ingest│  │ CRM CRUD │  │ AI tailor │  │
│  └──────────┘  └──────────────┘  └──────────┘  └───────────┘  │
│                          │                                      │
│                   Service Layer                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ JobService │ ApplicationService │ ResumeAIService        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                          │                                      │
│                   Repository Layer                               │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ JobRepo │ ApplicationRepo │ UserRepo │ ResumeRepo       │    │
│  └────────────────────────────────────────────────────────┘    │
└─────────┬──────────────────────────────────┬───────────────────┘
          │                                  │
          ▼                                  ▼
┌────────────────────┐          ┌────────────────────────────────┐
│  PostgreSQL 17     │          │  Redis 7.4                     │
│  + pgvector 0.8    │          │  ├── Celery broker             │
│                    │          │  ├── Result backend            │
│  Tables:           │          │  └── Cache layer               │
│  - users           │          └────────────────────────────────┘
│  - jobs            │                        │
│  - applications    │          ┌─────────────▼──────────────────┐
│  - resumes         │          │  Celery Workers                │
│  - embeddings      │          │  ├── job_ingestion (beat)      │
└────────────────────┘          │  ├── email_monitor             │
                                │  └── reminders                 │
                                └────────────────────────────────┘
                                               │
                          ┌────────────────────┴────────────────┐
                          │         External APIs               │
                          │  ├── JSearch (RapidAPI)             │
                          │  ├── python-jobspy (open source)    │
                          │  ├── OpenAI (GPT-4o + embeddings)   │
                          │  └── IMAP/Gmail API                 │
                          └─────────────────────────────────────┘
```

---

## MVP Features

### 1. Job Aggregation Engine
Automatically scrapes and ingests job listings from LinkedIn, Indeed, Glassdoor, ZipRecruiter, and Google for Jobs. Deduplicates across sources using content fingerprinting. Stores structured data: title, company, location, salary, JD text, URL, source.

### 2. AI Job-Resume Match Scoring
Converts job descriptions and your resume into vector embeddings (OpenAI `text-embedding-3-small`). Calculates cosine similarity to score each job on how well it matches your experience. Surface jobs by match score so you apply to roles where you'll succeed.

### 3. AI Resume Tailoring
With one click, GPT-4o rewrites your resume bullets to highlight experience most relevant to the specific job description. Preserves your voice and factual accuracy — no hallucinations. Output is diff-viewable so you can accept/reject each change.

### 4. AI Cover Letter Generator
Generates a tailored, professional cover letter for any job in your pipeline. Grounded in your resume and the specific JD. Editable before sending.

### 5. Application CRM
Track every application through its lifecycle: `saved → applied → phone_screen → interview → offer → rejected`. Add notes, interview dates, recruiter names, and salary expectations. Timeline view per application.

### 6. Smart Inbox Monitor
Monitors your email (IMAP / Gmail API) for messages related to your applications. Classifies emails: recruiter outreach, interview invite, rejection, offer, follow-up needed. Surfaces action items to your dashboard.

### 7. Follow-up Reminders
Automatically schedules reminders to follow up after applying (7 days), after a phone screen (3 days), and before interview deadlines. Sends push notifications or emails.

### 8. Analytics Dashboard
Pipeline funnel: applications → phone screens → interviews → offers. Response rate by company, role type, and resume version. Average days-to-response by source.

---

## Feature Roadmap

### Phase 1 — MVP (Current)
- [x] User auth (register, login, JWT refresh)
- [x] Job ingestion from 4+ sources with deduplication
- [x] Job search and filtering
- [x] Application CRM (CRUD + status tracking)
- [x] Resume upload (PDF + DOCX parsing)
- [x] AI match scoring (pgvector cosine similarity)
- [x] AI resume tailoring (GPT-4o)
- [x] AI cover letter generation
- [x] Email inbox monitoring (IMAP)
- [x] Follow-up reminders (Celery Beat)
- [x] Analytics dashboard (pipeline funnel)

### Phase 2 — Intelligence Layer
- [ ] Multi-resume management (version control with diff viewer)
- [ ] Job board search preferences per user (saved searches)
- [ ] Company research assistant (Glassdoor reviews, news)
- [ ] Interview preparation: AI generates likely questions based on JD
- [ ] Recruiter contact enrichment (find recruiter email from LinkedIn)
- [ ] Browser extension for one-click save from any job board

### Phase 3 — Automation & Integrations
- [ ] Auto-apply to easy-apply roles (LinkedIn Easy Apply, Indeed One-Click)
- [ ] Gmail full integration (OAuth, auto-label, auto-archive)
- [ ] Calendar integration (auto-add interview slots to Google Calendar)
- [ ] Slack / Discord notifications for new high-match jobs
- [ ] ATS document export (formatted resume to DOCX/PDF on demand)
- [ ] Zapier / Make webhook triggers for custom automations

### Phase 4 — Multi-Agent Architecture
- [ ] LangGraph orchestration for complex multi-step AI workflows
- [ ] Temporal for durable, fault-tolerant long-running workflows
- [ ] Agent: autonomous job researcher (deep company intel gathering)
- [ ] Agent: salary negotiation coach with market data
- [ ] Agent: post-interview thank-you email drafter
- [ ] Multi-tenant SaaS (coaching firms, bootcamps, career services)

---

## Prerequisites

- **Docker** 24.x and **Docker Compose** v2
- **Node.js** 22 LTS (with `nvm`)
- **Python** 3.13+ (with `uv`)
- **Git**
- **JSearch API key** — free tier at [rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
- **OpenAI API key** — [platform.openai.com](https://platform.openai.com)

---

## How to Run Locally

### 1. Clone and configure environment

```bash
git clone https://github.com/your-org/job-application-system.git
cd job-application-system

# Backend env
cp backend/.env.example backend/.env
# Frontend env
cp frontend/.env.example frontend/.env.local
```

Edit `backend/.env`:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/jobapp
REDIS_URL=redis://redis:6379/0

# Auth
JWT_SECRET_KEY=your-secret-key-change-in-production-min-32-chars
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# AI
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o

# Job Sources
JSEARCH_API_KEY=your-rapidapi-key

# CORS
ALLOWED_ORIGINS=["http://localhost:3000"]
```

Edit `frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 2. Start all services

```bash
docker compose up -d
```

This starts:
- `db` — PostgreSQL 17 on port `5432`
- `redis` — Redis 7.4 on port `6379`
- `api` — FastAPI on port `8000`
- `worker` — Celery worker
- `beat` — Celery Beat (scheduler)
- `frontend` — Next.js dev server on port `3000`

### 3. Run database migrations

```bash
docker compose exec api alembic upgrade head
```

### 4. Create initial data (optional)

```bash
# Seed a test user
docker compose exec api python scripts/seed.py

# Trigger manual job ingestion for testing
docker compose exec api python scripts/ingest_jobs.py --query "software engineer" --location "Remote"
```

### 5. Access the app

| Service | URL |
|---------|-----|
| Frontend | [http://localhost:3000](http://localhost:3000) |
| API Docs (Swagger) | [http://localhost:8000/docs](http://localhost:8000/docs) |
| API Docs (ReDoc) | [http://localhost:8000/redoc](http://localhost:8000/redoc) |

---

## How to Test

### Backend

```bash
# All tests
docker compose exec api pytest

# With coverage report
docker compose exec api pytest --cov=app --cov-report=html --cov-fail-under=80

# Unit tests only (fast — no DB)
docker compose exec api pytest tests/unit/ -v

# Integration tests (requires running DB and Redis)
docker compose exec api pytest tests/integration/ -v

# Specific test file
docker compose exec api pytest tests/unit/test_ai_service.py -v
```

### Frontend

```bash
# All tests
docker compose exec frontend npm test

# Watch mode
docker compose exec frontend npm run test:watch

# Coverage
docker compose exec frontend npm run test:coverage

# Type checking
docker compose exec frontend npx tsc --noEmit

# Lint
docker compose exec frontend npx eslint .
```

### End-to-End (Phase 2)

Playwright E2E tests will cover the critical flows: register → search jobs → apply → track application.

```bash
# (Phase 2)
npx playwright test
```

---

## Job Data Sources

The system uses a tiered approach to maximize job coverage at minimal cost:

| Tier | Source | Method | Cost | Notes |
|------|--------|--------|------|-------|
| 1 | JSearch (RapidAPI) | REST API | Free 200 req/mo; $25/mo Pro | Google for Jobs aggregator — highest quality |
| 2 | python-jobspy | Library scraping | Free, open source | LinkedIn, Indeed, Glassdoor, ZipRecruiter |
| 3 | RSS / Atom feeds | RSS parsing | Free | StackOverflow Jobs, RemoteOK, WeWorkRemotely |
| 4 | Apify actors | Cloud scraping | Pay-per-use | Fallback for blocked sources |

**Deduplication**: Every job gets a SHA-1 fingerprint of `normalize(title + company + location)`. Upserted on conflict — no duplicate listings across sources.

---

## API Quick Reference

```
POST   /api/v1/auth/register          Register new user
POST   /api/v1/auth/login             Login, returns access + refresh token
POST   /api/v1/auth/refresh           Refresh access token

GET    /api/v1/jobs                   Search/list jobs with filters
GET    /api/v1/jobs/{id}              Get job details
POST   /api/v1/jobs/ingest            Trigger manual job ingestion (authenticated)

GET    /api/v1/applications           List applications (paginated, filterable)
POST   /api/v1/applications           Create application
PATCH  /api/v1/applications/{id}      Update status, notes, etc.
DELETE /api/v1/applications/{id}      Archive application

POST   /api/v1/resume/upload          Upload resume (PDF or DOCX)
GET    /api/v1/resume/{id}            Get parsed resume
POST   /api/v1/resume/{id}/tailor     AI tailor resume for a specific job
POST   /api/v1/resume/{id}/cover-letter   Generate cover letter for a job

GET    /api/v1/analytics/pipeline     Application funnel metrics
GET    /api/v1/analytics/response-rates   Response rate by company/source
```

---

## Contributing

### Branch Naming

```
feature/short-description
fix/short-description
chore/short-description
```

### Commit Messages (Conventional Commits)

```
feat: add AI resume tailoring endpoint
fix: correct pagination offset in job search
chore: update OpenAI SDK to 1.60
test: add integration tests for application CRM
```

### Pull Request Checklist

- [ ] Tests pass (`pytest` + `npm test`)
- [ ] Coverage above 80%
- [ ] No `any` types in TypeScript
- [ ] No new Ruff/mypy violations
- [ ] `alembic upgrade head` runs cleanly (if DB schema changed)
- [ ] Env var additions documented in `.env.example`
- [ ] PR description explains the change and how to test it

---

## Security Notes

- Never commit `.env` files — they are in `.gitignore`.
- All auth tokens are stored in `httpOnly` cookies — not `localStorage`.
- Passwords are hashed with Argon2 — never stored in plaintext.
- All SQL queries use parameterized statements via SQLAlchemy ORM.
- Rate limiting applied to auth endpoints (5 req/min per IP).
- CSP headers set in Next.js middleware.
- CORS explicitly restricts allowed origins.

For vulnerability reports, open a private GitHub Security Advisory.

---

## License

MIT — see [LICENSE](LICENSE)
