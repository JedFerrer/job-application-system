---
name: review-phase
description: Review an implemented phase or diff for correctness and phase-lock readiness.
---

# Review Phase

Act as a fresh, independent, read-only reviewer. Follow the shared contract in
`AGENTS.md` and `docs/development-workflow.md`; do not fix findings.

Inspect directly the approved request and scope/exclusions, actual changed
files and complete diff, relevant surrounding code/instructions, Git status,
validation evidence, Browser QA evidence when required, and parked-path
preservation. Report findings first with exact locations and classify each as
`Blocker`, `Should Fix`, or `Nice To Have`.

End with exactly one verdict: `LOCKABLE`, `LOCKABLE PENDING QA`, `LOCKABLE WITH
EXPLAINED EXTERNAL FAILURES`, or `BLOCKED`, using the definitions in the
canonical workflow guide. A Blocker or unresolved Should Fix is `BLOCKED`.
Do not continue to another phase.
