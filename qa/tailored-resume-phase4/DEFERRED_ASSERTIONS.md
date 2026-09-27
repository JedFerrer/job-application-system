# Phase 4 deferred items (after Phase 3 integration)

Historical engineering record. The local job IDs, browser session, and
worktree state below are not reproducible portfolio fixtures or current QA
instructions. PUBLIC.4 evaluation must use committed synthetic data.

Integrated against `f8c4e01 fix(export): render tailored work history from canonical blocks`.

## Resolved by Phase 3 / Phase 4

- Canonical work-history render DTO: `buildReviewedWorkHistory` / `semanticWorkHistoryLines`.
- Preview/copy/PDF/DOCX share `ResumeRenderSection.roles` with `narrative`, `responsibilityHeading`, and `bullets`.
- `requires_regeneration` is on `TailoredResume` and the dialog shows an alert plus disabled export actions.
- `createReviewState` now seeds `rejected` from saved `final_text === original_text` when a suggestion existed, so Keep Original survives editable reopen.

## Still deferred (non-blocking)

- Distinct suggested wording on **backend** save remains sanitizer-dependent; frontend review-state tests cover accept/restore when wording differs.
- Arbitrary drag-reorder is not a product feature. Remaining-bullet order is recommended-first via `output_rank`.
- Playwright is not wired. Job 6781 is live local data, not a committed fixture.
- Live Browser QA of scenarios 1–6 was interrupted after generation on `/jobs/6781` because the IDE browser MCP disconnected. Re-run `qa/tailored-resume-phase4/BROWSER_QA.md` against the QA worktree frontend at `http://localhost:3000` (Keep Original rehydrate is in this worktree, not yet committed).
