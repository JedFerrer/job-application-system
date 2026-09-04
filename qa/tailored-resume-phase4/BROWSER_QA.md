# Tailored-resume Phase 4 Browser QA

Do **not** run this checklist until Phase 3 is integrated. This document is the
executable integration script for final Browser QA.

Playwright is not wired. Use the dashboard at `http://localhost:3000` with a
signed-in local account.

## Setup

| Item | Requirement |
|---|---|
| Backend | Local API on the configured frontend origin |
| Frontend | `cd frontend && npm run dev` → `http://localhost:3000` |
| Auth | Signed-in dashboard session |
| Viewport A | Desktop ≥ 1280×800 |
| Viewport B | Mobile 390×844 after the desktop pass for Scenarios 1–3 |
| Job 6781 | Live local job id. **Not a repo fixture.** The job must be visible at `/jobs/6781`. If it is missing, restore it from the local database used in prior QA; do not invent a substitute and still call it 6781. |
| Affected resume | The previously failing source resume, or the sanitized wrap fixture at `backend/tests/fixtures/tailored_resume_qa/malformed_wrap_two_role.txt` imported as a `.txt`/DOCX if the original file is unavailable |
| Strong-match job | A second visible job whose description emphasizes backend/platform/API work. Prior QA screenshots used `/jobs/4888` when present; if that id is gone, pick another strong-overlap job and record its id |
| Export files | Save PDF/DOCX to a scratch folder outside git |

## Semantic comparison (all export scenarios)

Ignore font, glyph style, page breaks, and extra whitespace.

Compare **role sequence, narrative sequence, heading presence, bullet inclusion, bullet text, and bullet order**.

After Copy / PDF / DOCX:

1. Paste copied text into a scratch `.txt` file.
2. Compare PDF and DOCX with:

```bash
cd backend
uv run python tests/unit/resume_qa_support.py path/to/copy.txt path/to/resume.pdf path/to/resume.docx
```

The helper uses `pdfplumber` and `python-docx` (already in backend dependencies). It does not use OCR.

Expected: `SEMANTIC MATCH` for sanitized-fixture exports. For job 6781, use the same token-order method by hand if the live resume wording differs from the sanitized fixture.

## Shared inspection (every generated/reviewed draft)

For each work-history role:

- Role header is present and belongs to that role only.
- Role narrative is one joined paragraph, not leftover wrap lines.
- At most one responsibilities heading, placed after the narrative and before bullets.
- Bullets are complete sentences in review order.
- No orphan continuation fragments such as `supporting operational workflows.` or `payments across three regions...` as standalone prose.
- No duplicated role prose or bullet text.
- Unclassified source text, if shown, is diagnostic-only and absent from Copy/PDF/DOCX.

---

## Scenario 1 — affected resume / job 6781

- Route: `/resume` then `/jobs/6781`
- Viewport: desktop, then mobile 390×844
- Preconditions: signed in; job 6781 visible; no unsaved review on this job

1. Import or re-import the affected resume (Profile/Resume upload).
2. Approve/verify the source resume so it is the active tailoring source.
3. Open `/jobs/6781`.
4. Open Tailor resume and generate a tailored resume.
5. Inspect summary. It must be complete, source-grounded, and not duplicated.
6. Inspect skills. They must come from the source/profile, in the reviewed order.
7. Inspect every work-history role using the shared inspection list.
8. Confirm no orphan continuation fragments.
9. Confirm no duplicate role prose/bullet text.
10. Confirm no truncated bullets.
11. Confirm unsupported IAM experience is not invented. If the source does not support IAM, the draft must not add it.
12. Export PDF.
13. Export DOCX.
14. Copy resume text.
15. Compare all surfaces semantically.

Expected:

- Generation succeeds and the dialog remains reviewable.
- Summary, skills, and every role pass the shared inspection.
- Copy/PDF/DOCX agree semantically and omit unclassified diagnostics.
- Browser console has no application errors; export-validation request returns 200.

Regression surfaces: `/resume` saved-draft list, `/jobs/6781` tailor dialog, Copy/PDF/DOCX.

---

## Scenario 2 — restore wording

- Route: `/jobs/6781` tailor dialog (continue from Scenario 1 or regenerate)
- Viewport: desktop, then mobile 390×844
- Preconditions: a suggested bullet whose wording differs from the source bullet

1. Click **Keep original** on one suggested bullet.
2. Verify the preview immediately shows source wording, not the suggestion.
3. Save draft.
4. Reload: open `/resume`, open the saved draft for this job (or reopen the dialog from the saved snapshot).
5. Verify restored wording remains.
6. Copy.
7. Export PDF.
8. Export DOCX.
9. Confirm the rejected suggestion does not reappear in preview, copy, PDF, or DOCX.

Expected:

- Badge/label shows original kept.
- Save succeeds.
- Reload returns the restored `final_text`.
- Copy/PDF/DOCX contain the original bullet once and never the rejected suggestion.

Regression surfaces: review controls, save/reload, all three export paths.

---

## Scenario 3 — omission/order

- Route: tailor dialog for job 6781
- Viewport: desktop, then mobile 390×844
- Preconditions: a role with at least three included bullets

1. Omit one supported bullet that is not the only remaining bullet.
2. Confirm remaining bullets follow the product's recommended-first output order.
3. Save and reload the draft.
4. Verify preview: omitted bullet is under **Not shown on this resume**; remaining bullets keep order.
5. Copy, export PDF, export DOCX, and confirm they match the preview: omitted text absent, remaining bullets in the same order.

Expected:

- Each non-empty role still has at least one bullet.
- Save/reload preserves `final_included` and `output_rank`.
- Copy/PDF/DOCX match.

Regression surfaces: omit/include controls, saved draft, exports.

---

## Scenario 4 — unsafe draft

- Route: `/resume` saved draft that requires regeneration, or a source-backed draft whose mapping can no longer be proven
- Viewport: desktop
- Preconditions: a draft whose GET payload has `requires_regeneration: true`, or whose export-validation returns 409

1. Open the draft. Confirm it remains readable: role headers, narrative, heading, and bullets still render.
2. Confirm the UI clearly indicates regeneration/review is required. If Phase 3 added a dedicated banner, record its copy. If only export toasts exist, record that as the current signal.
3. Click Copy, PDF, and DOCX.
4. Confirm none of the files are produced / clipboard is unchanged.
5. In Network, confirm `POST /resume/export-validation` returns 409 and is not bypassed by repeating the click.

Expected:

- Readable overlay/source-backed fields remain on screen.
- Export is blocked by backend validation, not only by a disabled button.
- Toast: `This resume must be re-generated after the source resume changes.` (or the Phase 3 equivalent).

Regression surfaces: GET `/resume/drafts/{id}`, POST `/resume/export-validation`, Copy/PDF/DOCX.

---

## Scenario 5 — legacy draft

- Route: `/resume`
- Viewport: desktop
- Preconditions: a saved draft **without** `tailoring_snapshot_version` of `full-context-v1` or `lossless-complete-v2` (legacy sparse/readable snapshot)

1. Open the legacy draft.
2. Confirm it remains readable.
3. Copy, PDF, and DOCX using the existing supported behavior.
4. Confirm export does **not** require source-backed export validation (`POST /resume/export-validation` is not called).
5. If the draft is `full-resume-v1` **with** canonical sections, export must be blocked client-side with `This saved resume must be re-generated before export.`

Expected:

- Legacy non-source-backed drafts stay readable and exportable through the legacy template path.
- `full-resume-v1` canonical drafts cannot be exported until regenerated.

Regression surfaces: `/resume` saved drafts, legacy export fallback.

---

## Scenario 6 — additional strong-match job

- Route: `/jobs/{id}` for the recorded strong-match job
- Viewport: desktop
- Preconditions: same approved source resume as Scenario 1

1. Generate a tailored resume.
2. Confirm tailoring emphasizes supported backend/platform evidence.
3. Confirm unsupported skills (including IAM, if absent from the source) are not invented.
4. Inspect every role with the shared inspection list.
5. Copy, export PDF, export DOCX, and compare semantically.

Expected:

- Strong overlap is reflected in included/recommended bullets and skills.
- No invented IAM or other unsupported claims.
- Surfaces agree.

Regression surfaces: tailor generation for a second job, exports.

---

## Console / network observations

Record for each scenario:

- Application `console.error` / failed chunks: none expected
- `GET /resume/tailor/{jobId}`: 200 on generate
- `POST /resume/drafts`: 201 on save
- `GET /resume/drafts/{id}`: 200 on reload
- `POST /resume/export-validation`: 200 on successful export; 409 on Scenario 4
- Download requests are client-side blob downloads, not API file streams

## Verdict

Use exactly one of: `QA PASSED`, `QA PASSED WITH NICE-TO-HAVE FINDINGS`, `QA FAILED`, `QA BLOCKED`.

This Phase 4 preparation does **not** execute Browser QA.
