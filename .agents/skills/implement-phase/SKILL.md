---
name: implement-phase
description: Implement one approved phase with a minimal scoped diff.
---

# Implement Phase

Use only after one named phase is explicitly approved. Follow the shared
contract in `AGENTS.md` and `docs/development-workflow.md`.

Confirm the approved files and exclusions, record `git status --short`, and
preserve all pre-existing and parked paths. Implement only the approved scope.
Within that scope, run focused and required broader checks, diagnose and
correct related failures, add regression tests when appropriate, self-review,
run the done-check, and prepare the Browser QA handoff without requesting
intermediate approval.

Do not start a later phase, materially expand scope, modify parked paths, run
destructive Git operations, stage, commit, or claim unavailable QA passed.
Finish with the relevant diff, `git status --short`, changed files, checks and
results, Browser QA handoff/status, risks, and review readiness.
