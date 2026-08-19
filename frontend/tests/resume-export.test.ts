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

import { buildResumeRenderModel, downloadResumeDocx, downloadResumePdf } from "@/lib/cover-letter-export";

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
    expect(sections.find((section) => section.section === "summary")?.heading).toBe("SUMMARY");
    expect(sections.find((section) => section.section === "skills")?.heading).toBe("SKILLS");
    expect(sections.every((section) => Boolean(section.heading.trim()))).toBe(true);
  });
});
