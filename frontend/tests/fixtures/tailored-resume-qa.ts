import type { TailoredResume } from "@/lib/types";

/** Sanitized wrap-continuation resume matching backend expected_semantic.json. */
export const QA_WRAP_NARRATIVE =
  "Worked across backend and platform systems supporting operational workflows.";
export const QA_WRAP_HEADING = "Responsibilities";
export const QA_WRAP_BULLETS = [
  "Designed an event pipeline that processed payments across three regions without interruption.",
  "Coordinated the migration window and reduced failed retries.",
  "Built internal APIs and operational dashboards.",
] as const;
export const QA_SECOND_NARRATIVE = "Maintained internal tooling for release operations.";
export const QA_SECOND_BULLET = "Improved deployment checks for backend services.";
export const QA_ORPHAN_FRAGMENTS = [
  "supporting operational workflows.",
  "payments across three regions without interruption.",
  "failed retries.",
] as const;
export const QA_TRUNCATED_PREFIXES = [
  "Worked across backend and platform systems",
  "Designed an event pipeline that processed",
  "Coordinated the migration window and reduced",
] as const;

export function qaWrapResume(overrides: Partial<TailoredResume> = {}): TailoredResume {
  return {
    job_id: 6781,
    professional_summary: "Backend and platform engineer with operational delivery experience.",
    full_name: "Jordan Example",
    email: "jordan@example.com",
    phone: null,
    headline: "Senior Engineer",
    skills: ["Python", "FastAPI", "PostgreSQL"],
    links: { linkedin: null, github: null, portfolio: null, website: null },
    projects: [],
    languages: [],
    licenses: [],
    education: [],
    certifications: [],
    matched_skills: ["python", "fastapi"],
    other_skills: ["postgresql"],
    missing_keywords: [],
    tailoring_snapshot_version: "full-context-v1",
    summary_evidence_ids: ["summary:0"],
    canonical_sections: [
      {
        section: "contact",
        heading: "Contact",
        canonical_order: 0,
        items: [
          { source_id: "contact-name", text: "Jordan Example", canonical_order: 0 },
          { source_id: "contact-email", text: "jordan@example.com", canonical_order: 1 },
        ],
      },
      {
        section: "summary",
        heading: "PROFESSIONAL SUMMARY",
        canonical_order: 1,
        items: [
          {
            source_id: "summary-0",
            text: "Backend and platform engineer with operational delivery experience.",
            canonical_order: 2,
          },
        ],
      },
      {
        section: "skills",
        heading: "TECHNICAL SKILLS",
        canonical_order: 3,
        items: [
          { source_id: "skill-0", text: "Python", canonical_order: 4 },
          { source_id: "skill-1", text: "FastAPI", canonical_order: 5 },
          { source_id: "skill-2", text: "PostgreSQL", canonical_order: 6 },
        ],
      },
      {
        section: "unclassified",
        canonical_order: 99,
        items: [{ source_id: "unclassified-0", text: "PASSTHROUGH_SHOULD_NOT_EXPORT", canonical_order: 99 }],
      },
    ],
    work_history: [
      {
        company: "Acme Platform",
        title: "Senior Engineer",
        start: "2020-01",
        end: null,
        source_header: "Acme Platform - Senior Engineer",
        source_role_id: "role-a",
        role_context: [
          "Worked across backend and platform systems",
          "supporting operational workflows.",
        ],
        responsibilities_heading: QA_WRAP_HEADING,
        original_highlights: [...QA_WRAP_BULLETS],
        tailored_highlights: [...QA_WRAP_BULLETS],
        tailored_highlight_source_indices: [0, 1, 2],
        selection_decisions: QA_WRAP_BULLETS.map((text, index) => ({
          source_id: `00000000-0000-0000-0000-00000000000${index + 1}`,
          source_index: index,
          canonical_order: index,
          original_text: text,
          suggested_text: index === 0
            ? "Designed a resilient event pipeline that processed payments across three regions without interruption."
            : text,
          final_text: index === 0
            ? "Designed a resilient event pipeline that processed payments across three regions without interruption."
            : text,
          recommended_included: true,
          final_included: true,
          recommendation_rank: index,
          output_rank: index,
        })),
      },
      {
        company: "Beacon Systems",
        title: "Software Engineer",
        start: "2017-01",
        end: "2019-12",
        source_header: "Beacon Systems - Software Engineer",
        source_role_id: "role-b",
        role_context: [QA_SECOND_NARRATIVE],
        responsibilities_heading: QA_WRAP_HEADING,
        original_highlights: [QA_SECOND_BULLET],
        tailored_highlights: [QA_SECOND_BULLET],
        tailored_highlight_source_indices: [0],
        selection_decisions: [
          {
            source_id: "00000000-0000-0000-0000-000000000010",
            source_index: 0,
            canonical_order: 10,
            original_text: QA_SECOND_BULLET,
            suggested_text: QA_SECOND_BULLET,
            final_text: QA_SECOND_BULLET,
            recommended_included: true,
            final_included: true,
            recommendation_rank: 0,
            output_rank: 0,
          },
        ],
      },
    ],
    ...overrides,
  };
}

export function qaLegacyResume(): TailoredResume {
  return {
    job_id: 42,
    professional_summary: "Legacy readable draft without full-context snapshot fields.",
    full_name: "Jordan Example",
    email: "jordan@example.com",
    phone: null,
    headline: "Senior Engineer",
    skills: ["Python"],
    links: { linkedin: null, github: null, portfolio: null, website: null },
    projects: [],
    languages: [],
    licenses: [],
    work_history: [
      {
        company: "Acme Platform",
        title: "Senior Engineer",
        start: null,
        end: null,
        original_highlights: [QA_WRAP_BULLETS[2]],
        tailored_highlights: [QA_WRAP_BULLETS[2]],
        role_context: [QA_WRAP_NARRATIVE],
        responsibilities_heading: QA_WRAP_HEADING,
      },
    ],
    education: [],
    certifications: [],
    matched_skills: ["python"],
    other_skills: [],
    missing_keywords: [],
  };
}
