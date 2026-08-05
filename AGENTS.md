# AGENTS.md

`AGENTS.md` is the canonical repository-wide contract for Codex work. The
detailed, reusable workflow is in [docs/development-workflow.md](docs/development-workflow.md).
Phase-specific plans and status documents are dynamic task context; they do
not replace this contract. Local or tool-specific files under `.agents/`,
`.cursor/`, or `.github/` are non-authoritative unless explicitly aligned with
this contract.

## Project context

This repository is an AI job application CRM with a FastAPI backend, Next.js
dashboard, and Chrome extension. Preserve the documented architecture,
authentication, ownership, privacy, extension-session, imported-job, and safe
autofill invariants when they are relevant to a change.

## Workflow contract

Use this lifecycle for feature work, risky refactors, and non-obvious bugs:

Research → Plan → one approved phase → implement and validate → Browser QA
when required → focused corrections → fresh independent review → lockable
verdict → explicit commit authorization.

Research, planning, Browser QA, review, and done-check are read-only. Planning
must stop after a plan and wait for one explicit implementation approval. A
valid phase plan includes the objective, approved scope, explicit exclusions,
acceptance criteria, expected files, implementation approach, focused and
broader validation, Browser QA requirements, risks, assumptions, stop
conditions, and a suggested commit message.

Approval of a named phase authorizes the implementation agent, without asking
again, to edit in-scope files, run focused and required broader checks, diagnose
and correct related in-scope failures, add regression tests when appropriate,
self-review, run the automated done-check, and prepare the Browser QA
handoff. The authorization applies only to that phase. The agent must not
implement a later phase, materially expand scope, modify parked paths, stage,
commit, or claim unavailable QA passed.

Do not stop merely because an in-scope check fails, a correction is needed, a
check must be rerun, or self-review finds a correctable issue. Stop only for a
material requirements conflict or scope expansion; destructive or irreversible
work; possible production impact; uncertain authentication, authorization,
security, privacy, permissions, or billing behavior; a new production
dependency; unavailable credentials or infrastructure; an unsafe product
decision; or an unrelated failure that cannot be separated from the approved
work. A stop report must state the blocker, evidence, safe actions attempted,
the exact decision required, and the safest options.

## Validation selection

Select checks by affected area; do not blindly run every command. Use only
commands supported by the repository's real configuration:

- Backend: `cd backend && uv run ruff check .`, `cd backend && uv run mypy app tests`,
  focused `cd backend && uv run pytest tests/unit/ -v`, and broader
  `cd backend && uv run pytest` when the change warrants it. Database-backed
  coverage requires the isolated test database; use
  `cd backend && docker compose run --rm test` when container-backed migration
  and integration confidence is required. Never point tests at `job_app`.
- Frontend: `cd frontend && npm test`, `cd frontend && npx tsc --noEmit`, and
  `cd frontend && npm run build` as affected. There is no frontend lint script.
- Extension: `cd extension && npm test` and `cd extension && npm run build` as
  affected. The declared lint command is currently unreliable because its
  ESLint setup is not present; do not claim it passed. Browser E2E automation
  is not runnable because Playwright is not wired.

Do not run formatters, install dependencies, migrations, services, or generated
artifact-producing commands unless the approved phase explicitly authorizes
them. Browser QA is a separate manual/browser-capable stage, not an inferred
automated result.

## Git and scope safety

At the beginning and end of implementation, run `git status --short`, inspect
the relevant diff, identify pre-existing changes, and verify parked paths are
untouched. Preserve unrelated work. Never reset, checkout, clean, stash,
restore, amend, force-push, stage, or commit without explicit authorization.
Only approved phase files may later be staged.

## Completion

The final handoff lists changed files, acceptance-criteria status, checks and
results, Browser QA status, risks or blockers, and whether the phase is ready
for independent review. A phase is not lockable with a Blocker or unresolved
Should Fix. The next phase never starts automatically.
