# Development Workflow

This is the reusable companion to the canonical [AGENTS.md](../AGENTS.md).
Plans and phase-status documents carry task-specific context; this guide keeps
the durable handoffs and evidence rules in one place.

## Lifecycle

Research → Plan → one explicit approval → implementation and validation →
Browser QA when required → focused corrections → fresh independent review →
lockable verdict → separately authorized commit.

The implementation agent never starts the next phase automatically.

## Plan output

Include:

- objective, assumptions, and unknowns;
- approved scope and explicit exclusions;
- expected affected files and implementation approach;
- acceptance criteria;
- focused and broader validation commands;
- Browser QA requirements and scenarios;
- risks, rollback notes, and hard stop conditions;
- suggested commit message.

Planning is read-only and ends awaiting one explicit implementation approval.

## Implementation handoff

After approval, state the approved phase and allowed files. The agent may edit
those files, run relevant checks, diagnose and correct related in-scope
failures, add regression tests when appropriate, self-review, run done-check,
and prepare Browser QA without repeated approval. It must not enter a later
phase, expand scope materially, modify parked paths, stage, commit, or claim
unavailable QA passed.

Begin and end with `git status --short` and a relevant diff review. Preserve all
pre-existing and parked changes. Stop only under the hard-stop conditions in
`AGENTS.md`, and explain evidence and attempted safe actions.

## Validation selection

Select checks by affected area. Verified commands are:

| Area | Commands | Notes |
| --- | --- | --- |
| Backend | `cd backend && uv run ruff check .`; `cd backend && uv run mypy app tests` | No service required; caches may be written. |
| Backend tests | `cd backend && uv run pytest tests/unit/ -v`; `cd backend && uv run pytest` | Use the isolated test DB; the test guard refuses the application DB. |
| Backend containers | `cd backend && docker compose run --rm test` | Use for isolated Docker-backed migration/integration checks. |
| Frontend | `cd frontend && npm test`; `cd frontend && npm run lint`; `cd frontend && npx tsc --noEmit`; `cd frontend && npm run build` | Lint may report warnings while exiting successfully; build writes `.next`. |
| Extension | `cd extension && npm test`; `cd extension && npm run build` | Build writes `dist`; the declared lint command is currently unreliable because ESLint setup is absent. |
| Browser automation | None | Playwright is not wired; use the separate Browser QA stage. |

Do not invent missing commands or claim checks that were not run.

## Browser QA handoff

Use Browser QA only when acceptance criteria require real browser interaction
or visual verification. The implementation handoff must include affected
routes, service/setup requirements, test-data prerequisites, viewport sizes,
numbered scenarios, expected results, and regression surfaces.

The report records each scenario with:

`scenario · route · viewport · preconditions · exact steps · expected result ·
actual result · console observations · network observations · screenshot
reference (when available) · severity`

Allowed verdicts are `QA PASSED`, `QA PASSED WITH NICE-TO-HAVE FINDINGS`,
`QA FAILED`, and `QA BLOCKED`. Missing browser capability, services, test
account/data, or extension setup means `QA BLOCKED`, never passed.

Only confirmed Blocker and Should Fix findings return to the implementation
agent. Nice To Have findings remain deferred unless separately approved. After
a correction, rerun focused automation and retest corrected and directly
affected browser scenarios.

## Independent review

A fresh reviewer is read-only and must inspect the actual changed files,
complete diff, surrounding instructions/code, Git status, validation evidence,
Browser QA evidence when required, approved scope/exclusions, and parked-path
preservation. The reviewer does not fix findings.

Findings are `Blocker`, `Should Fix`, or `Nice To Have`. Verdicts are:

- `LOCKABLE`: no Blocker or unresolved Should Fix; required evidence is complete.
- `LOCKABLE PENDING QA`: automated evidence is sufficient, but required Browser QA is still outstanding and the phase cannot be committed yet.
- `LOCKABLE WITH EXPLAINED EXTERNAL FAILURES`: required checks were attempted; failures are external, documented, separable, and accepted by the review gate.
- `BLOCKED`: a Blocker, unresolved Should Fix, scope violation, missing required evidence, or unsafe uncertainty remains.

## Done-check

The done-check verifies completion evidence and scope integrity; it is not a
second architectural review. Confirm approved files only, acceptance criteria,
required checks, accurate QA status, no Blocker or unresolved Should Fix,
untouched parked paths, no generated/unrelated artifacts, no staging or commit,
and no automatically started next phase.

## Handoff templates

Implementation: approved phase, allowed files, exclusions, acceptance criteria,
checks, Browser QA checklist, known risks, changed files, results, and stop
conditions.

Browser QA: use the report fields above and one of the four QA verdicts.

Review: original request, approved plan, diff/status evidence, findings by
severity, open assumptions, and one of the four final verdicts.
