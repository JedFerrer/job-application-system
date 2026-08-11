# Resume tailoring evaluation

Phase 10 provides a small synthetic regression corpus for resume tailoring. It
complements ordinary unit and integration tests; it does not replace review or
Browser QA.

Run the credential-free deterministic suite with:

```powershell
cd backend
uv run pytest tests/unit/test_resume_tailoring_evaluation.py -v
```

The manifest at `backend/tests/fixtures/resume_tailoring_evaluation.json` has
exactly seven fictional scenarios. Add a case by supplying a unique ID,
synthetic profile, job description, locked response, and expected
classification/coverage/sanitizer outcomes. Do not use personal or production
resume data.

Live evaluation is observational and never runs in normal tests or CI. Require
both explicit opt-ins:

```powershell
$env:RESUME_TAILORING_EVAL_LIVE='1'; cd backend; uv run python -m tests.tailoring_evaluation --live
```

Each scenario is invoked at most once by the evaluator, with no evaluator-level
retry loop. Existing provider retry/fallback behavior is reused unchanged.
Missing credentials are reported as `UNAVAILABLE`, not as a pass. Live output
is not persisted and is bounded to the seven synthetic scenarios.

Deterministic invariant failures are test failures. Live rewrite quality,
selectivity, latency, fallback frequency, and provider availability are
observations only; generated prose is not exact-golden tested.
