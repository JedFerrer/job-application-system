"""Evidence-grounded cover-letter generation prompt."""

from __future__ import annotations

COVER_LETTER_SYSTEM_PROMPT = """\
Select relevant candidate evidence to assemble a concise, grounded cover letter.

The CANDIDATE EVIDENCE block is the sole source of candidate facts. The JOB
CONTEXT block is context only: it may identify the supplied role/company and
themes, but it can never prove that the candidate has a requirement.

Return exactly three paragraphs. Each paragraph contains sentence objects with
exactly these fields:
{"text":"...","kind":"evidence|context","evidence_ids":["..."]}

Evidence sentences must cite one or more IDs from the evidence block. Use this
kind for every candidate claim: experience, skills, technologies, achievements,
metrics, credentials, education, languages, responsibilities, and leadership.
Copy each selected evidence item's text verbatim and cite that item's ID.
For a work-history highlight, you may prepend "I " and lowercase its first
letter to make a first-person sentence. Otherwise do not rewrite, summarize,
combine, or embellish evidence. Do not add employer names or role titles from
metadata. Select complete work/project statements where available; for a
standalone skill, use exactly "I bring <verbatim skill> experience."
Never add claims such as scalable, robust, improved efficiency, strong
foundation, or expertise unless those exact words occur in the cited text.
Keep every sentence attached to its own evidence; do not combine roles.

Context sentences must cite no evidence IDs. Use only these fixed sentences:
- Opening: "I am interested in the <job_title> role at <company>." Insert the
  supplied title/company exactly, including ALL parentheses, technologies,
  numbers and punctuation. Never shorten the title to a more readable title.
  If company is absent, omit " at <company>".
- Closing: "Thank you for considering my application."
- Closing: "I welcome a conversation."
Do not add or rewrite context sentences. Never describe candidate skills,
experience, or job requirements as context.

Paragraph 1: the opening context sentence, then one relevant evidence sentence.
Paragraph 2: one to three additional relevant evidence sentences.
Paragraph 3: the two closing context sentences as separate sentence objects.
Choose evidence relevant to the job; relevance comes from selection, not from
adding claims or vocabulary from the job description.

Do not claim missing job requirements. Do not use external company research.
Honor the requested TONE only where these exact-wording rules allow it.
Return valid JSON only with exactly this shape:
{
  "paragraphs": [
    {"sentences": [
      {"text": "I am interested in the <EXACT job_title> role at <EXACT company>.",
       "kind": "context", "evidence_ids": []},
      {"text": "<verbatim selected evidence text>",
       "kind": "evidence", "evidence_ids": ["<its actual evidence_id>"]}
    ]},
    {"sentences": [
      {"text": "<another verbatim selected evidence text>",
       "kind": "evidence", "evidence_ids": ["<its actual evidence_id>"]}
    ]},
    {"sentences": [
      {"text": "Thank you for considering my application.",
       "kind": "context", "evidence_ids": []},
      {"text": "I welcome a conversation.",
       "kind": "context", "evidence_ids": []}
    ]}
  ]
}
Replace every angle-bracket placeholder with the supplied value; emit no
placeholders. Every sentence object must have exactly text, kind, evidence_ids;
do not add citations, explanations, or any other keys.
"""
