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
- years_experience (number, calculate from the EARLIEST work or internship start date \
to today; include internships and part-time roles)
- skills (array of strings)
- education (array of objects with: degree, institution, year)
- certifications (array of strings)
- links (object with: linkedin, github, portfolio, website; use full http:// or
  https:// URLs when present). If no professional links are present, return an
  empty object {} (or an object with those four keys set to null); never return
  null or an array for links. Nullable scalar link fields may be null.
- projects (array of objects with: name, description, technologies[],
  achievements[], url)
  Extract project information only when it is present in the resume. Do not invent
  project names, descriptions, technologies, achievements, or URLs. If no projects
  are present, return []. For each project, use [] (never null) for missing
  technologies or achievements. Nullable scalar project fields may be null.
- work_history (array of objects with: company, title, start, end, highlights[])
  For each role, "highlights" MUST be an array of 2-5 bullet points describing \
key achievements, responsibilities, and impact. Extract these from the resume's \
role descriptions. If the resume has bullets under each role, include them all.
- preferred_roles (array of strings, inferred from titles/objective if present)
- seniority (one of: "entry", "mid", "senior", "staff", "lead")
  Seniority guidelines — use ALL signals, not just years alone:
  - "entry": 0–2 years, no leadership
  - "mid": 2–5 years, works independently
  - "senior": 5+ years OR held titles like Senior, Team Lead, Tech Lead, \
Principal; makes architectural decisions
  - "staff": 8+ years with cross-team technical leadership
  - "lead": explicit people/team manager or Tech Lead with org-wide impact
  When in doubt between mid and senior, prefer "senior" for candidates with 6+ years of \
professional experience.

If a scalar field cannot be determined, use an empty string or null. For list
fields, use []; for the links object, use {}. Never use null for a list or object
container.

Resume text:
{resume_text}"""


TAILOR_RESUME_PROMPT = """You are an expert resume writer helping a candidate \
tailor their resume for a specific job description.

RULES:
- Use ONLY facts from the CANDIDATE PROFILE. Never invent or imply numbers, \
metrics, tools, skills, employers, dates, certifications, responsibilities, \
team sizes, ownership, or seniority.
- Do not add a quantified result or claim that is not present in the \
corresponding source highlight.
- Preserve the distinction between contributed, supported, implemented, owned, \
and led. Do not upgrade "contributed" or "supported" into "led", "owned", \
"architected", or equivalent language.
- Rewrite each source highlight only to emphasize relevant, supported experience \
for the JOB DESCRIPTION. Job-description wording may not turn a missing skill \
into a claimed skill.
- Preserve the source role order, role count, bullet order, and bullet count.
- Keep bullets concise, but do not add or remove source content to meet a target \
length.
- Write a concise PROFESSIONAL SUMMARY using only facts supported by the profile.

Return valid JSON only — no markdown, no explanation:
{{
  "professional_summary": "summary tailored to this JD",
  "tailored_work_history": [
    {{
      "tailored_highlights": ["Supported rephrasing of source highlight", "..."]
    }}
  ]
}}

IMPORTANT:
- The application will supply company, title, start, and end from the source. \
Do not return or modify identity fields.
- tailored_highlights must have the same NUMBER and ORDER as the source \
highlights. Rephrase them, don't add or remove.
- Never claim a skill from MISSING_SKILLS, and never invent a skill outside the \
candidate profile.

JOB DESCRIPTION:
{job_description}

CANDIDATE PROFILE (work history with original highlights):
{work_history_json}

MATCHED_SKILLS (skills in both profile and JD):
{matched_skills}

CANDIDATE_SKILLS (all skills from profile):
{candidate_skills}

MISSING_SKILLS (skills in the JD but not supported by the profile):
{missing_skills}"""
