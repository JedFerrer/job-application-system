# Tailored-resume QA fixtures

Sanitized wrap-continuation resume used by Phase 4 regression tests.

| File | Purpose |
|---|---|
| `malformed_wrap_two_role.txt` | Raw source text with wrapped narrative and bullets plus a second role |
| `expected_semantic.json` | Logical result: one narrative, one heading, three complete bullets, second-role separation |

Compare exported PDF/DOCX/copy text:

```bash
cd backend
uv run python tests/unit/resume_qa_support.py --expected tests/fixtures/tailored_resume_qa/expected_semantic.json copy.txt resume.pdf resume.docx
```
