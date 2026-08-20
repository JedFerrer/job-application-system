"""Resume extraction prompt.

Adapted from hr-ai-nterviewer-saas (``services/prompts.py::RESUME_EXTRACTION_PROMPT``).
The field list mirrors ``ParsedProfile`` so prompt/schema drift can't silently
drop fields. Extraction only — the model must never invent data.
"""

from __future__ import annotations

RESUME_EXTRACTION_PROMPT_VERSION = "resume-extraction-prompt-v7"

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
- All CandidateProfile fields are normalized, human-readable profile values.
  Request-local candidate aliases such as `c7` are forbidden in profile fields;
  aliases are allowed only inside `source_references` below.
- work_history[].highlights are normalized CandidateProfile strings only. Each
  highlight must be a plain string such as "Built FastAPI APIs."; do not put
  `{{text, order}}` objects in profile highlights. Canonical source material is
  represented separately through `source_references` candidate aliases below.
- education (array of objects with: degree, institution, year)
- certifications (array of strings)
- languages (array of objects with: language, proficiency). Extract only
  clearly listed languages. Preserve the source language name and order. Use
  proficiency only when explicitly stated; never infer fluency; never translate
  or canonicalize names, split bilingual phrases, or create entries from
  incidental prose. Use null when proficiency is absent.
- licenses (array of objects with: name, issuer). Extract only clearly listed
  professional licenses and registrations. Preserve source wording and order.
  Include issuer only when explicitly stated; never infer credential status,
  numbers, dates, jurisdictions, or verification details. Use null when issuer
  is absent.
- links (object with: linkedin, github, portfolio, website; use full http:// or
  https:// URLs when present). If no professional links are present, return an
  empty object {{}} (or an object with those four keys set to null); never return
  null or an array for links. Nullable scalar link fields may be null.
- projects (array of objects with: name, description, technologies[],
  achievements[], url)
  Extract project information only when it is present in the resume. Do not invent
  project names, descriptions, technologies, achievements, or URLs. If no projects
  are present, return []. For each project, use [] (never null) for missing
  technologies or achievements. Nullable scalar project fields may be null.
- work_history (array of objects with: company, title, start, end, highlights[])
  For each role, "highlights" MUST include every verified/listed work bullet or
  highlight candidate assigned to that role by the source layout, in source order.
  Preserve role boundaries. Do not merge, summarize, or omit bullets merely to
  target a particular count. If a role has no assigned work bullets, return [].
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

- source_references (object selecting semantic source candidates only. The server
  provides a `SERVER-OWNED SOURCE CANDIDATES` list after this prompt. Return only
  candidate aliases such as `c0`, `c1`, and never return source prose, UUIDs,
  offsets, fingerprints, occurrence ordinals, or verification status. Use the
  shape `contact`/`links` maps of candidate aliases, `summary` as one alias or
  null, arrays of aliases for sections/skills/education/certifications/licenses/
  languages, and work-history/project objects containing alias fields and alias
  arrays. The server preserves every verified candidate that you do not
  semantically reference. If semantic grouping is uncertain, return a partial
  semantic subset rather than assigning candidates speculatively. A supported
  tailoring resume needs at least one work-history header plus one source
  highlight alias; otherwise return `null` for `source_references`.
  For each `work_history` role, use exactly one `header` candidate alias for
  the complete raw role header (company/title/date when those share one line),
  plus a `highlights` alias list. Never split one raw header into separate
  company/title/date references and never invent substrings.
  The optional role `order` is ignored; the server derives canonical role order
  from each resolved header candidate's source order.
  `education`, `certifications`, `licenses`, and `languages` MUST be arrays of
  candidate aliases, never profile-shaped objects. `skills` and `sections` are
  also alias arrays. Project objects use alias fields and alias arrays only.
  Do not emit `source_content`. Keep the two objects separate: profile
  `work_history[].highlights` are human-readable resume wording, while only
  `source_references` fields may contain `cN` aliases. Do not add `header` to
  profile work-history objects.)
- extraction_evidence (object with optional arrays for work_history, education,
  certifications, projects, languages, and licenses). Each item must contain
  item_index (the zero-based
  index in the corresponding extracted array) and excerpt (a short, contiguous
  excerpt copied exactly from the resume text). Return at most one item per index,
  never include contact fields, links, or skills, and return [] when no evidence is
  available. Evidence is advisory and must not change or remove profile facts.

If a scalar field cannot be determined, use an empty string or null. For list
fields, use []; for the links object, use {{}}. Never use null for a list or object
container.

Resume text:
{resume_text}"""


LEGACY_TAILOR_RESUME_PROMPT = """You are an expert resume writer helping a candidate \
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


TAILOR_RESUME_PROMPT = """You are making a conservative, selective resume-tailoring pass \
for a specific job description.

The supplied canonical source bullets are the sole authority for resume prose. \
The candidate profile, matched skills, missing skills, and job description are \
relevance context only and never authorize a new resume fact. Return valid JSON only — \
no markdown or explanation.

Rules:
- Canonical source bullets are the only resume-prose evidence. Profile and job
  fields are context only and cannot authorize new facts.
- Return only optional rewrite patches for the eligible source bullets below.
- Omit a bullet when it should be kept unchanged. Do not regenerate the resume.
- A rewrite must preserve the exact meaning and every substantive fact of its exact source bullet.
- Do not add or strengthen metrics, tools, skills, responsibilities, scope, \
ownership, architecture, leadership, seniority, employers, dates, certifications, \
education, projects, languages, or licenses.
- Do not use JD wording to claim a missing skill. In particular, never turn \
Docker into Kubernetes.
- Do not change role identity or move a bullet between roles. The application \
supplies all identity fields and preserves all bullets.
- Use a rewrite only when it is materially clearer for this JD; otherwise omit it.

Return exactly this shape (target_alias/evidence_aliases are preferred):
{{
  "rewrites": [
    {{"target_alias": "r0:b1", "rewritten_text": "...", "evidence_aliases": ["r0:b1"]}}
  ]
}}

Every rewrite must cite its target bullet's evidence ID. Additional factual
words may come only from explicitly cited bullets in the same role. Cite no
other role, project, skill-list, education, credential, or JD evidence. Do not
invent relationships between separately cited facts: neutral combinations are
allowed, but purpose, causality, integration, architecture, ownership,
leadership, and metrics remain source-local.

Concrete evidence-ID example:
- target evidence `r0:b0`: "Built FastAPI APIs."
- same-role evidence `r0:b1`: "Automated internal workflows."
- valid patch: {{"role_index": 0, "source_index": 0,
  "rewritten_text": "Built FastAPI APIs; automated internal workflows.",
  "evidence_ids": ["r0:b0", "r0:b1"]}}
- invalid patch: use no patch when the target ID is missing, an ID is from
  another role, or the rewrite says the APIs caused, enabled, backed, or
  integrated the separately cited workflow fact.
Use only IDs shown in the evidence bundle, include the target ID in every
patch, and omit the patch when no safe neutral rewrite is possible. Prefer exact
source wording joined with a semicolon; do not invent inflections or
use purpose, causal, dependency, or technical-integration language. Cite only
the target and the one strongest supporting bullet needed for the rewrite;
do not cite every relevant bullet in the role.

JOB DESCRIPTION:
{job_description}

ELIGIBLE SOURCE BULLETS (only these may be patched):
{eligible_bullets_json}

MATCHED_SKILLS:
{matched_skills}

MISSING_SKILLS (never claim these):
{missing_skills}"""
