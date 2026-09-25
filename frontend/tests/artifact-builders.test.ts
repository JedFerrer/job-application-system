import { afterEach, describe, expect, it, vi } from "vitest";
import { qaWrapResume } from "./fixtures/tailored-resume-qa";
import { buildCoverLetterDocx, buildCoverLetterPdf, buildResumeDocx, buildResumePdf, downloadDocx, downloadPdf, downloadResumeDocx, downloadResumePdf } from "@/lib/cover-letter-export";

afterEach(() => vi.unstubAllGlobals());

describe("real browser artifact builders", () => {
  it("returns four nonempty signed files with stable metadata and no download", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click");
    const resume = qaWrapResume();
    const built = [
      await buildResumePdf(resume, "Jordan_Resume"),
      await buildResumeDocx(resume, "Jordan_Resume"),
      await buildCoverLetterPdf("Dear Hiring Team,\n\nI can help.", "cover_letter_Role"),
      await buildCoverLetterDocx("Dear Hiring Team,\n\nI can help.", "cover_letter_Role"),
    ];
    expect(built.map((file) => file.exporterVersion)).toEqual(["resume-pdf-v2", "resume-docx-v1", "cover-letter-pdf-v2", "cover-letter-docx-v1"]);
    expect(built.map((file) => file.format)).toEqual(["pdf", "docx", "pdf", "docx"]);
    expect(built.map((file) => file.contentType)).toEqual([
      "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]);
    for (const file of built) {
      expect(file.blob.size).toBeGreaterThan(20);
      expect(file.filename).toMatch(/\.(pdf|docx)$/);
      expect(file.blob.type).toBe(file.contentType);
      const signature = Array.from(new Uint8Array(await file.blob.slice(0, 5).arrayBuffer()));
      if (file.format === "pdf") expect(signature).toEqual([37, 80, 68, 70, 45]);
      else expect(signature.slice(0, 2)).toEqual([80, 75]);
    }
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("retains local downloads for all four existing editor wrappers", async () => {
    const blobs: Blob[] = [];
    const names: string[] = [];
    vi.stubGlobal("URL", {
      createObjectURL: (blob: Blob) => { blobs.push(blob); return `blob:editor-${blobs.length}`; },
      revokeObjectURL: vi.fn(),
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    const resume = qaWrapResume();
    await downloadResumePdf(resume, "Jordan_Resume");
    await downloadResumeDocx(resume, "Jordan_Resume");
    await downloadPdf("Dear Hiring Team", "cover_letter_Role");
    await downloadDocx("Dear Hiring Team", "cover_letter_Role");
    expect(names).toEqual(["Jordan_Resume.pdf", "Jordan_Resume.docx", "cover_letter_Role.pdf", "cover_letter_Role.docx"]);
    expect(blobs).toHaveLength(4);
    expect(blobs.every((blob) => blob.size > 20)).toBe(true);
    click.mockRestore();
  });
});
