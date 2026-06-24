/**
 * Client-side export helpers for cover letters.
 *
 * Both functions accept the raw text content and an optional filename stem.
 * They run entirely in the browser — no server round-trip required.
 */

import { Document, Packer, Paragraph, TextRun } from "docx";
import jsPDF from "jspdf";

function buildFilename(stem: string, ext: string): string {
  const safe = stem.replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "_");
  return `${safe || "cover_letter"}.${ext}`;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Download the cover letter as a .docx Word document. */
export async function downloadDocx(content: string, stem = "cover_letter"): Promise<void> {
  const paragraphs = content
    .split(/\n\n+/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map(
      (text) =>
        new Paragraph({
          children: [new TextRun({ text, size: 24, font: "Calibri" })],
          spacing: { after: 200 },
        }),
    );

  const doc = new Document({
    sections: [{ children: paragraphs }],
  });

  const buffer = await Packer.toBlob(doc);
  triggerDownload(buffer, buildFilename(stem, "docx"));
}

/** Download the cover letter as a .pdf file. */
export function downloadPdf(content: string, stem = "cover_letter"): void {
  const pdf = new jsPDF({ unit: "mm", format: "a4" });

  const margin = 20;
  const pageWidth = pdf.internal.pageSize.getWidth();
  const pageHeight = pdf.internal.pageSize.getHeight();
  const usableWidth = pageWidth - margin * 2;

  pdf.setFont("helvetica");
  pdf.setFontSize(11);

  const lines = pdf.splitTextToSize(content, usableWidth) as string[];
  let y = margin;
  const lineHeight = 6;

  for (const line of lines) {
    if (y + lineHeight > pageHeight - margin) {
      pdf.addPage();
      y = margin;
    }
    pdf.text(line, margin, y);
    y += lineHeight;
  }

  pdf.save(buildFilename(stem, "pdf"));
}
