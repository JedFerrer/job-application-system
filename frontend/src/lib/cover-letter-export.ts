/**
 * Client-side export helpers for cover letters and tailored resumes.
 *
 * All functions run entirely in the browser — no server round-trip required.
 */

import { Document, Packer, Paragraph, TextRun } from "docx";
import jsPDF from "jspdf";
import type { TailoredResume } from "./types";

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

// ---- Resume export (professional ATS-friendly layout) -----------------------

const MARGIN = 15; // mm (~0.59 inches)
const PAGE_W = 215.9; // US Letter width in mm
const PAGE_H = 279.4; // US Letter height in mm
const FONT = "helvetica";
const FONT_BOLD = "helvetica";
const NAME_SIZE = 16;
const HEADING_SIZE = 11;
const BODY_SIZE = 10;
const LINE_H = 4.8; // body line height
const PARA_GAP = 2.5; // gap after paragraph/bullet
const SECTION_GAP = 5; // gap after section heading
const JOB_GAP = 3.5; // gap between jobs
const BULLET_INDENT = 6; // mm indent for bullets

function monthYear(d: string | null): string {
  if (!d) return "";
  const m = d.match(/^(\d{4})-(\d{2})/);
  if (!m) return d;
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${months[parseInt(m[2]!,10)-1] || m[2]} ${m[1]}`;
}

function dateRange(start: string | null, end: string | null): string {
  const s = monthYear(start);
  const e = end ? monthYear(end) : "Present";
  if (!s && !e) return "";
  return `${s} – ${e}`;
}

interface PdfCtx {
  pdf: jsPDF;
  y: number;
  pageH: number;
}

function newPage(ctx: PdfCtx): void {
  ctx.pdf.addPage();
  ctx.y = MARGIN;
}

function ensureSpace(ctx: PdfCtx, needed: number): void {
  if (ctx.y + needed > ctx.pageH - MARGIN) {
    newPage(ctx);
  }
}

function writeLine(ctx: PdfCtx, text: string, size: number, isBold: boolean, x?: number): void {
  const px = x ?? MARGIN;
  ctx.pdf.setFontSize(size);
  ctx.pdf.setFont(FONT, isBold ? "bold" : "normal");
  ctx.pdf.text(text, px, ctx.y);
  ctx.y += LINE_H;
}

function writeWrapped(ctx: PdfCtx, text: string, size: number, maxW: number, x?: number): number {
  const px = x ?? MARGIN;
  ctx.pdf.setFontSize(size);
  ctx.pdf.setFont(FONT, "normal");
  const lines = ctx.pdf.splitTextToSize(text, maxW);
  const blockH = lines.length * LINE_H;
  ensureSpace(ctx, blockH);
  ctx.pdf.text(lines, px, ctx.y);
  ctx.y += blockH;
  return lines.length;
}

function writeHrule(ctx: PdfCtx): void {
  ctx.pdf.setDrawColor(180, 180, 180);
  ctx.pdf.setLineWidth(0.2);
  ctx.pdf.line(MARGIN, ctx.y, PAGE_W - MARGIN, ctx.y);
  ctx.y += 3;
}

function writeSectionHeading(ctx: PdfCtx, title: string): void {
  ensureSpace(ctx, HEADING_SIZE + SECTION_GAP);
  ctx.y += 2;
  writeLine(ctx, title, HEADING_SIZE, true);
  writeHrule(ctx);
}

function writeBullet(ctx: PdfCtx, text: string): void {
  const bulletW = PAGE_W - MARGIN * 2 - BULLET_INDENT;
  ctx.pdf.setFontSize(BODY_SIZE);
  ctx.pdf.setFont(FONT, "normal");
  const lines = ctx.pdf.splitTextToSize(text, bulletW);
  const blockH = lines.length * LINE_H;
  ensureSpace(ctx, blockH + PARA_GAP);
  ctx.pdf.text("•", MARGIN, ctx.y);
  ctx.pdf.text(lines, MARGIN + BULLET_INDENT, ctx.y);
  ctx.y += blockH + PARA_GAP;
}

/** Professional resume PDF with ATS-friendly layout and smart page breaks. */
export function downloadResumePdf(data: TailoredResume, stem: string): void {
  const pdf = new jsPDF({ unit: "mm", format: "letter" });
  const ctx: PdfCtx = { pdf, y: MARGIN, pageH: PAGE_H };
  const textW = PAGE_W - MARGIN * 2;

  // Header — keep together
  ensureSpace(ctx, 30);
  if (data.full_name) {
    writeLine(ctx, data.full_name.toUpperCase(), NAME_SIZE, true);
  }
  if (data.headline) {
    ctx.y += 1;
    writeLine(ctx, data.headline, BODY_SIZE, false);
  }
  const contact = [data.email, data.phone].filter(Boolean).join(" | ");
  if (contact) {
    ctx.y += 1;
    writeLine(ctx, contact, BODY_SIZE, false);
  }
  ctx.y += 4;

  // Professional Summary
  if (data.professional_summary) {
    writeSectionHeading(ctx, "PROFESSIONAL SUMMARY");
    writeWrapped(ctx, data.professional_summary, BODY_SIZE, textW);
    ctx.y += SECTION_GAP;
  }

  // Technical Skills (categorized)
  const allSkills = [...data.matched_skills, ...data.other_skills];
  if (allSkills.length > 0) {
    writeSectionHeading(ctx, "TECHNICAL SKILLS");
    writeWrapped(ctx, allSkills.join(", "), BODY_SIZE, textW);
    ctx.y += SECTION_GAP;
  }

  // Professional Experience
  if (data.work_history.length > 0) {
    writeSectionHeading(ctx, "PROFESSIONAL EXPERIENCE");

    for (let i = 0; i < data.work_history.length; i++) {
      const role = data.work_history[i]!;
      const title = role.title || "Role";
      const company = role.company || "";
      const date = dateRange(role.start, role.end);
      const header = `${title} | ${company}${date ? ` | ${date}` : ""}`;

      // Calculate room needed: header + at least 2 bullets
      const bullets = role.tailored_highlights;
      const minBullets = Math.min(2, bullets.length);
      const textLines: number[] = [];
      for (let b = 0; b < bullets.length; b++) {
        const lines = pdf.splitTextToSize(bullets[b]!, textW - BULLET_INDENT).length;
        textLines.push(lines);
      }
      const minBlock = LINE_H + PARA_GAP + minBullets * (LINE_H + PARA_GAP);
      const fullBlock = LINE_H + PARA_GAP + bullets.length * (LINE_H + PARA_GAP);
      const remaining = ctx.pageH - MARGIN - ctx.y;

      // If not enough room for heading + 2 bullets, new page
      if (remaining < minBlock && ctx.y > MARGIN + 40) {
        newPage(ctx);
      } else {
        ctx.y += JOB_GAP;
      }

      // Write job header
      ensureSpace(ctx, LINE_H + 2);
      writeLine(ctx, header, BODY_SIZE, true);

      // Write bullets
      for (let b = 0; b < bullets.length; b++) {
        const bLines = textLines[b]!;
        // If only 1 bullet left and it would be orphaned, new page
        if (b === bullets.length - 1 && bLines === 1 && ctx.y + LINE_H + PARA_GAP > ctx.pageH - MARGIN && ctx.y > MARGIN + 20) {
          newPage(ctx);
        }
        ensureSpace(ctx, bLines * LINE_H + PARA_GAP);
        writeBullet(ctx, bullets[b]!);
      }
    }
    ctx.y += SECTION_GAP;
  }

  // Education
  if (data.education.length > 0) {
    writeSectionHeading(ctx, "EDUCATION");
    for (const edu of data.education) {
      const entry = [edu.degree, edu.institution, edu.year].filter(Boolean).join(" | ");
      if (entry) writeLine(ctx, entry, BODY_SIZE, false);
    }
    ctx.y += SECTION_GAP;
  }

  // Certifications
  if (data.certifications.length > 0) {
    writeSectionHeading(ctx, "CERTIFICATIONS");
    writeWrapped(ctx, data.certifications.join(" | "), BODY_SIZE, textW);
    ctx.y += SECTION_GAP;
  }

  pdf.save(buildFilename(stem, "pdf"));
}

/** Professional resume DOCX with ATS-friendly single-column layout. */
export async function downloadResumeDocx(data: TailoredResume, stem: string): Promise<void> {
  const children: Paragraph[] = [];
  const N = "Calibri";
  const SZ = 20; // half-points: 20 = 10pt
  const SZ_HEAD = 22; // 11pt
  const SZ_NAME = 32; // 16pt
  const NO_SPACE = { before: 0, after: 0, line: 276 }; // 1.15 line height in twips

  function p(text: string, opts: { bold?: boolean; size?: number; spaceAfter?: number } = {}) {
    children.push(
      new Paragraph({
        children: [new TextRun({ text, bold: opts.bold ?? false, size: opts.size ?? SZ, font: N })],
        spacing: { after: opts.spaceAfter ?? 0, line: 276 },
      }),
    );
  }

  // Name
  if (data.full_name) p(data.full_name.toUpperCase(), { bold: true, size: SZ_NAME, spaceAfter: 40 });
  // Headline
  if (data.headline) p(data.headline, { spaceAfter: 20 });
  // Contact
  const contact = [data.email, data.phone].filter(Boolean).join(" | ");
  if (contact) p(contact, { spaceAfter: 120 });

  // Section helper
  function section(title: string) {
    p(title, { bold: true, size: SZ_HEAD, spaceAfter: 60 });
  }

  // Professional Summary
  if (data.professional_summary) {
    section("PROFESSIONAL SUMMARY");
    p(data.professional_summary, { spaceAfter: 100 });
  }

  // Technical Skills
  const allSkills = [...data.matched_skills, ...data.other_skills];
  if (allSkills.length > 0) {
    section("TECHNICAL SKILLS");
    p(allSkills.join(", "), { spaceAfter: 100 });
  }

  // Professional Experience
  if (data.work_history.length > 0) {
    section("PROFESSIONAL EXPERIENCE");
    for (const role of data.work_history) {
      const title = role.title || "Role";
      const company = role.company || "";
      const date = dateRange(role.start, role.end);
      p(`${title} | ${company}${date ? ` | ${date}` : ""}`, { bold: true, spaceAfter: 40 });
      for (const h of role.tailored_highlights) {
        p(`  • ${h}`, { spaceAfter: 20 });
      }
      p("", { spaceAfter: 80 });
    }
  }

  // Education
  if (data.education.length > 0) {
    section("EDUCATION");
    for (const edu of data.education) {
      const entry = [edu.degree, edu.institution, edu.year].filter(Boolean).join(" | ");
      if (entry) p(entry, { spaceAfter: 20 });
    }
    p("", { spaceAfter: 80 });
  }

  // Certifications
  if (data.certifications.length > 0) {
    section("CERTIFICATIONS");
    p(data.certifications.join(" | "), { spaceAfter: 20 });
  }

  const doc = new Document({ sections: [{ children }] });
  const buffer = await Packer.toBlob(doc);
  triggerDownload(buffer, buildFilename(stem, "docx"));
}
