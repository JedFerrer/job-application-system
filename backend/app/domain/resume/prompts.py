"""Resume extraction prompt.

Adapted from hr-ai-nterviewer-saas (``services/prompts.py::RESUME_EXTRACTION_PROMPT``).
The field list mirrors ``ParsedProfile`` so prompt/schema drift can't silently
drop fields. Extraction only — the model must never invent data.
"""

from __future__ import annotations

RESUME_EXTRACTION_PROMPT = """You are an expert resume parser. Extract structured \
information from the resume text below. Return valid JSON only — no markdown, no \
explanation. Extract only what is present; never invent data.

Fields to extract:
- full_name (string)
- email (string)
- phone (string)
- headline (string, a 1-line professional headline or current role)
- years_experience (number, calculate from the EARLIEST work or internship start date to today; include internships and part-time roles)
- skills (array of strings)
- education (array of objects with: degree, institution, year)
- certifications (array of strings)
- work_history (array of objects with: company, title, start, end, highlights[])
- preferred_roles (array of strings, inferred from titles/objective if present)
- seniority (one of: "entry", "mid", "senior", "staff", "lead")
  Seniority guidelines — use ALL signals, not just years alone:
  - "entry": 0–2 years, no leadership
  - "mid": 2–5 years, works independently
  - "senior": 5+ years OR held titles like Senior, Team Lead, Tech Lead, Principal; makes architectural decisions
  - "staff": 8+ years with cross-team technical leadership
  - "lead": explicit people/team manager or Tech Lead with org-wide impact
  When in doubt between mid and senior, prefer "senior" for candidates with 6+ years of professional experience.

If a field cannot be determined, use an empty string, empty array, or null.

Resume text:
{resume_text}"""
