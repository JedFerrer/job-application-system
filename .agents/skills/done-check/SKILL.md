---
name: done-check
description: Final readiness gate before committing, merging, or ending a completed phase.
---

# Done Check

Run the final completion and scope gate described in `AGENTS.md` and
`docs/development-workflow.md`. This is not a second architectural review.

Verify that only approved files changed; acceptance criteria are met; required
checks and QA are accurately reported; no Blocker or unresolved Should Fix
remains; parked paths are untouched; no generated, secret, or unrelated
artifacts were introduced; no staging or commit occurred; and no next phase
started automatically.

Report evidence, remaining risks or blockers, and whether the phase is ready
for a fresh independent review. Do not edit files or authorize implementation.
