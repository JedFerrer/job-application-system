# Frontend dashboard

The dashboard provides account access, jobs/matching, profile/resume management,
application preparation, document review, artifacts, history, and interview prep.

See the [system overview](../README.md), [backend](../backend/README.md), and
[extension](../extension/README.md). [RIGHTS.md](../RIGHTS.md) permits unmodified
local evaluation; editing instructions apply to authorized contributors.

## Stack and prerequisites

- Node.js **22**, npm, and the committed `package-lock.json`.
- Next.js 16, React 19, TypeScript, Tailwind CSS 4, TanStack Query, Radix UI;
  see [package.json](package.json) for dependency specifications.
- A running backend for authenticated application behavior.
- Python 3.13 and `backend/.venv` for the complete test suite's artifact
  interoperability checks. UI startup alone does not need host Python when the
  backend already runs in Docker.

## Setup and start

Run frontend commands from `frontend/`, unless stated otherwise.

1. Complete [backend setup](../backend/README.md#setup-and-start) and verify health.
2. Copy [.env.example](.env.example) to `.env.local` **only if it does not
   already exist**. Preserve existing configuration. On PowerShell:

   ```powershell
   Copy-Item .env.example .env.local
   ```

   On macOS/Linux:

   ```sh
   cp .env.example .env.local
   ```

   Keep `BACKEND_INTERNAL_URL=http://localhost:8000`. Leave
   `NEXT_PUBLIC_GOOGLE_CLIENT_ID` blank to disable Google sign-in, or match it
   to backend `GOOGLE_CLIENT_ID` for an intentional integration. Never put
   private provider keys in `NEXT_PUBLIC_*` values.
3. Install locked dependencies and start:

   ```sh
   npm ci
   npm run dev
   ```

4. Open `http://localhost:3000` and register/sign in with an invented identity.
   A rendered sign-in page confirms the UI serves; signed-in sessions and data
   requests also require a healthy backend.

Next.js refreshes source changes automatically. Restart after environment
changes. Ctrl+C stops the server; stop backend services separately.

### API and session flow

Browser calls use `/api/backend/*`. The
[Next.js route handler](src/app/api/backend/%5B...path%5D/route.ts) forwards them
to `BACKEND_INTERNAL_URL` under `/api/v1/*`. This is server-side configuration,
not a browser-side provider URL. When Next.js runs in a container, localhost
means that container; use an address reachable from that runtime.

The [API client](src/lib/api.ts) and [auth context](src/context/auth-context.tsx)
coordinate requests/session recovery. Refresh credentials use httpOnly cookies.
TanStack Query and task contexts load data and follow background work. Preserve
existing authentication/ownership behavior when changing requests.

## Source structure

```text
frontend/
├── src/
│   ├── app/                  # App Router pages, layouts, providers, API proxy
│   │   ├── dashboard/        # Jobs, matches, applications, settings
│   │   ├── extension/connect/ # Extension pairing screen
│   │   └── api/backend/      # Catch-all FastAPI proxy
│   ├── components/           # Feature UI and shared primitives
│   ├── context/              # Authentication and resumable task state
│   ├── lib/                  # API client, types, exports, display rules
│   └── assets/               # Source asset notices
├── public/                   # Static files, including PDF export fonts
├── tests/                    # Vitest suites and fixtures
├── next.config.ts            # Next.js configuration
└── vitest.config.ts          # jsdom environment and font-fetch setup
```

Add routes in `src/app`, feature UI in `src/components`, and shared API/types/
export logic in `src/lib`. Keep resumable tasks in their existing contexts.
A generated document does not imply an application submission.

## Tests and quality checks

For the full frontend suite, first run this from `backend/`:

```sh
uv sync --locked --dev --python 3.13
```

Then return to `frontend/`:

| Check | Command |
| --- | --- |
| Full Vitest suite | `npm test` |
| Focused example | `npm test -- tests/interview-submission-context.test.tsx` |
| Test watcher | `npm run test:watch` |
| Lint | `npm run lint` |
| Types | `npx tsc --noEmit` |
| Production build | `npm run build` |

[Artifact interoperability tests](tests/artifact-interop.test.ts) invoke
`backend/.venv/Scripts/python.exe` on Windows or `backend/.venv/bin/python`
elsewhere and validate exports through backend Python code. Node dependencies
alone are insufficient for that suite. This does not mean every frontend test
needs a running API/database.

Vitest uses jsdom; [font setup](tests/setup-font-fetch.ts) supplies committed
production fonts to export tests. Lint can report warnings with a successful
exit; report warnings accurately. Build/type checks may write `.next` or
TypeScript caches. Manual Browser QA is needed when changes require visual,
interaction, or real-cookie evidence. Playwright is not wired as a runnable
repository E2E command.

### Local production build inspection

With environment configured and backend running, from `frontend/`:

```sh
npm run build
npm run start
```

Stop any development server on port `3000` first. Open `http://localhost:3000`;
Ctrl+C stops the server. A successful build is not deployment approval; see
[verification limits](../README.md#security-rights-and-verification-limits).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Proxy reports backend unavailable | Check API health and `BACKEND_INTERNAL_URL` from the Next.js network context |
| Sign-in/session recovery fails | Check backend logs, local origin, cookies; do not disable authentication |
| Artifact test cannot spawn Python | Install backend dependencies in its expected `.venv` using Python 3.13 |
| Provider action unavailable | Check backend provider settings; a working UI does not imply AI credentials |
| Environment edit has no effect | Restart Next.js; public variables may require a production rebuild |
| Port `3000` occupied | Inspect the conflict and stop only the server you own |

The [synthetic walkthrough](../README.md#isolated-portfolio-walkthrough) uses
a separate dashboard port/profile.

## Workflow and verification status

See [AGENTS.md](../AGENTS.md), the
[development workflow](../docs/development-workflow.md), and
[CI](../.github/workflows/quality.yml). Use repository Issues with sanitized
reproduction details for support. This guide was statically checked; no fresh
installation, build, or browser QA was performed for this update.
