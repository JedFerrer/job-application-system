// ── Job Extractor ──────────────────────────────────────────────────────────
// Normalizes raw job detection into an ExtractedJob with cleaned fields.

import type { ExtractedJob } from "../shared/types";
import { normalizeCompany, normalizeTitle } from "../shared/validators";
import type { RawJobDetection } from "./job-detector";

/**
 * Extract and normalize job fields from a raw detection.
 */
export function extractJob(detection: RawJobDetection): Partial<ExtractedJob> | null {
  if (!detection.title || !detection.company) return null;

  const title = normalizeTitle(detection.title);
  const company = normalizeCompany(detection.company);
  const location = detection.location?.trim();
  const salaryText = detection.salaryText?.trim();
  const description = detection.description?.trim() ?? "";

  // Try to find external apply URL
  const externalApplyUrl = findExternalApplyUrl();

  // Try to extract salary from description if not already found
  const salary = salaryText || extractSalaryFromText(description);

  return {
    title,
    company,
    location: location || undefined,
    description,
    salaryText: salary || undefined,
    externalApplyUrl: externalApplyUrl || undefined,
    sourceUrl: window.location.href,
    source: detection.atsType ?? "generic",
    atsType: detection.atsType as ExtractedJob["atsType"],
    rawHtmlHash: "",
    extractedAt: new Date().toISOString(),
  };
}

/**
 * Find an external "Apply" URL on the page (e.g., LinkedIn's external apply link).
 */
function findExternalApplyUrl(): string | null {
  // LinkedIn external apply
  const linkedInApply = document.querySelector<HTMLAnchorElement>(
    "a[data-tracking-control-name='public_jobs_apply-link']"
  );
  if (linkedInApply?.href) return linkedInApply.href;

  // Generic apply links
  const applyLinks = document.querySelectorAll<HTMLAnchorElement>("a");
  for (const link of applyLinks) {
    const text = link.textContent?.toLowerCase().trim() ?? "";
    if (/\bapply\b/i.test(text) && link.href && !link.href.startsWith("javascript:")) {
      // Exclude links that stay on the same domain (these are usually internal navigation)
      try {
        const linkHost = new URL(link.href).hostname;
        const pageHost = window.location.hostname;
        if (linkHost !== pageHost) return link.href;
      } catch {
        // Invalid URL — skip
      }
    }
  }

  return null;
}

/**
 * Try to extract salary information from text using regex.
 */
function extractSalaryFromText(text: string): string | null {
  const patterns = [
    /\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*[-–to]+\s*\$?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:k|K)?(?:\s*(?:per|a|\/)\s*(?:year|yr|annually|annual))?/i,
    /(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*[-–to]+\s*\$?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:k|K)/i,
    /\b(?:salary|compensation|pay)\s*(?:range|band)?:?\s*\$?(\d{1,3}(?:,\d{3})*)\s*[-–to]+\s*\$?(\d{1,3}(?:,\d{3})*)/i,
    /\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*[-–to]+\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:k|K)/i,
    /(\d{2,3})\s*[-–to]+\s*(\d{2,3})\s*(?:k|K)\s*(?:per|a|\/)\s*(?:year|yr|annually)/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1] && match[2]) {
      return `$${match[1]} - $${match[2]}`;
    }
  }

  return null;
}
