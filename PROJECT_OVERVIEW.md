# AI Job Application Operations System — Project Overview

> **A CRM + AI recruiter assistant + automation engine built for job seekers.**
> Transforms a painful, fragmented job search into a single intelligent operations hub.

---

## Table of Contents

1. [What Is This Project?](#what-is-this-project)
2. [The Problem It Solves](#the-problem-it-solves)
3. [How It Works (Concept)](#how-it-works-concept)
4. [Tech Stack](#tech-stack)
5. [Architecture](#architecture)
6. [All Features](#all-features)
   - [Implemented (MVP + Phase 2)](#implemented-mvp--phase-2)
   - [Planned (Phase 3–4)](#planned-phase-3--4)
7. [User POV Flows](#user-pov-flows)
   - [Onboarding Flow](#1-onboarding-flow-day-1--10-minutes)
   - [Daily Active Search Loop](#2-daily-active-search-loop)
   - [Application Tracking Flow](#3-application-tracking-flow)
   - [Interview Prep Flow](#4-interview-prep-flow)
   - [Application Assistant Flow](#5-application-assistant-flow-copy-paste-aid)
   - [Google Sign-In Flow](#6-google-sign-in-flow)
   - [Auth & Session Flow](#7-auth--session-flow)
8. [Data Models](#data-models)
9. [API Endpoints](#api-endpoints)
10. [Frontend Routes](#frontend-routes)
11. [Job Data Sources](#job-data-sources)
12. [Development Phases](#development-phases)

---

## What Is This Project?

The **AI Job Application Operations System** is a full-stack web application that automates and optimizes the entire job search pipeline. It ingests jobs from multiple boards (LinkedIn, Indeed, Glassdoor, Glassdoor, and more), scores them against your resume using AI, generates tailored cover letters, tracks every application through a CRM pipeline, and provides interview preparation — all in one place.

**Built with:** FastAPI (Python) + Next.js 16 (React/TypeScript) + PostgreSQL + pgvector + Redis + Celery + OpenAI.

---

## The Problem It Solves

Every active job seeker juggles **12+ friction points simultaneously**:

| Pain Point | Why It Hurts |
|---|---|
| Tailoring resumes per role | Manual, time-consuming, easy to forget per JD |
| Writing cover letters | Blank-page problem × dozens of applications |
| Tracking applications | Spreadsheets break down fast |
| Researching companies | Context switching across 5+ tabs |
| Following up | Timing is awkward, easy to forget |
| Interview prep | No structured intel per company |
| Managing recruiter conversations | Threads across email, LinkedIn, phone |
| Matching skills to JDs | No objective signal — gut feeling only |
| Multi-platform fatigue | LinkedIn, Indeed, Greenhouse, Lever, Workday |
| Deadline management | Offer expirations, referral windows |
| Salary intel | No anchor point per role/company/location |
| Timezone/shift filtering | Remote roles span all time zones |
| Identifying scam jobs | Increasingly hard to spot without research |

---

## How It Works (Concept)

> **Upload resume → connect job sources → let AI run your job search like a project.**

The system acts as a **personal job search operations center** — it ingests data, applies AI to every friction point, tracks everything in a CRM, and gradually automates the repetitive work.

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
| OpenAI SDK | 1.x | GPT-4o (LLM) + text-embedding-3-small (embeddings) |
| python-jobspy | 1.1.x | Open-source job scraper |
| JSearch API | RapidAPI | Commercial job search API |
| tenacity | 9.x | Retry with exponential backoff |
| structlog | — | Structured logging |

### Frontend

| Technology | Version | Purpose |
|---|---|---|
| Next.js | 16.2 | App Router, SSR, Server Actions |
| React | 19.2 | UI library (React Compiler enabled) |
| TypeScript | 5.x | Type safety |
| TailwindCSS | 4.x | Utility-first styling |
| shadcn/ui | latest | Component library (Radix primitives) |
| TanStack Query | v5 | Server state, caching, optimistic updates |
| Zustand | 5.x | Client-side UI state |
| Zod | 3.x | Schema validation |
| React Hook Form | 7.x | Form management |

### Infrastructure

| Technology | Purpose |
|---|---|
| Docker Compose | Local development orchestration |
| Nginx | Reverse proxy |
| Railway / Render | Production deployment targets |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│           Next.js 16.2 (App Router + React 19.2)               │
│   Dashboard │ Job Feed │ CRM │ Resume Editor │ Cover Letters   │
└────────────────────────┬───────────────────────────────────────┘
                         │ HTTPS / httpOnly cookies
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   FastAPI 0.136.x (Python 3.13)                 │
│  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌───────────┐  │
│  │ /auth    │  │ /jobs        │  │ /apply   │  │ /resume   │  │
│  │ JWT      │  │ search/ingest│  │ CRM CRUD │  │ AI tailor │  │
│  └──────────┘  └──────────────┘  └──────────┘  └───────────┘  │
│                          │                                      │
│                   Service Layer                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ JobService │ ApplicationService │ ResumeAIService         │   │
│  │ MatchingService │ CoverLetterService │ InterviewService  │   │
│  │ AuthService │ PreferenceService                          │   │
│  └─────────────────────────────────────────────────────────┘   │
└────┬──────────────┬───────────────────────────────────┬─────────┘
     │              │                                   │
┌────▼────┐   ┌─────▼──────┐                    ┌──────▼──────┐
│PostgreSQL│   │   Redis     │                    │  AI Layer   │
│+ pgvector│   │ (cache +    │                    │  OpenAI     │
│          │   │  task queue)│                    │  Embeddings │
└──────────┘   └─────┬──────┘                    │  pgvector   │
                     │                           └─────────────┘
              ┌──────▼──────┐
              │Celery Workers│
              │ Job Ingestion│
              │ Match Ranking│
              │ Maintenance  │
              └─────────────┘
```

---

## All Features

### Implemented (MVP + Phase 2)

#### 1. Authentication & User Management
- Email + password registration and login
- Google OAuth sign-in (GSI — "Continue with Google")
- JWT access tokens (in-memory) + httpOnly refresh cookies
- Auto-refresh on 401 responses (silent refresh)
- Password reset flow (forgot password → email → reset)
- Auth guard for protected dashboard routes
- Multi-user support with per-user data isolation

#### 2. Resume Parsing & Candidate Profile
- Upload PDF/DOCX resume files
- AI-powered text extraction and structured parsing
- Builds a **Candidate Profile** with:
  - Skills (hard + soft) with years of experience
  - Education history (degree, institution, year)
  - Certifications
  - Work history with context and highlights
  - Preferred role types, seniority level
- Profile stored as JSONB with vector embeddings (1536-dim)
- User can view and edit parsed profile
- Background task processing with progress polling (`GET /resume/tasks/{id}`)

#### 3. Job Feed Ingestion
- Multi-source aggregation:
  - **python-jobspy**: LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google
  - **onlinejobs.ph**: Custom scraper for Philippine market
  - **JSearch API**: Commercial aggregator (RapidAPI)
- SHA256 deduplication by company + title + location fingerprint
- Job descriptions embedded as vectors (pgvector) for semantic matching
- Shift type detection (day/night/flexible) via regex + LLM fallback
- Auto-refresh via Celery scheduled tasks (3× daily)
- On-demand refresh with cooldown gate (120 min, returns HTTP 429 if too soon)
- Job archiving for stale listings
- Paginated, filterable job listing

#### 4. AI JD-to-Resume Matching (3-Layer Pipeline)
- **Layer 1 — Vector Pre-Rank**: pgvector cosine similarity between JD embedding and profile embedding
- **Layer 2 — Deterministic Skill Recall**: Alias-aware skill matching (e.g., "K8s" → "Kubernetes") with precision/recall
- **Layer 3 — LLM Explanation**: GPT-4o explains match quality, matched skills, and missing keywords (lazy-loaded, cached)
- Match confidence bands: Strong / Moderate / Stretch / Weak
- Skills gap summary across all matches
- Per-job match explanation with detailed reasoning

#### 5. AI Cover Letter Generator
- Generates tailored 3-paragraph cover letter from profile + job description
- Configurable tone (professional, enthusiastic, concise)
- Model selection (GPT-4o, etc.)
- User can edit and save cover letters
- Auto-links cover letter to application record
- One cover letter per job per user (upsert pattern)

#### 6. Application CRM (Minimal)
- Track applications through status pipeline:
  - `saved` → `applied` → `interview` → `offer` → `rejected` / `closed`
- Each application links to a job and optional cover letter
- Notes field for free-text tracking
- Table view with inline status dropdown updates
- Jobs can be "saved" to applications from matches/jobs view
- Unique constraint: one application per user per job

#### 7. Job Search Preferences
- Persistent search criteria per user:
  - Job titles (e.g., "Senior Backend Engineer", "Staff Engineer")
  - Keywords to include/exclude
  - Locations (city, state, country)
  - Remote preference (remote/hybrid/onsite)
  - Salary minimum
  - Job types (full-time, part-time, contract, internship)
  - Enabled sources (LinkedIn, Indeed, Glassdoor, etc.)
  - Results per source and hours_old filter
- Auto-population from parsed resume profile
- Preferences drive scheduled scraping

#### 8. Interview Preparation
- One-click generation of targeted interview questions per job
- LLM generates:
  - Role-specific technical questions
  - Behavioral/STAR questions
  - Questions to ask the interviewer
  - Focus areas based on JD gaps
- Cached per job per user
- `force_refresh` option to regenerate
- Stored as JSONB in `interview_preps` table

#### 9. Application Assistant (Copy-Paste Aid)
- Slide-over panel showing structured profile data for quick copy-paste
- Sections: Personal Details, Work Experience, Education, Skills, Certifications
- One-click copy per field (📋 button with toast feedback)
- "Copy all" per section
- "Open job posting" link to source URL in new tab
- Stays open while navigating between matches

### Planned (Phase 3–4)

#### Phase 3 — Intelligence & Enrichment
- AI Resume Tailoring Engine (rewrites bullet points per JD)
- Multi-resume management (different versions for different roles)
- Company research briefings (culture, Glassdoor, recent news, tech stack)
- AI Follow-Up Assistant (drafts follow-up emails)
- Scam/Fake Job Detector (risk labels: Safe / Review / Likely Scam)
- Profile enrichment (LinkedIn URL, portfolio, address, work authorization, pronouns)
- Job-tailored work history suggestions in Application Assistant

#### Phase 4 — Automation & Multi-Agent
- Browser extension for auto-fill (Workday, Greenhouse, Lever, etc.)
- Auto-apply with user review step
- Gmail/Calendar integration
- Slack/Discord notifications
- ATS export
- LangGraph orchestration for multi-step workflows
- Temporal for durable workflows
- Autonomous agents: research, negotiation, thank-you emails
- Multi-tenant SaaS

---

## User POV Flows

### 1. Onboarding Flow (Day 1, ~10 minutes)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ONBOARDING                                  │
│                                                                     │
│  Step 1: Sign Up                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  User visits /signup                                          │  │
│  │  → Enters name, email, password (min 8 chars)                 │  │
│  │  → OR clicks "Sign in with Google"                            │  │
│  │  → Redirected to /dashboard/jobs                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Step 2: Upload Resume                                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  User navigates to Settings → Profile                        │  │
│  │  → Uploads PDF or DOCX resume                                │  │
│  │  → Backend extracts text (pdfplumber/python-docx)            │  │
│  │  → Celery worker: parse → embed → persist                    │  │
│  │  → Frontend polls GET /resume/tasks/{id} for progress        │  │
│  │  → Parsed profile appears: skills, work history, education   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Step 3: Set Job Preferences                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  User navigates to Settings → Preferences                    │  │
│  │  → Auto-populated from resume (job titles, skills)           │  │
│  │  → User sets: target roles, locations, remote pref, salary   │  │
│  │  → Selects job sources: LinkedIn, Indeed, Glassdoor, etc.    │  │
│  │  → Clicks "Save & Fetch Jobs" or "Refresh Now"               │  │
│  │  → System starts ingesting matching jobs                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Step 4: Jobs Appear                                               │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Dashboard → Jobs tab shows ingested jobs                    │  │
│  │  → Each job shows: title, company, location, source, salary  │  │
│  │  → Deduplicated across sources (same job appears once)       │  │
│  │  → Shift type detected (Day/Night/Flexible)                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2. Daily Active Search Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│                      DAILY SEARCH LOOP                              │
│                                                                     │
│  Morning: Dashboard                                                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Stats cards: Total Jobs | Matches | Active Applications     │  │
│  │  Pipeline funnel: Saved → Applied → Interview → Offer        │  │
│  │  New matches surfaced since last visit                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Browse Matches                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Navigate to Dashboard → Matches                             │  │
│  │  → Jobs ranked by AI match score (0–100)                     │  │
│  │  → Confidence band: Strong / Moderate / Stretch / Weak       │  │
│  │  → Click "Explain" → LLM reasoning:                          │  │
│  │    "85% match — Python/FastAPI aligned, but JD requires      │  │
│  │     Kubernetes which is not in your resume"                  │  │
│  │  → Click "Skills Gap Summary" → see missing keywords         │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Actions on a Match                                                │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  For each high-score match, user can:                        │  │
│  │                                                              │  │
│  │  [Save to Applications]  → Moves job to CRM pipeline         │  │
│  │  [Generate Cover Letter] → AI writes tailored letter          │  │
│  │  [Interview Prep]        → Generate targeted questions       │  │
│  │  [Apply Assistant]       → Copy-paste panel for form filling │  │
│  │  [Explain Match]         → Detailed LLM reasoning            │  │
│  │  [Archive]               → Hide irrelevant jobs              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Generate Cover Letter                                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  User clicks "Generate Cover Letter" on a match/job          │  │
│  │  → AI generates 3-paragraph letter:                          │  │
│  │    - Opening: Why this company/role                          │  │
│  │    - Body: Relevant experience mapped to JD requirements     │  │
│  │    - Closing: Call to action                                 │  │
│  │  → User reviews and can edit                                 │  │
│  │  → Saved and auto-linked to application                      │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Apply (External)                                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  User clicks "Open job posting" → external ATS page          │  │
│  │  → Opens Application Assistant slide-over panel              │  │
│  │  → Copy-paste personal info, work history, skills, education │  │
│  │  → Submit external application                               │  │
│  │  → Back in CRM: update status to "applied"                   │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 3. Application Tracking Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                   APPLICATION TRACKING                              │
│                                                                     │
│  Dashboard → Applications tab                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Table view of all applications:                             │  │
│  │  ┌──────────┬──────────┬──────────┬──────────┬──────────┐   │  │
│  │  │ Company  │ Role     │ Status   │ Date     │ Actions  │   │  │
│  │  ├──────────┼──────────┼──────────┼──────────┼──────────┤   │  │
│  │  │ Stripe   │ Sr. BE   │ Applied  │ Jun 25   │ [Update] │   │  │
│  │  │ Notion   │ Staff FE │ Interview│ Jun 24   │ [Update] │   │  │
│  │  │ Vercel   │ SWE      │ Offer    │ Jun 20   │ [Update] │   │  │
│  │  └──────────┴──────────┴──────────┴──────────┴──────────┘   │  │
│  │                                                              │  │
│  │  Status Pipeline:                                            │  │
│  │  saved → applied → interview → offer → rejected / closed     │  │
│  │                                                              │  │
│  │  Click status badge → inline dropdown to change status       │  │
│  │  Add notes per application                                   │  │
│  │  View attached cover letter                                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Dashboard Stats                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────┐ │  │
│  │  │ Total Jobs │ │ Matches    │ │ Active     │ │ Offer     │ │  │
│  │  │    247     │ │    42      │ │ Apps: 15   │ │ Rate: 12% │ │  │
│  │  └────────────┘ └────────────┘ └────────────┘ └───────────┘ │  │
│  │                                                              │  │
│  │  Pipeline Funnel:                                            │  │
│  │  Saved (42) → Applied (15) → Interview (3) → Offer (1)      │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 4. Interview Prep Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     INTERVIEW PREP                                  │
│                                                                     │
│  From a match card or application:                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  User clicks "Interview Prep" button                          │  │
│  │  → POST /interview-prep/{job_id}                              │  │
│  │  → LLM generates targeted questions based on:                 │  │
│  │    - Job description requirements                             │  │
│  │    - Candidate's skills and experience                        │  │
│  │    - Missing keywords / skill gaps                            │  │
│  │  → Result cached (no repeat LLM calls)                        │  │
│  │  → "Force Refresh" option to regenerate                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Generated Prep Content                                            │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  📋 Focus Areas: Kubernetes, System Design, API Architecture  │  │
│  │                                                              │  │
│  │  🔧 Technical Questions:                                     │  │
│  │  1. Explain how you would design a rate limiter for a        │  │
│  │     distributed system.                                      │  │
│  │  2. How do you handle database migrations with zero          │  │
│  │     downtime?                                                │  │
│  │  3. ...                                                     │  │
│  │                                                              │  │
│  │  👔 Behavioral Questions:                                    │  │
│  │  1. Tell me about a time you had to push back on a          │  │
│  │     product requirement.                                     │  │
│  │  2. ...                                                     │  │
│  │                                                              │  │
│  │  ❓ Questions to Ask Them:                                   │  │
│  │  1. What does the onboarding process look like for this     │  │
│  │     role?                                                    │  │
│  │  2. ...                                                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 5. Application Assistant Flow (Copy-Paste Aid)

```
┌─────────────────────────────────────────────────────────────────────┐
│                   APPLICATION ASSISTANT                             │
│                                                                     │
│  From a match card, click "Apply Assistant":                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Slide-over panel opens:                                     │  │
│  │                                                              │  │
│  │  📋 Personal Details                                         │  │
│  │  ┌──────────────────────────────────────┐                    │  │
│  │  │ Jane Doe                       [📋]  │ ← one-click copy   │  │
│  │  │ jane@email.com                 [📋]  │                    │  │
│  │  │ +1 (555) 123-4567              [📋]  │                    │  │
│  │  │ San Francisco, CA              [📋]  │                    │  │
│  │  └──────────────────────────────────────┘                    │  │
│  │                                                              │  │
│  │  💼 Work Experience                                          │  │
│  │  ┌──────────────────────────────────────┐                    │  │
│  │  │ Senior Dev @ TechCorp                │                    │  │
│  │  │ Jan 2022 – Present                   │                    │  │
│  │  │ • Led migration to React 18... [📋]  │                    │  │
│  │  │ • Reduced bundle size by 40%   [📋]  │                    │  │
│  │  │                    [Copy all]        │                    │  │
│  │  └──────────────────────────────────────┘                    │  │
│  │                                                              │  │
│  │  🎓 Education                                                │  │
│  │  🛠️ Skills                                                   │  │
│  │  📜 Certifications                                           │  │
│  │                                                              │  │
│  │  [Open job posting in new tab]                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  User works side-by-side: panel open ← → external ATS form          │
│  Each 📋 copies a single field to clipboard with toast "Copied!"    │
└─────────────────────────────────────────────────────────────────────┘
```

### 6. Google Sign-In Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GOOGLE SIGN-IN                                   │
│                                                                     │
│  User visits /signin or /signup                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  GoogleSignInButton component mounts                         │  │
│  │  → Loads GSI script (accounts.google.com/gsi/client)         │  │
│  │  → google.accounts.id.initialize({ client_id, callback })    │  │
│  │  → google.accounts.id.renderButton(container, { ... })       │  │
│  │  → Renders "Sign in with Google" branded button              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  User clicks "Sign in with Google"                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  → Google popup opens                                        │  │
│  │  → User selects Google account / enters credentials          │  │
│  │  → Google returns credential (ID token) to callback          │  │
│  │  → GoogleSignInButton calls onCredential(idToken)            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  Frontend sends ID token to backend                                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  useAuth().loginWithGoogle(idToken)                          │  │
│  │  → POST /api/backend/auth/google { id_token }                │  │
│  │  → Backend validates token with Google                       │  │
│  │  → Creates user (or links existing by email/google_id)       │  │
│  │  → Returns TokenResponse: access_token + user                │  │
│  │  → Sets httpOnly refresh cookie                              │  │
│  │  → Frontend stores access token in memory                    │  │
│  │  → Auth status → "authenticated"                             │  │
│  │  → router.replace("/dashboard/jobs")                         │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 7. Auth & Session Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AUTH & SESSION                                   │
│                                                                     │
│  Registration                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  POST /auth/register { name, email, password }               │  │
│  │  → Password hashed with Argon2                                │  │
│  │  → User created                                              │  │
│  │  → JWT access token + httpOnly refresh cookie returned       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Login (Email + Password)                                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  POST /auth/login { email, password }                        │  │
│  │  → Verify password against Argon2 hash                       │  │
│  │  → Issue JWT access token (15 min expiry)                    │  │
│  │  → Set httpOnly refresh cookie (7 day expiry)                │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Silent Refresh (on page load / 401)                                │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Frontend AuthContext mounts                                  │  │
│  │  → POST /auth/refresh (cookie sent automatically)            │  │
│  │  → Backend validates refresh token                           │  │
│  │  → Issues new access token                                   │  │
│  │  → Status → "authenticated" (or "unauthenticated" on fail)   │  │
│  │                                                              │  │
│  │  Also: on any 401 API response, auto-refresh + retry         │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Logout                                                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  POST /auth/logout                                           │  │
│  │  → Clears refresh cookie                                     │  │
│  │  → Frontend clears access token from memory                  │  │
│  │  → Status → "unauthenticated"                                │  │
│  │  → Redirected to /signin                                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Password Reset                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  /forgot-password → POST /auth/forgot-password { email }     │  │
│  │  → Backend generates reset token                             │  │
│  │  → User receives email with reset link                       │  │
│  │  → /reset-password?token=xxx → POST /auth/reset-password     │  │
│  │  → Password updated                                          │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Data Models

### Users & Auth

| Model | Table | Key Fields |
|---|---|---|
| **User** | `users` | `id`, `email`, `name`, `hashed_password`, `google_id`, `avatar_url`, `is_active` |

### Resume & Profile

| Model | Table | Key Fields |
|---|---|---|
| **Resume** | `resumes` | `id`, `user_id`, `filename`, `raw_text`, `is_active` |
| **CandidateProfile** | `candidate_profiles` | `id`, `user_id`, `resume_id`, `parsed` (JSONB), `summary_text`, `embedding` (Vector 1536) |

### Jobs

| Model | Table | Key Fields |
|---|---|---|
| **Job** | `jobs` | `id`, `fingerprint` (unique SHA256), `title`, `company`, `location`, `is_remote`, `job_type`, `description`, `description_embedding` (Vector 1536), `salary_min/max/currency`, `source`, `source_url`, `posted_at`, `fetched_at`, `is_archived`, `shift_type`, `raw_response` (JSONB) |
| **UserJob** | `user_jobs` | Composite PK `(user_id, job_id)`, `preference_id` |

### Matching

| Model | Table | Key Fields |
|---|---|---|
| **UserJobMatch** | `user_job_matches` | `id`, `user_id`, `job_id` (unique pair), `vector_score`, `llm_score`, `llm_confidence`, `llm_explanation`, `matched_skills` (JSONB), `missing_keywords` (JSONB), `deterministic_match` (JSONB), `confidence` |

### Preferences

| Model | Table | Key Fields |
|---|---|---|
| **UserJobPreference** | `user_job_preferences` | `id`, `user_id` (unique), `job_titles[]`, `keywords[]`, `excluded_keywords[]`, `locations[]`, `remote_preference`, `salary_min`, `job_types[]`, `enabled_sources[]`, `results_per_source`, `hours_old`, `is_active`, `last_scraped_at` |

### Applications & Cover Letters

| Model | Table | Key Fields |
|---|---|---|
| **CoverLetter** | `cover_letters` | `id`, `user_id`, `job_id` (unique), `content`, `tone`, `model`, `is_edited` |
| **Application** | `applications` | `id`, `user_id`, `job_id` (unique pair), `status` (saved/applied/interview/offer/rejected/closed), `cover_letter_id`, `notes` |

### Interview Prep

| Model | Table | Key Fields |
|---|---|---|
| **InterviewPrep** | `interview_preps` | `id`, `user_id`, `job_id` (unique pair), `questions` (JSONB), `focus_areas[]`, `model` |

---

## API Endpoints

All endpoints are under `/api/v1`.

### Auth (`/auth`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/register` | Register new user (name, email, password) |
| `POST` | `/login` | Login with email + password |
| `POST` | `/google` | Login/register with Google ID token |
| `POST` | `/refresh` | Refresh access token (httpOnly cookie) |
| `POST` | `/logout` | Logout (clear refresh cookie) |
| `GET` | `/me` | Get current authenticated user |
| `POST` | `/forgot-password` | Request password reset email |
| `POST` | `/reset-password` | Reset password with token |

### Resume (`/resume`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/resume` | Upload resume file (PDF/DOCX) → background processing |
| `GET` | `/resume/tasks/{id}` | Poll background task progress |
| `GET` | `/resume/profile` | Get parsed candidate profile |
| `PUT` | `/resume/profile` | Update/edit candidate profile |
| `GET` | `/resume/tailor/{job_id}` | AI-tailor work history for a specific job |

### Jobs (`/jobs`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs/ingest` | Trigger job ingestion (returns 202 + task ID) |
| `GET` | `/jobs/tasks/{id}` | Poll ingestion task progress |
| `GET` | `/jobs` | List jobs (paginated, filterable) |
| `POST` | `/jobs/import` | Manually import a job from URL |
| `GET` | `/jobs/{id}` | Get single job details |
| `PATCH` | `/jobs/{id}/archive` | Archive a job (soft delete) |

### Matches (`/matches`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/matches/rank` | Trigger match ranking for current user |
| `GET` | `/matches` | List ranked matches |
| `GET` | `/matches/skills-gap-summary` | Aggregated missing skills across all matches |
| `GET` | `/matches/{job_id}/explain` | Get LLM explanation for a match (lazy, cached) |

### Preferences (`/preferences`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/preferences` | Get current user's job search preferences |
| `PUT` | `/preferences` | Create or fully replace preferences |
| `PATCH` | `/preferences` | Partially update preferences |
| `POST` | `/preferences/refresh` | On-demand job scrape (202; 429 if cooldown active) |

### Interview Prep (`/interview-prep`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/interview-prep/{job_id}` | Generate interview questions (cached; `?force_refresh=true`) |

### Cover Letters (`/cover-letters`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/cover-letters/by-job/{job_id}` | Get cover letter for a job |
| `POST` | `/cover-letters` | Generate new cover letter |
| `GET` | `/cover-letters/{id}` | Get cover letter by ID |
| `PATCH` | `/cover-letters/{id}` | Edit cover letter content |

### Applications (`/applications`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/applications` | Create application (save job to pipeline) |
| `GET` | `/applications` | List applications with joined job data |
| `PATCH` | `/applications/{id}` | Update status, notes, or cover letter |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Basic health check |
| `GET` | `/health/detailed` | DB connectivity check |

---

## Frontend Routes

| Route | Page | Auth Required | Description |
|---|---|---|---|
| `/` | Landing/redirect | No | Redirects to `/dashboard` if authenticated, else `/signin` |
| `/signin` | Sign In | No | Email + password login, Google sign-in, forgot password link |
| `/signup` | Sign Up | No | Name + email + password registration, Google sign-in |
| `/forgot-password` | Forgot Password | No | Email form to request password reset |
| `/reset-password` | Reset Password | No | New password form (token from email link) |
| `/dashboard` | Dashboard Home | Yes | Stats cards (jobs/matches/apps/offer rate) + pipeline funnel |
| `/dashboard/jobs` | Job Feed | Yes | Ingest jobs, browse feed with filters, save/archive actions |
| `/dashboard/matches` | Matches | Yes | Ranked matches, explain, cover letter, interview prep, apply assistant |
| `/dashboard/applications` | Applications CRM | Yes | Table view with status pipeline, notes, cover letter |
| `/dashboard/settings/profile` | Profile Settings | Yes | Upload resume, view/edit parsed profile |
| `/dashboard/settings/preferences` | Preferences | Yes | Edit search criteria, refresh jobs, cooldown indicator |
| `/dashboard/resume` | Redirect | Yes | → `/dashboard/settings/profile` |
| `/dashboard/preferences` | Redirect | Yes | → `/dashboard/settings/preferences` |

---

## Job Data Sources

### Tier 1 — Official APIs

| Source | Coverage | Cost | Format |
|---|---|---|---|
| **JSearch (RapidAPI)** | LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster | Free: 200 req/mo; Pro: $25/mo | REST API |
| **Adzuna API** | 50+ countries, major boards | Free: 250 req/day | REST API |
| **USAJobs API** | US Federal Government | Free, no key | REST API |

### Tier 2 — Open-Source Scraping

| Source | Coverage | Notes |
|---|---|---|
| **python-jobspy** | LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google | 3.5k+ GitHub stars; needs rotating proxies for LinkedIn |

### Tier 3 — RSS Feeds

| Source | Focus | Format |
|---|---|---|
| Remotive | Remote tech jobs | REST API |
| We Work Remotely | Remote jobs (by category) | RSS |
| Jobicy | Remote jobs | RSS |
| HN Who's Hiring | Startup jobs (monthly) | Algolia API |

### Tier 4 — Managed Scraping (Production Scale)

| Provider | Type | Pricing |
|---|---|---|
| Apify | Marketplace actors | Pay per compute unit |
| Bright Data | Residential proxies + SERP API | ~$0.001/request |
| Oxylabs | Job-specific scraping | Enterprise |

### Custom Scrapers

| Source | Type | Notes |
|---|---|---|
| onlinejobs.ph | Custom scraper | Philippine virtual assistant/staff marketplace |

---

## Development Phases

| Phase | Name | Status | Key Deliverables |
|---|---|---|---|
| **Phase 1** | MVP | ✅ Complete | Resume parsing, job ingestion (JobSpy + custom scrapers), AI matching (3-layer), cover letters, minimal CRM, auth (JWT + Google OAuth) |
| **Phase 2** | Intelligence | ✅ Complete | Preferences-driven scheduled scraping, interview prep, skills gap UI, on-demand refresh with cooldown |
| **Phase 3** | Intelligence+ | 🔜 Planned | AI resume tailoring engine, multi-resume management, company research, follow-up assistant, scam detector, profile enrichment, application assistant (Phase 2–3) |
| **Phase 4** | Automation | 🔮 Future | Browser extension, auto-apply, Gmail/Calendar, Slack/Discord, ATS export, LangGraph, Temporal, multi-agent, multi-tenant SaaS |

---

*Generated 2026-06-29 — based on codebase analysis and documentation in `docs/`*
