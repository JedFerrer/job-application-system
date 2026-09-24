import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildResumeRenderModel,
  downloadResumeDocx,
  downloadResumePdf,
  formatResumeText,
} from "@/lib/cover-letter-export";
import { buildReviewedWorkHistory, semanticWorkHistoryLines } from "@/lib/reviewed-resume-render";
import {
  QA_ORPHAN_FRAGMENTS,
  QA_SECOND_BULLET,
  QA_SECOND_NARRATIVE,
  QA_TRUNCATED_PREFIXES,
  QA_WRAP_BULLETS,
  QA_WRAP_HEADING,
  QA_WRAP_NARRATIVE,
  qaWrapResume,
} from "./fixtures/tailored-resume-qa";

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
    output() { return new Blob(["%PDF-1.4\n"], { type: "application/pdf" }); }
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

function normalize(value: string): string {
  return value.replace(/\u2022/g, " ").replace(/\s+/g, " ").trim();
}

function qaWrapResumeOriginalBullets() {
  const resume = qaWrapResume();
  return qaWrapResume({
    work_history: [
      {
        ...resume.work_history[0]!,
        tailored_highlights: [...QA_WRAP_BULLETS],
        selection_decisions: resume.work_history[0]!.selection_decisions!.map((decision) => ({
          ...decision,
          suggested_text: decision.original_text,
          final_text: decision.original_text,
        })),
      },
      resume.work_history[1]!,
    ],
  });
}

function countNormalized(haystack: string, needle: string): number {
  const source = normalize(haystack);
  const target = normalize(needle);
  if (!target) return 0;
  let count = 0;
  let start = 0;
  while (true) {
    const found = source.indexOf(target, start);
    if (found < 0) return count;
    count += 1;
    start = found + target.length;
  }
}

function assertSemanticSurface(surface: string): void {
  const tokens = [
    "Acme Platform - Senior Engineer",
    QA_WRAP_NARRATIVE,
    QA_WRAP_HEADING,
    ...QA_WRAP_BULLETS,
    "Beacon Systems - Software Engineer",
    QA_SECOND_NARRATIVE,
    QA_WRAP_HEADING,
    QA_SECOND_BULLET,
  ];
  const uniqueOnce = [
    "Acme Platform - Senior Engineer",
    QA_WRAP_NARRATIVE,
    ...QA_WRAP_BULLETS,
    "Beacon Systems - Software Engineer",
    QA_SECOND_NARRATIVE,
    QA_SECOND_BULLET,
  ];
  let cursor = 0;
  const normalized = normalize(surface);
  for (const token of tokens) {
    const needle = normalize(token);
    const found = normalized.indexOf(needle, cursor);
    expect(found, `missing semantic token: ${token}`).toBeGreaterThanOrEqual(0);
    cursor = found + needle.length;
  }
  for (const token of uniqueOnce) {
    expect(countNormalized(surface, token)).toBe(1);
  }
  expect(countNormalized(surface, QA_WRAP_HEADING)).toBe(2);
  for (const fragment of QA_ORPHAN_FRAGMENTS) {
    expect(countNormalized(surface, fragment)).toBe(1);
  }
  for (const prefix of QA_TRUNCATED_PREFIXES) {
    expect(normalized).toContain(normalize(prefix));
  }
  expect(surface).not.toContain("PASSTHROUGH_SHOULD_NOT_EXPORT");
}

describe("Phase 4 QA cross-surface semantic equality", () => {
  beforeEach(() => {
    capture.pdfText.length = 0;
    capture.docxText.length = 0;
    vi.stubGlobal("URL", { createObjectURL: () => "blob:resume", revokeObjectURL: () => {} });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("joins wrapped role narrative and keeps bullets complete and ordered", () => {
    const resume = qaWrapResumeOriginalBullets();
    const copied = formatResumeText(resume);
    expect(copied).toContain(QA_WRAP_NARRATIVE);
    expect(copied).toContain(QA_WRAP_BULLETS[0]);
    expect(copied).toContain(QA_WRAP_BULLETS[1]);
    expect(copied).toContain(QA_WRAP_BULLETS[2]);
    expect(copied).not.toMatch(/Worked across backend and platform systems\nsupporting operational workflows\./);
    expect(copied.indexOf(QA_WRAP_NARRATIVE)).toBeLessThan(copied.indexOf(QA_WRAP_HEADING));
    expect(copied.indexOf(QA_WRAP_HEADING)).toBeLessThan(copied.indexOf(QA_WRAP_BULLETS[0]));
    expect(copied.indexOf(QA_WRAP_BULLETS[0])).toBeLessThan(copied.indexOf(QA_WRAP_BULLETS[1]));
    expect(copied.indexOf(QA_WRAP_BULLETS[1])).toBeLessThan(copied.indexOf(QA_WRAP_BULLETS[2]));
    expect(copied.indexOf(QA_WRAP_BULLETS[2])).toBeLessThan(copied.indexOf("Beacon Systems - Software Engineer"));
    expect(copied).toContain(QA_SECOND_NARRATIVE);
    expect(copied).toContain(QA_SECOND_BULLET);

    const work = buildResumeRenderModel(resume).find((section) => section.section === "work_history");
    expect(work?.roles).toHaveLength(2);
    expect(work?.roles?.[0]?.narrative).toBe(QA_WRAP_NARRATIVE);
    expect(work?.roles?.[0]?.responsibilityHeading).toBe(QA_WRAP_HEADING);
    expect(work?.roles?.[0]?.bullets.map((bullet) => bullet.text)).toEqual([...QA_WRAP_BULLETS]);
    expect(semanticWorkHistoryLines(resume)).toEqual([
      QA_WRAP_NARRATIVE,
      QA_WRAP_HEADING,
      ...QA_WRAP_BULLETS,
      QA_SECOND_NARRATIVE,
      QA_WRAP_HEADING,
      QA_SECOND_BULLET,
    ]);
    expect(buildReviewedWorkHistory(resume)[1]?.bullets.map((bullet) => bullet.text)).toEqual([QA_SECOND_BULLET]);
  });

  it("keeps preview, copy, PDF, and DOCX semantically equal", async () => {
    const resume = qaWrapResumeOriginalBullets();
    const copied = formatResumeText(resume);
    downloadResumePdf(resume, "qa-wrap-resume");
    await downloadResumeDocx(resume, "qa-wrap-resume");
    assertSemanticSurface(copied);
    assertSemanticSurface(capture.pdfText.join("\n"));
    assertSemanticSurface(capture.docxText.join("\n"));
  });
});
