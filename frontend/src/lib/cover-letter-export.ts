/**
 * Client-side export helpers for cover letters and tailored resumes.
 *
 * All functions run entirely in the browser — no server round-trip required.
 */

import { Document, Packer, Paragraph, TextRun } from "docx";
import jsPDF from "jspdf";
import type { TailoredCanonicalSection, TailoredResume } from "./types";

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

function resumeSkills(data: TailoredResume): string[] {
  if (data.tailoring_snapshot_version === "full-context-v1" && data.skills) return data.skills;
  return data.canonical_sections?.find((section) => section.section === "skills")?.items.map((item) => item.text)
    ?? data.skills
    ?? [...data.matched_skills, ...data.other_skills];
}

function resumeLinks(data: TailoredResume): string[] {
  const canonical = data.canonical_sections?.find((section) => section.section === "links");
  if (canonical) return canonical.items.map((item) => item.text);
  return Object.entries(data.links ?? {})
    .filter((entry): entry is [string, string] => Boolean(entry[1]))
    .map(([label, url]) => `${label[0]!.toUpperCase()}${label.slice(1)}: ${url}`);
}

function joinSourceLines(values: string[]): string {
  return values.reduce((joined, value) => {
    if (!joined) return value.trim();
    const next = value.trim();
    return joined.endsWith("-") ? `${joined}${next}` : `${joined} ${next}`;
  }, "");
}

function canonicalSection(data: TailoredResume, section: TailoredCanonicalSection["section"]): TailoredCanonicalSection | null {
  return data.canonical_sections?.find((item) => item.section === section) ?? null;
}

function hasCanonicalSections(data: TailoredResume): boolean {
  return Boolean(data.canonical_sections?.length);
}

function canonicalValues(data: TailoredResume, section: TailoredCanonicalSection["section"]): string[] | null {
  const match = canonicalSection(data, section);
  if (!match) return null;
  const values = match.items.map((item) => item.text);
  return section === "summary" ? [joinSourceLines(values)] : values;
}

function resumeSummary(data: TailoredResume): string[] | null {
  if (data.tailoring_snapshot_version === "full-context-v1" && data.summary_evidence_ids?.length && data.professional_summary) {
    return [data.professional_summary];
  }
  return canonicalValues(data, "summary") ?? (data.professional_summary ? [data.professional_summary] : null);
}

function roleContext(role: TailoredResume["work_history"][number]): string | null {
  if (!role.role_context?.length) return null;
  return joinSourceLines(role.role_context);
}

function canonicalHeaderItems(data: TailoredResume): string[] | null {
  if (!hasCanonicalSections(data)) return null;
  const sections = data.canonical_sections!;
  const seen = new Set<string>();
  return sections
    .filter((section) => section.section === "contact" || section.section === "links")
    .flatMap((section) => section.items)
    .sort((a, b) => a.canonical_order - b.canonical_order)
    .filter((item) => {
      if (seen.has(item.source_id)) return false;
      seen.add(item.source_id);
      return true;
    })
    .map((item) => item.text);
}

export function buildResumeHeaderItems(data: TailoredResume): string[] {
  const canonical = canonicalHeaderItems(data);
  if (canonical) return canonical;
  return [data.full_name, data.headline, data.email, data.phone, ...resumeLinks(data)]
    .filter((item): item is string => Boolean(item));
}

export function resumeSectionHeading(section: TailoredCanonicalSection["section"], heading?: string | null): string {
  if (heading) return heading;
  return {
    contact: "CONTACT",
    links: "LINKS",
    summary: "PROFESSIONAL SUMMARY",
    skills: "TECHNICAL SKILLS",
    projects: "PROJECTS",
    education: "EDUCATION",
    certifications: "CERTIFICATIONS",
    licenses: "LICENSES",
    languages: "LANGUAGES",
    named: "ADDITIONAL INFORMATION",
    unclassified: "",
  }[section];
}

export interface ResumeRenderSection {
  section: TailoredCanonicalSection["section"] | "work_history" | null;
  heading: string | null;
  items: string[];
  sourceIds?: string[];
  order: number;
  roles?: TailoredResume["work_history"];
}

function sourceOrder(data: TailoredResume, sourceId?: string | null): number | null {
  if (!sourceId || !data.source_section_order?.length) return null;
  const index = data.source_section_order.indexOf(sourceId);
  return index >= 0 ? index : null;
}

function workHistoryOrder(data: TailoredResume, role: TailoredResume["work_history"][number], index: number): number {
  const roleOrder = sourceOrder(data, role.source_role_id);
  if (roleOrder != null) return roleOrder;
  const decisionOrders = (role.selection_decisions ?? []).map((decision) => decision.canonical_order);
  return decisionOrders.length ? Math.min(...decisionOrders) : Number.MAX_SAFE_INTEGER + index;
}

/** One ordered, empty-heading-free model shared by all resume exports. */
export function buildResumeRenderModel(data: TailoredResume): ResumeRenderSection[] {
  const canonical = (data.canonical_sections ?? [])
    .filter((section) => section.section !== "unclassified" && section.items.length > 0)
    .map((section) => ({
      section: section.section,
      heading: section.section === "unclassified" ? null : resumeSectionHeading(section.section, section.heading),
      items: section.section === "summary"
        ? resumeSummary(data) ?? [joinSourceLines(section.items.map((item) => item.text))]
        : section.section === "skills" && data.tailoring_snapshot_version === "full-context-v1"
          ? resumeSkills(data)
        : section.items.map((item) => item.text),
      sourceIds: section.items.map((item) => item.source_id),
      order: sourceOrder(data, section.items[0]?.source_id) ?? section.canonical_order ?? 0,
    }));
  const work = data.work_history.length
    ? [{ section: "work_history" as const, heading: "PROFESSIONAL EXPERIENCE", items: [], roles: data.work_history, order: Math.min(...data.work_history.map((role, index) => workHistoryOrder(data, role, index))) }]
    : [];
  if (canonical.length) return [...canonical, ...work].sort((a, b) => a.order - b.order);
  const fallback: ResumeRenderSection[] = [];
  const add = (section: TailoredCanonicalSection["section"], heading: string, items: string[]) => {
    const values = items.filter(Boolean);
    if (values.length) fallback.push({ section, heading, items: values, order: fallback.length });
  };
  add("contact", "CONTACT", [data.full_name ?? "", data.headline ?? "", data.email ?? "", data.phone ?? ""]);
  add("links", "LINKS", resumeLinks(data));
  add("summary", "PROFESSIONAL SUMMARY", data.professional_summary ? [data.professional_summary] : []);
  add("skills", "TECHNICAL SKILLS", resumeSkills(data));
  add("projects", "PROJECTS", data.projects?.flatMap((project) => [project.name, project.description, ...project.technologies, ...project.achievements, project.url].filter((item): item is string => Boolean(item))) ?? []);
  add("education", "EDUCATION", data.education.map((item) => [item.degree, item.institution, item.year].filter(Boolean).join(" | ")));
  add("certifications", "CERTIFICATIONS", data.certifications);
  add("licenses", "LICENSES", data.licenses?.map((item) => [item.name, item.issuer].filter(Boolean).join(" | ")) ?? []);
  add("languages", "LANGUAGES", data.languages?.map((item) => [item.language, item.proficiency].filter(Boolean).join(" | ")) ?? []);
  return [...fallback, ...work];
}

const DEDICATED_RESUME_SECTION_TYPES = new Set<TailoredCanonicalSection["section"]>([
  "contact", "links", "summary", "skills",
]);

function remainingCanonicalSections(data: TailoredResume): ResumeRenderSection[] {
  return buildResumeRenderModel(data).filter(
    (section) => section.section !== "work_history" && (section.section === null || !DEDICATED_RESUME_SECTION_TYPES.has(section.section)),
  );
}

export function formatResumeText(data: TailoredResume): string {
  const lines: string[] = [];
  const canonical = hasCanonicalSections(data);
  if (canonical) lines.push(...buildResumeHeaderItems(data), "");
  for (const section of buildResumeRenderModel(data)) {
    if (canonical && (section.section === "contact" || section.section === "links")) continue;
    if (section.heading) lines.push(section.heading);
    if (section.section === "work_history") {
      for (const role of section.roles ?? []) {
        lines.push(role.source_header || [role.title, role.company, dateRange(role.start, role.end)].filter(Boolean).join(" | ") || "Role");
        const context = roleContext(role);
        if (context) lines.push(context);
        if (role.responsibilities_heading) lines.push(role.responsibilities_heading);
        lines.push(...role.tailored_highlights.map((item) => `  • ${item}`), "");
      }
      continue;
    }
    lines.push(...section.items, "");
  }
  return lines.join("\n").trim();
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

  // Header — keep together and preserve canonical source order.
  ensureSpace(ctx, 30);
  const headerItems = buildResumeHeaderItems(data);
  const normalizedName = data.full_name?.trim().toLocaleLowerCase();
  for (const [index, value] of headerItems.entries()) {
    const isName = Boolean(normalizedName && value.trim().toLocaleLowerCase() === normalizedName);
    writeLine(ctx, value, isName ? NAME_SIZE : BODY_SIZE, isName);
    if (index === 0 && isName) ctx.y += 1;
  }
  ctx.y += 4;

  // Source-backed resumes use the same ordered model as copied text. Legacy
  // resumes retain the established template fallback below.
  if (hasCanonicalSections(data)) {
    for (const rendered of buildResumeRenderModel(data)) {
      if (rendered.section === "contact" || rendered.section === "links") continue;
      if (rendered.section === "work_history") {
        if (!rendered.roles?.length) continue;
        writeSectionHeading(ctx, rendered.heading || "PROFESSIONAL EXPERIENCE");
        for (const role of rendered.roles) {
          ensureSpace(ctx, LINE_H + 2);
          const title = role.title || "Role";
          const company = role.company || "";
          const date = dateRange(role.start, role.end);
          writeLine(ctx, role.source_header || `${title} | ${company}${date ? ` | ${date}` : ""}`, BODY_SIZE, true);
          const context = roleContext(role);
          if (context) writeWrapped(ctx, context, BODY_SIZE, textW);
          if (role.responsibilities_heading) writeLine(ctx, role.responsibilities_heading, BODY_SIZE, true);
          for (const bullet of role.tailored_highlights) writeBullet(ctx, bullet);
          ctx.y += JOB_GAP;
        }
        continue;
      }
      if (!rendered.items.length) continue;
      writeSectionHeading(ctx, rendered.heading || "");
      const values = rendered.section === "skills" ? [rendered.items.join(", ")] : rendered.items;
      for (const value of values) writeWrapped(ctx, value, BODY_SIZE, textW);
      ctx.y += SECTION_GAP;
    }
    pdf.save(buildFilename(stem, "pdf"));
    return;
  }

  // Professional Summary
  const canonicalSummary = resumeSummary(data);
  if (canonicalSummary?.length || data.professional_summary) {
    writeSectionHeading(ctx, canonicalSection(data, "summary")?.heading || "PROFESSIONAL SUMMARY");
    for (const summary of canonicalSummary ?? [data.professional_summary!]) writeWrapped(ctx, summary, BODY_SIZE, textW);
    ctx.y += SECTION_GAP;
  }

  // Technical Skills (categorized)
  const allSkills = resumeSkills(data);
  if (allSkills.length > 0) {
    writeSectionHeading(ctx, canonicalSection(data, "skills")?.heading || "TECHNICAL SKILLS");
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
      const header = role.source_header || `${title} | ${company}${date ? ` | ${date}` : ""}`;

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
      const context = roleContext(role);
      if (context) writeWrapped(ctx, context, BODY_SIZE, textW);
      if (role.responsibilities_heading) writeLine(ctx, role.responsibilities_heading, BODY_SIZE, true);

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

  const canonicalSections = Boolean(data.canonical_sections?.length);
  if (canonicalSections) {
    for (const section of remainingCanonicalSections(data)) {
      if (section.heading) writeSectionHeading(ctx, section.heading);
      for (const value of section.items) writeWrapped(ctx, value, BODY_SIZE, textW);
    }
  }
  if (!canonicalSections && data.projects?.length) {
    writeSectionHeading(ctx, "PROJECTS");
    for (const project of data.projects) {
      if (project.name) writeLine(ctx, project.name, BODY_SIZE, true);
      if (project.description) writeWrapped(ctx, project.description, BODY_SIZE, textW);
      if (project.technologies.length) writeWrapped(ctx, `Technologies: ${project.technologies.join(", ")}`, BODY_SIZE, textW);
      for (const achievement of project.achievements) writeBullet(ctx, achievement);
      if (project.url) writeWrapped(ctx, `URL: ${project.url}`, BODY_SIZE, textW);
    }
    ctx.y += SECTION_GAP;
  }

  // Education
  if (!canonicalSections && data.education.length > 0) {
    writeSectionHeading(ctx, "EDUCATION");
    for (const edu of data.education) {
      const entry = [edu.degree, edu.institution, edu.year].filter(Boolean).join(" | ");
      if (entry) writeLine(ctx, entry, BODY_SIZE, false);
    }
    ctx.y += SECTION_GAP;
  }

  // Certifications
  if (!canonicalSections && data.certifications.length > 0) {
    writeSectionHeading(ctx, "CERTIFICATIONS");
    writeWrapped(ctx, data.certifications.join(" | "), BODY_SIZE, textW);
    ctx.y += SECTION_GAP;
  }

  if (!canonicalSections && data.licenses?.length) {
    writeSectionHeading(ctx, "LICENSES & CREDENTIALS");
    for (const item of data.licenses) writeLine(ctx, [item.name, item.issuer].filter(Boolean).join(" | "), BODY_SIZE, false);
    ctx.y += SECTION_GAP;
  }
  if (!canonicalSections && data.languages?.length) {
    writeSectionHeading(ctx, "LANGUAGES");
    for (const item of data.languages) writeLine(ctx, [item.language, item.proficiency].filter(Boolean).join(" | "), BODY_SIZE, false);
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

  // Header — preserve canonical source order and avoid normalized duplicates.
  const headerItems = buildResumeHeaderItems(data);
  const normalizedName = data.full_name?.trim().toLocaleLowerCase();
  for (const [index, value] of headerItems.entries()) {
    const isName = Boolean(normalizedName && value.trim().toLocaleLowerCase() === normalizedName);
    p(value, isName ? { bold: true, size: SZ_NAME, spaceAfter: 40 } : { spaceAfter: index === headerItems.length - 1 ? 120 : 0 });
  }

  // Section helper
  function section(title: string) {
    p(title, { bold: true, size: SZ_HEAD, spaceAfter: 60 });
  }

  // Source-backed resumes use the same ordered model as copied text. Legacy
  // resumes retain the established template fallback below.
  if (hasCanonicalSections(data)) {
    for (const rendered of buildResumeRenderModel(data)) {
      if (rendered.section === "contact" || rendered.section === "links") continue;
      if (rendered.section === "work_history") {
        if (!rendered.roles?.length) continue;
        section(rendered.heading || "PROFESSIONAL EXPERIENCE");
        for (const role of rendered.roles) {
          const title = role.title || "Role";
          const company = role.company || "";
          const date = dateRange(role.start, role.end);
          p(role.source_header || `${title} | ${company}${date ? ` | ${date}` : ""}`, { bold: true, spaceAfter: 40 });
          const context = roleContext(role);
          if (context) p(context, { spaceAfter: 20 });
          if (role.responsibilities_heading) p(role.responsibilities_heading, { bold: true, spaceAfter: 20 });
          for (const bullet of role.tailored_highlights) p(`  • ${bullet}`, { spaceAfter: 20 });
          p("", { spaceAfter: 80 });
        }
        continue;
      }
      if (!rendered.items.length) continue;
      section(rendered.heading || "");
      const values = rendered.section === "skills" ? [rendered.items.join(", ")] : rendered.items;
      for (const value of values) p(value);
    }
    const doc = new Document({ sections: [{ children }] });
    const buffer = await Packer.toBlob(doc);
    triggerDownload(buffer, buildFilename(stem, "docx"));
    return;
  }

  // Professional Summary
  const canonicalSummary = resumeSummary(data);
  if (canonicalSummary?.length || data.professional_summary) {
    section(canonicalSection(data, "summary")?.heading || "PROFESSIONAL SUMMARY");
    for (const value of canonicalSummary ?? [data.professional_summary!]) p(value, { spaceAfter: 100 });
  }

  // Technical Skills
  const allSkills = resumeSkills(data);
  if (allSkills.length > 0) {
    section(canonicalSection(data, "skills")?.heading || "TECHNICAL SKILLS");
    p(allSkills.join(", "), { spaceAfter: 100 });
  }

  // Professional Experience
  if (data.work_history.length > 0) {
    section("PROFESSIONAL EXPERIENCE");
    for (const role of data.work_history) {
      const title = role.title || "Role";
      const company = role.company || "";
      const date = dateRange(role.start, role.end);
      p(role.source_header || `${title} | ${company}${date ? ` | ${date}` : ""}`, { bold: true, spaceAfter: 40 });
      const context = roleContext(role);
      if (context) p(context, { spaceAfter: 20 });
      if (role.responsibilities_heading) p(role.responsibilities_heading, { bold: true, spaceAfter: 20 });
      for (const h of role.tailored_highlights) {
        p(`  • ${h}`, { spaceAfter: 20 });
      }
      p("", { spaceAfter: 80 });
    }
  }

  const canonicalSections = Boolean(data.canonical_sections?.length);
  if (canonicalSections) {
    for (const rendered of remainingCanonicalSections(data)) {
      if (rendered.heading) section(rendered.heading);
      for (const value of rendered.items) p(value);
    }
  }
  if (!canonicalSections && data.projects?.length) {
    section("PROJECTS");
    for (const project of data.projects) {
      if (project.name) p(project.name, { bold: true });
      if (project.description) p(project.description);
      if (project.technologies.length) p(`Technologies: ${project.technologies.join(", ")}`);
      for (const achievement of project.achievements) p(`  • ${achievement}`);
      if (project.url) p(`URL: ${project.url}`);
      p("", { spaceAfter: 40 });
    }
  }

  // Education
  if (!canonicalSections && data.education.length > 0) {
    section("EDUCATION");
    for (const edu of data.education) {
      const entry = [edu.degree, edu.institution, edu.year].filter(Boolean).join(" | ");
      if (entry) p(entry, { spaceAfter: 20 });
    }
    p("", { spaceAfter: 80 });
  }

  // Certifications
  if (!canonicalSections && data.certifications.length > 0) {
    section("CERTIFICATIONS");
    p(data.certifications.join(" | "), { spaceAfter: 20 });
  }

  if (!canonicalSections && data.licenses?.length) {
    section("LICENSES & CREDENTIALS");
    for (const item of data.licenses) p([item.name, item.issuer].filter(Boolean).join(" | "));
  }
  if (!canonicalSections && data.languages?.length) {
    section("LANGUAGES");
    for (const item of data.languages) p([item.language, item.proficiency].filter(Boolean).join(" | "));
  }

  const doc = new Document({ sections: [{ children }] });
  const buffer = await Packer.toBlob(doc);
  triggerDownload(buffer, buildFilename(stem, "docx"));
}
