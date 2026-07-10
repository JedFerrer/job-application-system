// ── Job Detector ───────────────────────────────────────────────────────────
// Four-layer detection strategy:
//   1. Known domain adapters (LinkedIn, Indeed, Greenhouse, Lever, Workday)
//   2. JSON-LD structured data (<script type="application/ld+json">)
//   3. DOM heuristics (title/company/location/description patterns)
//   4. URL heuristics (already filtered by content script init)

import type { ExtractedJob } from "../shared/types";
import { detectAtsType } from "../shared/validators";
import { getTextContent } from "./dom-utils";

/** Raw detected job before normalization. */
export interface RawJobDetection {
  title: string;
  company: string;
  location?: string;
  description: string;
  salaryText?: string;
  atsType?: string;
}

/**
 * Detect a job posting on the current page.
 * Returns a raw job detection or null if no job found.
 */
export function detectJob(): RawJobDetection | null {
  const atsType = detectAtsType(window.location.href);

  // Layer 1: Known domain adapters
  if (atsType === "linkedin") return detectLinkedInJob();
  if (atsType === "indeed") return detectIndeedJob();
  if (atsType === "greenhouse") return detectGreenhouseJob();
  if (atsType === "lever") return detectLeverJob();
  if (atsType === "workday") return detectWorkdayJob();

  // Layer 2: JSON-LD structured data
  const jsonLdJob = detectJsonLd();
  if (jsonLdJob) return jsonLdJob;

  // Layer 3: DOM heuristics
  const domJob = detectByHeuristics();
  if (domJob) return domJob;

  return null;
}

// ── Layer 1: Known Domain Adapters ────────────────────────────────────────

function detectLinkedInJob(): RawJobDetection | null {
  let title: string | null = null;
  let company: string | null = null;
  let location: string | null = null;
  let description: string | null = null;

  // Strategy 1: Try document.title — LinkedIn formats as "Title - Company | LinkedIn"
  const docTitle = document.title || "";
  const linkedInMatch = docTitle.match(/^(.+?)\s*[-–|]\s*(.+?)\s*[-–|]\s*LinkedIn/i);
  if (linkedInMatch) {
    title = linkedInMatch[1]?.trim() ?? null;
    company = linkedInMatch[2]?.trim() ?? null;
  }

  // Strategy 2: Try any h1 (LinkedIn usually wraps the job title in an h1)
  if (!title) {
    const h1s = document.querySelectorAll("h1");
    for (const h1 of h1s) {
      const text = (h1.textContent ?? "").trim();
      if (text.length > 3 && text.length < 200) {
        title = text;
        break;
      }
    }
  }

  // Strategy 3: Try finding company from any link or span near the title
  if (!company) {
    // Companies are often in a link or span near the top
    const maybeCompany =
      document.querySelector("a[href*='/company/']") ??
      document.querySelector("[data-tracking-control-name*='company']") ??
      document.querySelector("a[href*='linkedin.com/company/']");
    if (maybeCompany) {
      company = (maybeCompany.textContent ?? "").trim();
    }
  }

  // Strategy 4: Try page metadata
  if (!company) {
    const metaCompany = document.querySelector("meta[property='og:description']");
    if (metaCompany) {
      const content = metaCompany.getAttribute("content") ?? "";
      const parts = content.split("·").map(s => s.trim());
      if (parts.length >= 2) company = parts[0] ?? null;
    }
  }

  // Location
  const maybeLocation =
    document.querySelector("[class*='bullet']") ??
    document.querySelector("span[class*='location']");
  if (maybeLocation) {
    location = (maybeLocation.textContent ?? "").trim();
  }

  // Description
  const descEl =
    document.querySelector("#job-details") ??
    document.querySelector("article") ??
    document.querySelector("[class*='description']");
  if (descEl) {
    description = (descEl.textContent ?? "").trim();
  }

  if (!title || !company) return null;

  return {
    title,
    company,
    location: location ?? undefined,
    description: description ?? "",
    atsType: "linkedin",
  };
}

function detectIndeedJob(): RawJobDetection | null {
  const titleEl = document.querySelector(
    "h1.jobsearch-JobInfoHeader-title, h1.icl-u-xs-mb--xs, h1[data-testid='jobsearch-JobInfoHeader-title']"
  );
  const companyEl = document.querySelector(
    "[data-company-name], div[data-testid='inlineHeader-companyName'], span.css-1saizt3"
  );
  const locationEl = document.querySelector(
    "[data-location], div[data-testid='inlineHeader-companyLocation']"
  );
  const descEl = document.querySelector("#jobDescriptionText, div.jobsearch-jobDescriptionText");

  if (!titleEl || !companyEl) return null;

  return {
    title: getTextContent(titleEl),
    company: getTextContent(companyEl),
    location: locationEl ? getTextContent(locationEl) : undefined,
    description: descEl ? getTextContent(descEl) : "",
    atsType: "indeed",
  };
}

function detectGreenhouseJob(): RawJobDetection | null {
  const titleEl = document.querySelector("h1.app-title, h1.job-title, h1");
  const companyEl = document.querySelector(".company-name, .app-company");
  const locationEl = document.querySelector(".location, .app-location");
  const descEl = document.querySelector("#content, .job-description, #job_description");

  if (!titleEl || !companyEl) return null;

  return {
    title: getTextContent(titleEl),
    company: getTextContent(companyEl),
    location: locationEl ? getTextContent(locationEl) : undefined,
    description: descEl ? getTextContent(descEl) : "",
    atsType: "greenhouse",
  };
}

function detectLeverJob(): RawJobDetection | null {
  const titleEl = document.querySelector(".posting-headline h2, h2.posting-title, h2");
  const companyEl = document.querySelector(".posting-categories, .posting-categories a");
  const locationEl = document.querySelector(".location, .posting-categories span");
  const descEl = document.querySelector(
    ".section-wrapper, .posting-page-content, .posting-description"
  );

  // Lever pages show company name in the URL subdomain (e.g., company.lever.co)
  const companyFromDomain = window.location.hostname.split(".")[0];

  if (!titleEl) return null;

  return {
    title: getTextContent(titleEl),
    company: companyEl ? getTextContent(companyEl) : (companyFromDomain ?? "Unknown"),
    location: locationEl ? getTextContent(locationEl) : undefined,
    description: descEl ? getTextContent(descEl) : "",
    atsType: "lever",
  };
}

// ── Workday Adapter ─────────────────────────────────────────────────────

function detectWorkdayJob(): RawJobDetection | null {
  // Workday uses a highly dynamic SPA. Strategies:
  // 1. Check for job title in the main heading
  // 2. Try to extract company from URL subdomain or page header
  // 3. Look for job description in common content containers
  // 4. Check inside iframes (Workday often embeds content)

  // Try main page first
  const titleSelectors = [
    'h1[data-automation-id="jobPostingHeader"]',
    "h1[data-automation-id='jobPostingTitle']",
    "h1.css-1cvo3bf", // Common Workday heading class
    "[data-automation-id='jobPostingTitle']",
    'h1[class*="title"]',
    "h1",
  ];

  let title = "";
  for (const sel of titleSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      title = getTextContent(el);
      if (title && title.length > 2) break;
    }
  }

  // If no title found on main page, try inside iframes
  if (!title) {
    const iframes = document.querySelectorAll("iframe");
    for (const iframe of iframes) {
      try {
        const doc = iframe.contentDocument || iframe.contentWindow?.document;
        if (!doc) continue;
        const h1 = doc.querySelector("h1");
        if (h1) {
          title = getTextContent(h1);
          if (title) break;
        }
      } catch {
        // Cross-origin iframe — can't access
      }
    }
  }

  if (!title) return null;

  // Company detection
  const companySelectors = [
    '[data-automation-id="jobPostingCompanyName"]',
    '[data-automation-id="companyName"]',
    ".css-1saizt3", // Generic Workday company class
    '[class*="company"]',
  ];

  let company = "";
  for (const sel of companySelectors) {
    const el = document.querySelector(sel);
    if (el) {
      company = getTextContent(el);
      if (company) break;
    }
  }

  // Fallback: extract company from subdomain (e.g., company.wd5.myworkdayjobs.com)
  if (!company) {
    const hostname = window.location.hostname;
    const parts = hostname.split(".");
    if (parts.length >= 3 && parts[1]?.includes("wd")) {
      company = parts[0] ?? "";
      // Capitalize first letter
      company = company.charAt(0).toUpperCase() + company.slice(1);
    }
  }

  // Location
  const locationSelectors = [
    '[data-automation-id="jobPostingLocation"]',
    '[data-automation-id="location"]',
    ".css-129qk5j", // Common Workday location class
  ];
  let location: string | undefined;
  for (const sel of locationSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      location = getTextContent(el);
      if (location) break;
    }
  }

  // Description
  const descSelectors = [
    '[data-automation-id="jobPostingDescription"]',
    "#jobPostingDescription",
    '[class*="description"]',
    ".css-1o8q4lo", // Workday description container
  ];
  let description = "";
  for (const sel of descSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      description = getTextContent(el);
      if (description.length > 100) break;
    }
  }

  // Fallback: grab the main content area
  if (!description) {
    const main = document.querySelector("main, [role='main'], .css-navg0q");
    if (main) description = getTextContent(main);
  }

  return {
    title,
    company: company || "Unknown",
    location,
    description,
    atsType: "workday",
  };
}

// ── Layer 2: JSON-LD Structured Data ──────────────────────────────────────

function detectJsonLd(): RawJobDetection | null {
  const scripts = document.querySelectorAll<HTMLScriptElement>(
    'script[type="application/ld+json"]'
  );
  for (const script of scripts) {
    try {
      const data = JSON.parse(script.textContent ?? "");
      // Handle @graph (multiple entities)
      const items = data["@graph"] ?? [data];
      for (const item of Array.isArray(items) ? items : [items]) {
        if (
          item["@type"] === "JobPosting" ||
          (typeof item["@type"] === "string" && item["@type"].includes("JobPosting"))
        ) {
          return {
            title: item.title ?? "",
            company:
              item.hiringOrganization?.name ??
              item.employer?.name ??
              "",
            location:
              item.jobLocation?.address?.addressLocality ??
              item.jobLocation?.name ??
              item.applicantLocationRequirements?.name ??
              undefined,
            description: item.description ?? "",
            salaryText: item.baseSalary?.value
              ? `${item.baseSalary.value} ${item.baseSalary.currency ?? ""}`
              : undefined,
            atsType: "generic",
          };
        }
      }
    } catch {
      // Malformed JSON-LD — skip
    }
  }
  return null;
}

// ── Layer 3: DOM Heuristics ───────────────────────────────────────────────

function detectByHeuristics(): RawJobDetection | null {
  // Look for common job page patterns
  const titleSelectors = [
    "h1[class*='job']", "h1[class*='title']", "h1[class*='position']",
    "h2[class*='job']", "h2[class*='title']",
    "[data-testid='job-title']", "[data-automation='job-title']",
  ];

  let title = "";
  for (const sel of titleSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      title = getTextContent(el);
      if (title) break;
    }
  }
  if (!title) {
    // Fallback: first h1 on the page
    const h1 = document.querySelector("h1");
    if (h1) title = getTextContent(h1);
  }

  // Company detection
  const companySelectors = [
    "[class*='company']", "[class*='employer']", "[class*='organization']",
    "[data-testid='company-name']",
  ];
  let company = "";
  for (const sel of companySelectors) {
    const el = document.querySelector(sel);
    if (el) {
      company = getTextContent(el);
      if (company) break;
    }
  }

  // Description
  const descSelectors = [
    "[class*='job-description']", "[class*='description']",
    "[data-testid='job-description']", "#job-description",
  ];
  let description = "";
  for (const sel of descSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      description = getTextContent(el);
      if (description.length > 100) break;
    }
  }
  if (!description) {
    // Fallback: largest text block on page
    description = document.body.innerText.slice(0, 15000);
  }

  if (title && company) {
    return { title, company, description, atsType: "generic" };
  }

  return null;
}
