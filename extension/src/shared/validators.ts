// ── URL Validators ─────────────────────────────────────────────────────────
import { JOB_URL_PATTERNS, SUPPORTED_DOMAINS } from "./constants";

/** Check if the current page URL looks like a job posting. */
export function isJobUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    const hostname = parsed.hostname.replace(/^www\./, "");

    // Check domain patterns first (faster, more specific)
    for (const domain of Object.values(SUPPORTED_DOMAINS)) {
      const domainNorm = domain.replace(/^www\./, "");
      if (hostname === domainNorm || hostname.endsWith("." + domainNorm)) return true;
    }

    // Fall back to URL path heuristics
    const pathname = parsed.pathname.toLowerCase();
    return JOB_URL_PATTERNS.some((pattern) => pattern.test(pathname));
  } catch {
    return false;
  }
}

/** Detect the ATS type from a URL. */
export function detectAtsType(url: string): string | undefined {
  try {
    const hostname = new URL(url).hostname.replace(/^www\./, "");
    if (hostname.includes("linkedin.com")) return "linkedin";
    if (hostname.includes("indeed.com")) return "indeed";
    if (hostname.includes("greenhouse.io")) return "greenhouse";
    if (hostname.includes("jobs.lever.co")) return "lever";
    if (hostname.includes("workdayjobs.com") || hostname.includes("myworkdayjobs.com"))
      return "workday";
    return "generic";
  } catch {
    return undefined;
  }
}

/** Generate a simple hash from raw HTML for dedup detection. */
export function hashHtml(html: string): string {
  // Simple djb2 hash — good enough for dedup comparison
  let hash = 5381;
  for (let i = 0; i < Math.min(html.length, 5000); i++) {
    hash = ((hash << 5) + hash + html.charCodeAt(i)) | 0;
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/** Normalize a company name (strip legal suffixes, trim whitespace). */
export function normalizeCompany(name: string): string {
  return name
    .replace(/\b(inc\.?|llc\.?|ltd\.?|corp\.?|corporation|limited|incorporated)\b/gi, "")
    .replace(/[,\s]+/g, " ")
    .trim();
}

/** Normalize a job title (capitalize first letters, trim). */
export function normalizeTitle(title: string): string {
  return title.replace(/\s+/g, " ").trim();
}
