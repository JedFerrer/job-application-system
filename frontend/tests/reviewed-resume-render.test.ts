import { beforeEach, describe, expect, it, vi } from "vitest";
import { buildResumeRenderModel, downloadResumeDocx, downloadResumePdf, formatResumeText } from "@/lib/cover-letter-export";
import { buildReviewedWorkHistory, semanticWorkHistoryLines } from "@/lib/reviewed-resume-render";
import type { TailoredBulletDecision, TailoredResume } from "@/lib/types";

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

const NARRATIVE = "Owned event delivery for payment processing across the platform.";
const HEADING = "Responsibilities";
const BULLET_ONE = "Designed an event pipeline that processed payments across three regions without interruption.";
const BULLET_TWO = "Coordinated the migration window and reduced failed retries.";
const SUGGESTED_ONE = "Designed a resilient event pipeline covering three payment regions without interruption.";
const FRAGMENT_ONE = "payments across three regions without";
const FRAGMENT_TWO = "migration window and reduced failed retries.";

function decision(overrides: Partial<TailoredBulletDecision> & Pick<TailoredBulletDecision, "source_id" | "source_index" | "original_text">): TailoredBulletDecision {
  return {
    canonical_order: overrides.source_index,
    suggested_text: overrides.original_text,
    final_text: overrides.original_text,
    recommended_included: true,
    final_included: true,
    recommendation_rank: overrides.source_index,
    output_rank: overrides.source_index,
    ...overrides,
  };
}

function malformedClassResume(overrides: Partial<TailoredResume> = {}): TailoredResume {
  return {
    job_id: 9,
    professional_summary: "Platform engineer with event-driven delivery experience.",
    full_name: "Casey Rivers",
    email: "casey@example.com",
    phone: null,
    headline: "Platform Engineer",
    skills: ["Python"],
    links: { linkedin: null, github: null, portfolio: null, website: null },
    projects: [],
    languages: [],
    licenses: [],
    education: [],
    certifications: [],
    matched_skills: [],
    other_skills: [],
    missing_keywords: [],
    tailoring_snapshot_version: "full-context-v1",
    work_history: [{
      company: "Northwind",
      title: "Platform Engineer",
      start: "2021-03",
      end: null,
      original_highlights: [BULLET_ONE, BULLET_TWO],
      tailored_highlights: [BULLET_ONE, BULLET_TWO],
      tailored_highlight_source_indices: [0, 1],
      source_role_id: "role-northwind",
      source_header: "Platform Engineer | Northwind",
      role_context: [NARRATIVE],
      responsibilities_heading: HEADING,
      selection_decisions: [
        decision({
          source_id: "00000000-0000-0000-0000-000000000001",
          source_index: 0,
          original_text: BULLET_ONE,
          suggested_text: SUGGESTED_ONE,
          final_text: SUGGESTED_ONE,
          output_rank: 0,
        }),
        decision({
          source_id: "00000000-0000-0000-0000-000000000002",
          source_index: 1,
          original_text: BULLET_TWO,
          suggested_text: BULLET_TWO,
          final_text: BULLET_TWO,
          output_rank: 1,
        }),
      ],
    }],
    canonical_sections: [
      {
        section: "summary",
        heading: "Summary",
        canonical_order: 0,
        items: [{ source_id: "summary-0", text: "Platform engineer with event-driven delivery experience.", canonical_order: 0 }],
      },
      {
        section: "unclassified",
        heading: "",
        canonical_order: 9,
        items: [
          { source_id: "unclassified-1", text: FRAGMENT_ONE, canonical_order: 9 },
          { source_id: "unclassified-2", text: FRAGMENT_TWO, canonical_order: 10 },
        ],
      },
    ],
    ...overrides,
  };
}

function standalone(values: string[], fragment: string): boolean {
  return values.some((value) => {
    const trimmed = value.trim().replace(/^•\s*/, "");
    return trimmed === fragment;
  });
}

function workHistoryOutputs(copied: string, pdf: string[], docx: string[]): string[][] {
  return [
    copied.split(/\n/).map((line) => line.trim()).filter(Boolean),
    pdf.map((line) => line.trim()).filter(Boolean),
    docx.map((line) => line.trim()).filter(Boolean),
  ];
}

describe("canonical reviewed work-history render model", () => {
  beforeEach(() => {
    capture.pdfText.length = 0;
    capture.docxText.length = 0;
    vi.stubGlobal("URL", { createObjectURL: () => "blob:resume", revokeObjectURL: () => {} });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("normalizes source-backed roles once and feeds identical content to copy, PDF, and DOCX", async () => {
    const resume = malformedClassResume();
    const roles = buildReviewedWorkHistory(resume);
    const semantic = semanticWorkHistoryLines(resume);

    expect(roles).toHaveLength(1);
    expect(roles[0]?.narrative).toBe(NARRATIVE);
    expect(roles[0]?.responsibilityHeading).toBe(HEADING);
    expect(roles[0]?.bullets.map((bullet) => bullet.text)).toEqual([SUGGESTED_ONE, BULLET_TWO]);
    expect(buildResumeRenderModel(resume).find((section) => section.section === "work_history")?.roles).toEqual(roles);

    const copied = formatResumeText(resume);
    downloadResumePdf(resume, "canonical-role");
    await downloadResumeDocx(resume, "canonical-role");

    expect(semantic).toEqual([NARRATIVE, HEADING, SUGGESTED_ONE, BULLET_TWO]);
    for (const output of [copied, capture.pdfText.join("\n"), capture.docxText.join("\n")]) {
      expect(output).toContain(NARRATIVE);
      expect(output).toContain(HEADING);
      expect(output).toContain(SUGGESTED_ONE);
      expect(output).toContain(BULLET_TWO);
      expect(output.indexOf(NARRATIVE)).toBeLessThan(output.indexOf(HEADING));
      expect(output.indexOf(HEADING)).toBeLessThan(output.indexOf(SUGGESTED_ONE));
      expect(output.indexOf(SUGGESTED_ONE)).toBeLessThan(output.indexOf(BULLET_TWO));
    }
  });

  it("does not render unclassified continuation fragments as role narrative or duplicate bullets", async () => {
    const resume = malformedClassResume();
    const role = buildReviewedWorkHistory(resume)[0]!;
    expect(role.narrative).toBe(NARRATIVE);
    expect(role.narrative).not.toBe(FRAGMENT_ONE);
    expect(role.bullets.map((bullet) => bullet.text)).toEqual([SUGGESTED_ONE, BULLET_TWO]);
    expect(role.bullets.some((bullet) => bullet.text === FRAGMENT_ONE || bullet.text === FRAGMENT_TWO)).toBe(false);

    const copied = formatResumeText(resume);
    downloadResumePdf(resume, "canonical-role");
    await downloadResumeDocx(resume, "canonical-role");
    const outputs = workHistoryOutputs(copied, capture.pdfText, capture.docxText);
    for (const lines of outputs) {
      expect(standalone(lines, FRAGMENT_ONE)).toBe(false);
      expect(standalone(lines, FRAGMENT_TWO)).toBe(false);
      expect(lines.filter((line) => line.includes(NARRATIVE))).toHaveLength(1);
      expect(lines.filter((line) => line === HEADING || line.includes(HEADING)).length).toBeGreaterThanOrEqual(1);
      expect(lines.filter((line) => line.includes(HEADING)).length).toBe(1);
      expect(lines.some((line) => line.includes(BULLET_ONE) || line.includes(SUGGESTED_ONE))).toBe(true);
      expect(lines.some((line) => line.includes(BULLET_TWO))).toBe(true);
    }
    expect(copied).not.toContain(FRAGMENT_ONE);
    expect(capture.pdfText.join("\n")).not.toContain(FRAGMENT_ONE);
    expect(capture.docxText.join("\n")).not.toContain(FRAGMENT_ONE);
  });

  it("uses reviewed final_text and output_rank rather than suggested_text or source order", async () => {
    const resume = malformedClassResume();
    resume.work_history[0] = {
      ...resume.work_history[0]!,
      tailored_highlights: [SUGGESTED_ONE, BULLET_TWO],
      tailored_highlight_source_indices: [0, 1],
      selection_decisions: [
        decision({
          source_id: "00000000-0000-0000-0000-000000000001",
          source_index: 0,
          original_text: BULLET_ONE,
          suggested_text: SUGGESTED_ONE,
          final_text: BULLET_ONE,
          recommendation_rank: 0,
          output_rank: 1,
        }),
        decision({
          source_id: "00000000-0000-0000-0000-000000000002",
          source_index: 1,
          original_text: BULLET_TWO,
          suggested_text: "Moved migration coordination earlier.",
          final_text: BULLET_TWO,
          recommendation_rank: 1,
          output_rank: 0,
        }),
      ],
    };

    const bullets = buildReviewedWorkHistory(resume)[0]!.bullets;
    expect(bullets.map((bullet) => bullet.text)).toEqual([BULLET_TWO, BULLET_ONE]);
    expect(semanticWorkHistoryLines(resume)).not.toContain(SUGGESTED_ONE);
    expect(semanticWorkHistoryLines(resume)).not.toContain("Moved migration coordination earlier.");

    const copied = formatResumeText(resume);
    downloadResumePdf(resume, "restored-order");
    await downloadResumeDocx(resume, "restored-order");
    for (const output of [copied, capture.pdfText.join("\n"), capture.docxText.join("\n")]) {
      expect(output.indexOf(BULLET_TWO)).toBeLessThan(output.indexOf(BULLET_ONE));
      expect(output).toContain(BULLET_ONE);
      expect(output).not.toContain(SUGGESTED_ONE);
    }
  });

  it("omits bullets with final_included false from every export surface", async () => {
    const resume = malformedClassResume();
    resume.work_history[0] = {
      ...resume.work_history[0]!,
      tailored_highlights: [SUGGESTED_ONE],
      tailored_highlight_source_indices: [0],
      selection_decisions: [
        decision({
          source_id: "00000000-0000-0000-0000-000000000001",
          source_index: 0,
          original_text: BULLET_ONE,
          suggested_text: SUGGESTED_ONE,
          final_text: SUGGESTED_ONE,
          output_rank: 0,
        }),
        decision({
          source_id: "00000000-0000-0000-0000-000000000002",
          source_index: 1,
          original_text: BULLET_TWO,
          suggested_text: BULLET_TWO,
          final_text: BULLET_TWO,
          recommended_included: false,
          final_included: false,
          recommendation_rank: null,
          output_rank: null,
        }),
      ],
    };

    expect(buildReviewedWorkHistory(resume)[0]!.bullets.map((bullet) => bullet.text)).toEqual([SUGGESTED_ONE]);
    const copied = formatResumeText(resume);
    downloadResumePdf(resume, "omitted-bullet");
    await downloadResumeDocx(resume, "omitted-bullet");
    for (const output of [copied, capture.pdfText.join("\n"), capture.docxText.join("\n")]) {
      expect(output).toContain(SUGGESTED_ONE);
      expect(output).not.toContain(BULLET_TWO);
    }
  });

  it("hides a responsibility heading when no reviewed bullets remain", () => {
    const resume = malformedClassResume();
    resume.work_history[0] = {
      ...resume.work_history[0]!,
      tailored_highlights: [],
      tailored_highlight_source_indices: [],
      selection_decisions: [],
      responsibilities_heading: HEADING,
    };
    expect(buildReviewedWorkHistory(resume)[0]?.responsibilityHeading).toBeNull();
    expect(formatResumeText(resume)).not.toContain(HEADING);
  });
});
