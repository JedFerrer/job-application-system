---
name: browser-qa
description: Run or plan browser/manual QA for the Next.js dashboard or Chrome extension.
---

# Browser QA

Perform a separate, read-only Browser QA session only when acceptance criteria
require real browser interaction or visual verification. Use the handoff and
report contract in `AGENTS.md` and `docs/development-workflow.md`.

The handoff must specify affected routes, service/setup requirements,
test-data prerequisites, viewports, numbered scenarios, expected results, and
regression surfaces. The report must include scenario, route, viewport,
preconditions, exact steps, expected and actual results, console and network
observations, screenshot reference when available, and severity.

Use exactly one verdict: `QA PASSED`, `QA PASSED WITH NICE-TO-HAVE FINDINGS`,
`QA FAILED`, or `QA BLOCKED`. Missing browser capability, services, test
account/data, or extension setup is `QA BLOCKED`, never passed. Return only
confirmed Blocker and Should Fix findings for correction; defer Nice To Have.
After correction, require focused automated validation and browser retesting
of corrected and directly affected scenarios. Do not edit application files.
