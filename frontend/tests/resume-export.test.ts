import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TailoredCanonicalSection, TailoredResume } from "@/lib/types";

const capture = vi.hoisted(() => ({ pdfText: [] as string[], docxText: [] as string[] }));

vi.mock("jspdf", () => {
  class MockJsPdf {
    internal = { pageSize: { getWidth: () => 215.9, getHeight: () => 279.4 } };
    setFont() {}
    setFontSize() {}
    setDrawColor() {}
    setLineWidth() {}
    line() {}
    addPage() {}
    splitTextToSize(text: string) { return [text]; }
    text(value: string | string[]) { capture.pdfText.push(...(Array.isArray(value) ? value : [value])); }
    save() {}
  }
  return { default: MockJsPdf };
});

vi.mock("docx", () => {
  class TextRun { constructor(readonly options: { text: string }) {} }
  class Paragraph {
    constructor(options: { children?: TextRun[] }) {
      for (const child of options.children ?? []) capture.docxText.push(child.options.text);
    }
  }
  class Document { constructor(_options: unknown) {} }
  return { TextRun, Paragraph, Document, Packer: { toBlob: vi.fn(async () => new Blob()) } };
});

import { buildResumeHeaderItems, buildResumeRenderModel, downloadResumeDocx, downloadResumePdf, formatResumeText } from "@/lib/cover-letter-export";

const SUMMARY = "SUMMARY_MARKER: lossless platform leadership";
const SKILLS = "SKILLS_MARKER: TypeScript, FastAPI, PostgreSQL";
const CONTEXT = "ROLE_CONTEXT_MARKER: Led a 12-person platform team.";
const RESPONSIBILITIES = "RESPONSIBILITIES_MARKER:";
const EDUCATION = "EDUCATION_MARKER: BSc Computer Science";
const AWARDS = "AWARDS_MARKER: Engineering Excellence";
const VOLUNTEER = "VOLUNTEER_MARKER: Community mentor";

function canonical(
  section: TailoredCanonicalSection["section"],
  heading: string,
  text: string,
  canonicalOrder: number,
): TailoredCanonicalSection {
  return { section, heading, canonical_order: canonicalOrder, items: [{ source_id: `${section}-${canonicalOrder}`, text, canonical_order: 0 }] };
}

const resume: TailoredResume = {
  job_id: 1,
  professional_summary: SUMMARY,
  full_name: "Ada Lovelace",
  email: "ada@example.com",
  phone: "555-0100",
  headline: "Platform Engineer",
  skills: [SKILLS],
  links: { linkedin: null, github: null, portfolio: null, website: null },
  projects: [],
  languages: [],
  licenses: [],
  education: [],
  certifications: [],
  matched_skills: [],
  other_skills: [],
  missing_keywords: [],
  work_history: [{
    company: "Analytical Engines",
    title: "Platform Engineer",
    start: "2020-01",
    end: null,
    original_highlights: ["Built the platform."],
    tailored_highlights: ["Built the platform."],
    role_context: [CONTEXT],
    responsibilities_heading: RESPONSIBILITIES,
  }],
  canonical_sections: [
    canonical("contact", "Contact", "LOCATION_MARKER: London", 0),
    canonical("summary", "Summary", SUMMARY, 1),
    canonical("skills", "Skills", SKILLS, 2),
    canonical("education", "Education", EDUCATION, 3),
    canonical("named", "Awards & Achievements", AWARDS, 4),
    canonical("named", "Volunteer & Community", VOLUNTEER, 5),
  ],
};

function count(values: string[], marker: string): number {
  return values.filter((value) => value.includes(marker)).length;
}

describe("lossless resume exports", () => {
  beforeEach(() => {
    capture.pdfText.length = 0;
    capture.docxText.length = 0;
    vi.stubGlobal("URL", { createObjectURL: () => "blob:resume", revokeObjectURL: () => {} });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("emits each canonical substantive section once in the actual PDF and DOCX paths", async () => {
    downloadResumePdf(resume, "lossless-resume");
    await downloadResumeDocx(resume, "lossless-resume");

    for (const output of [capture.pdfText, capture.docxText]) {
      expect(count(output, SUMMARY)).toBe(1);
      expect(count(output, SKILLS)).toBe(1);
      expect(output).not.toContain("SUMMARY");
      expect(output).not.toContain("SKILLS");
      expect(output).toContain(CONTEXT);
      expect(output).toContain(RESPONSIBILITIES);
      expect(output).toContain(EDUCATION);
      expect(output).toContain(AWARDS);
      expect(output).toContain(VOLUNTEER);
    }
  });

  it("retains semantic identities and never creates an empty canonical heading", () => {
    const sections = buildResumeRenderModel(resume);
    expect(sections.find((section) => section.section === "summary")?.heading).toBe("Summary");
    expect(sections.find((section) => section.section === "skills")?.heading).toBe("Skills");
    expect(sections.every((section) => Boolean(section.heading?.trim()))).toBe(true);
  });

  it("renders the complete v4 header and wrapped summary once in source order", async () => {
    const complete: TailoredResume = {
      ...resume,
      canonical_sections: [
        {
          section: "contact",
          items: [
            { source_id: "name", text: "Ada Lovelace", canonical_order: 0 },
            { source_id: "address-1", text: "12 Analytical Engine Lane", canonical_order: 1 },
            { source_id: "address-2", text: "London, UK", canonical_order: 2 },
            { source_id: "email", text: "ada@example.com", canonical_order: 5 },
            { source_id: "phone", text: "+44 20 0000 0000", canonical_order: 6 },
          ],
          canonical_order: 0,
        },
        {
          section: "links",
          items: [
            { source_id: "github", text: "github.com/ada", canonical_order: 3 },
            { source_id: "website", text: "https://ada.example", canonical_order: 4 },
          ],
          canonical_order: 3,
        },
        {
          section: "summary",
          heading: "PROFESSIONAL SUMMARY",
          items: [
            { source_id: "summary-1", text: "Full-stack engineer with API design and", canonical_order: 8 },
            { source_id: "summary-2", text: "automation expertise.", canonical_order: 9 },
          ],
          canonical_order: 7,
        },
        {
          section: "skills",
          heading: "TECHNICAL STACK",
          items: ["Python", "FastAPI", "Django", "Ruby on Rails", "React", "AWS", "Docker"].map((text, index) => ({ source_id: `skill-${index}`, text, canonical_order: 11 + index })),
          canonical_order: 10,
        },
      ],
      source_schema_version: "source-resume-v4",
    };

    const header = buildResumeHeaderItems(complete);
    const copied = formatResumeText(complete);
    expect(header).toEqual([
      "Ada Lovelace", "12 Analytical Engine Lane", "London, UK", "github.com/ada",
      "https://ada.example", "ada@example.com", "+44 20 0000 0000",
    ]);
    for (const value of header) expect(count([copied], value)).toBe(1);
    expect(copied.indexOf("London, UK")).toBeLessThan(copied.indexOf("github.com/ada"));
    expect(copied.indexOf("github.com/ada")).toBeLessThan(copied.indexOf("ada@example.com"));
    expect(count([copied], "Full-stack engineer with API design and automation expertise.")).toBe(1);
    expect(count([copied], "TECHNICAL STACK")).toBe(1);
    expect(copied).not.toContain("Full-stack engineer with API design and\nautomation expertise.");

    downloadResumePdf(complete, "v4-resume");
    await downloadResumeDocx(complete, "v4-resume");
    for (const output of [capture.pdfText, capture.docxText]) {
      for (const value of header) expect(count(output, value)).toBe(1);
      expect(count(output, "Full-stack engineer with API design and automation expertise.")).toBe(1);
      expect(count(output, "PROFESSIONAL SUMMARY")).toBe(1);
      expect(count(output, "TECHNICAL STACK")).toBe(1);
      expect(output).not.toContain("Full-stack engineer with API design and\n");
    }
  });

  it("excludes unclassified passthrough content from exports", async () => {
    const marker = "PASSTHROUGH_MARKER: literal source text";
    const passthrough: TailoredResume = {
      ...resume,
      canonical_sections: [
        ...resume.canonical_sections!,
        canonical("unclassified", "", marker, 6),
      ],
    };

    downloadResumePdf(passthrough, "lossless-resume");
    await downloadResumeDocx(passthrough, "lossless-resume");

    for (const output of [capture.pdfText, capture.docxText]) {
      expect(count(output, marker)).toBe(0);
      expect(output).not.toContain("UNCLASSIFIED");
      expect(output).not.toContain("OTHER");
    }
    expect(buildResumeRenderModel(passthrough).find((section) => section.section === "unclassified")).toBeUndefined();
  });

  it("keeps the normalized header fallback when canonical sections are empty", () => {
    const legacy = { ...resume, canonical_sections: [] };
    expect(buildResumeHeaderItems(legacy)).toEqual([
      "Ada Lovelace", "Platform Engineer", "ada@example.com", "555-0100",
    ]);
    expect(formatResumeText(legacy)).toContain("Ada Lovelace");
  });
});
