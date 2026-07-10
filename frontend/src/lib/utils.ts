import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Strip markdown formatting to produce a clean plain-text excerpt. */
export function stripMarkdown(md: string | null | undefined): string {
  if (!md) return "";
  let text = md;

  // Remove images entirely
  text = text.replace(/!\[.*?\]\(.*?\)/g, "");
  // Convert links to just the link text
  text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
  // Remove heading markers (##, ###, etc.)
  text = text.replace(/^#{1,6}\s+/gm, "");
  // Normalise list bullets before paired markers — otherwise * bullets get
  // consumed by the italic regex. Replace "* ", "- ", "+ " at line starts
  // with a plain bullet so the text still reads as a list.
  text = text.replace(/^[\t ]*[-*+]\s+/gm, "• ");
  // Remove bold/italic markers (must run after bullet normalisation)
  text = text.replace(/\*\*(.+?)\*\*/g, "$1");
  text = text.replace(/__(.+?)__/g, "$1");
  text = text.replace(/\*(.+?)\*/g, "$1");
  text = text.replace(/_(.+?)_/g, "$1");
  // Remove strikethrough
  text = text.replace(/~~(.+?)~~/g, "$1");
  // Remove inline code backticks
  text = text.replace(/`(.+?)`/g, "$1");
  // Remove blockquotes
  text = text.replace(/^>\s+/gm, "");
  // Remove horizontal rules
  text = text.replace(/^[-*_]{3,}\s*$/gm, "");
  // Unescape all backslash-escaped markdown punctuation (\\-, \\&, \\*, etc.)
  text = text.replace(/\\(.)/g, "$1");
  // Remove HTML tags
  text = text.replace(/<[^>]*>/g, "");
  // Collapse whitespace
  text = text.replace(/\n{2,}/g, " ");
  text = text.replace(/\s+/g, " ");

  return text.trim();
}

/**
 * Format a job description for the detail page — preserves paragraph
 * breaks, list structure, and basic formatting so it's readable.
 *
 * Handles both HTML descriptions (common from scrapers) and plain /
 * markdown-ish text.  Unlike stripMarkdown this does NOT collapse all
 * whitespace into a single line — it keeps paragraph separation.
 */
export function formatJobDescription(raw: string | null | undefined): string {
  if (!raw) return "";

  let text = raw;

  // ---- Decode common HTML entities ---------------------------------------
  text = text.replace(/&amp;/g, "&");
  text = text.replace(/&lt;/g, "<");
  text = text.replace(/&gt;/g, ">");
  text = text.replace(/&quot;/g, '"');
  text = text.replace(/&#39;/g, "'");
  text = text.replace(/&nbsp;/g, " ");
  text = text.replace(/&#x2F;/g, "/");

  // ---- Convert structural HTML to whitespace / bullets -------------------
  text = text.replace(/<br\s*\/?>/gi, "\n");
  text = text.replace(/<\/p>/gi, "\n\n");
  text = text.replace(/<p[^>]*>/gi, "");
  text = text.replace(/<\/div>/gi, "\n");
  text = text.replace(/<div[^>]*>/gi, "");
  text = text.replace(/<\/li>/gi, "\n");
  text = text.replace(/<li[^>]*>/gi, "• ");
  text = text.replace(/<\/ul>/gi, "\n");
  text = text.replace(/<ul[^>]*>/gi, "\n");
  text = text.replace(/<\/ol>/gi, "\n");
  text = text.replace(/<ol[^>]*>/gi, "\n");
  text = text.replace(/<hr\s*\/?>/gi, "\n");
  // Convert heading tags to standalone lines
  text = text.replace(/<\/h[1-6]>/gi, "\n");
  text = text.replace(/<h[1-6][^>]*>/gi, "\n");
  text = text.replace(/<\/?(?:strong|b|em|i|u|span|a)[^>]*>/gi, "");

  // ---- Strip remaining HTML tags -----------------------------------------
  text = text.replace(/<[^>]*>/g, "");

  // ---- Normalise markdown (same logic as stripMarkdown, but keep newlines)
  text = text.replace(/!\[.*?\]\(.*?\)/g, "");
  text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
  text = text.replace(/^#{1,6}\s+/gm, "");
  text = text.replace(/^[\t ]*[-*+]\s+/gm, "• ");
  text = text.replace(/\*\*(.+?)\*\*/g, "$1");
  text = text.replace(/__(.+?)__/g, "$1");
  text = text.replace(/\*(.+?)\*/g, "$1");
  text = text.replace(/_(.+?)_/g, "$1");
  text = text.replace(/`(.+?)`/g, "$1");
  text = text.replace(/^>\s+/gm, "");
  text = text.replace(/^[-*_]{3,}\s*$/gm, "");
  text = text.replace(/\\(.)/g, "$1");

  // ---- Normalise whitespace (preserve paragraph structure) ----------------
  // Collapse 2+ consecutive newlines down to 1 — keep paragraphs separated
  // but remove blank lines between them.
  text = text.replace(/\n{2,}/g, "\n");
  // Trim leading/trailing whitespace on each line (but keep the line breaks).
  text = text
    .split("\n")
    .map((line) => line.trim())
    .join("\n");
  // Remove leading/trailing blank lines.
  text = text.replace(/^\n+/, "").replace(/\n+$/, "");

  return text;
}
