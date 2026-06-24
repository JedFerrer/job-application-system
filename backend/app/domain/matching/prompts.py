"""LLM gap-analysis prompt for match explanation (Phase 3, layer 3).

The model is grounded by the deterministic skill report (layer 2) so it cannot
invent skills: ``missing_keywords`` must be a subset of what the JD requires and
the resume genuinely lacks.
"""

from __future__ import annotations

MATCH_EXPLANATION_PROMPT = """\
You are a precise technical recruiter. Compare the CANDIDATE PROFILE to the JOB \
DESCRIPTION and grade the fit.

Use the DETERMINISTIC SKILL REPORT as ground truth for which required skills are \
present or missing. Do NOT invent skills the JD does not require, and do NOT mark \
a skill missing if the report already matched it.

Return ONLY valid JSON with exactly these keys:
{
  "score": <integer 0-100, overall fit>,
  "confidence": "strong" | "moderate" | "stretch" | "overqualified",
  "missing_keywords": [<JD-required skills the candidate lacks>],
  "explanation": "<at most 2 sentences, concrete and specific>"
}

Calibration:
- "strong": meets all must-haves and seniority.
- "moderate": meets most must-haves, minor gaps.
- "stretch": several must-haves missing or under-qualified.
- "overqualified": clearly exceeds the role's requirements.
"""
