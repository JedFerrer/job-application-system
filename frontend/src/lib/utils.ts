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
