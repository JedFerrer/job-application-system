// ── Supported Domains ──────────────────────────────────────────────────────
// Host permissions in manifest.json must match these patterns.
export const SUPPORTED_DOMAINS: Record<string, string> = {
  linkedin: "www.linkedin.com",
  indeed: "indeed.com",
  greenhouse: "greenhouse.io",
  lever: "jobs.lever.co",
  workday: "workdayjobs.com",
  // Generic career pages use URL heuristics, no domain mapping.
};

// ── Known ATS Domain Patterns ──────────────────────────────────────────────
export const ATS_DOMAIN_PATTERNS: Array<{ pattern: RegExp; type: string }> = [
  { pattern: /linkedin\.com\/jobs\//, type: "linkedin" },
  { pattern: /indeed\.com/, type: "indeed" },
  { pattern: /greenhouse\.io/, type: "greenhouse" },
  { pattern: /jobs\.lever\.co/, type: "lever" },
  { pattern: /workdayjobs\.com/, type: "workday" },
  { pattern: /myworkdayjobs\.com/, type: "workday" },
  { pattern: /ashbyhq\.com/, type: "ashby" },
  { pattern: /smartrecruiters\.com/, type: "smartrecruiters" },
  { pattern: /bamboohr\.com/, type: "bamboohr" },
  { pattern: /workable\.com/, type: "workable" },
  { pattern: /wellfound\.com/, type: "wellfound" },
  { pattern: /ziprecruiter\.com/, type: "ziprecruiter" },
];

// ── Job Page URL Heuristics ────────────────────────────────────────────────
export const JOB_URL_PATTERNS: RegExp[] = [
  /\/jobs?\//,
  /\/careers?\//,
  /\/positions?\//,
  /\/openings?\//,
  /\/job\//,
  /\/posting\//,
  /\/vacanc(y|ies)\//,
];

// ── Apply Button Text Patterns ─────────────────────────────────────────────
export const APPLY_BUTTON_PATTERNS: RegExp[] = [
  /\bapply\b/i,
  /\bapply now\b/i,
  /\bsubmit application\b/i,
  /\bcontinue application\b/i,
  /\bstart application\b/i,
  /\bbegin application\b/i,
  /\bcomplete application\b/i,
  /\bsubmit\b/i,
];

// ── Confirmation Message Patterns ──────────────────────────────────────────
export const CONFIRMATION_PATTERNS: RegExp[] = [
  /thank you for (applying|your application|your interest)/i,
  /application (submitted|received|complete)/i,
  /your application has been (received|submitted|sent)/i,
  /we (have )?received your application/i,
  /thank you for your interest/i,
  /application successfully submitted/i,
  /your (job )?application (was|has been) (submitted|received)/i,
  /you('ve| have) (successfully )?applied/i,
];

// ── Submit Button Text Patterns ────────────────────────────────────────────
export const SUBMIT_BUTTON_PATTERNS: RegExp[] = [
  /\bsubmit application\b/i,
  /\bsend application\b/i,
  /\bapply now\b/i,
  /\bcomplete application\b/i,
  /\bfinish\b/i,
  /\bsubmit\b/i,
];

// ── Autofill Field Tiers ───────────────────────────────────────────────────
// Confidence >= SAFE_THRESHOLD → fill automatically after user clicks Fill
export const SAFE_CONFIDENCE_THRESHOLD = 0.95;
// Confidence >= REVIEW_THRESHOLD → show in review list for user approval
export const REVIEW_CONFIDENCE_THRESHOLD = 0.70;

// Fields that are safe to fill (after user clicks Fill Safe Fields)
export const SAFE_FIELD_PATHS: Set<string> = new Set([
  "personal.first_name",
  "personal.last_name",
  "personal.email",
  "personal.phone",
  "personal.city",
  "personal.country",
  "links.linkedin",
  "links.github",
  "links.portfolio",
  "links.website",
  "work.current_title",
  "work.years_experience",
  "skills",
]);

// Fields that need review before filling
export const REVIEW_FIELD_PATHS: Set<string> = new Set([
  "work.expected_salary",
  "work.current_company",
  "authorization.requires_sponsorship",
  "authorization.authorized_to_work",
  "personal.address",
]);

// Fields/labels that must NEVER be autofilled
export const SENSITIVE_LABEL_PATTERNS: RegExp[] = [
  /race/i,
  /ethnicity/i,
  /gender/i,
  /sex(?!ual)/i,  // "sex" but not "sexual" (sexual orientation handled separately)
  /disability/i,
  /veteran/i,
  /health/i,
  /criminal/i,
  /religion/i,
  /religious/i,
  /political/i,
  /eeo/i,
  /equal opportunity/i,
  /background check/i,
  /drug test/i,
  /citizenship/i,
  /national origin/i,
  /sexual orientation/i,
  /gender identity/i,
  /marital status/i,
  /military/i,
];

// Fields that must NEVER be autofilled (by input type)
export const NEVER_FILL_INPUT_TYPES: Set<string> = new Set([
  "password",
  "file",
]);

// ── Event Types ────────────────────────────────────────────────────────────
export const EVENT_TYPES = {
  JOB_DETECTED: "job_detected",
  JOB_IMPORTED: "job_imported",
  APPLY_CLICKED: "apply_clicked",
  ATS_REDIRECT_DETECTED: "ats_redirect_detected",
  FORM_DETECTED: "form_detected",
  AUTOFILL_STARTED: "autofill_started",
  AUTOFILL_COMPLETED: "autofill_completed",
  SUBMIT_CLICKED: "submit_clicked",
  CONFIRMATION_DETECTED: "confirmation_detected",
  USER_MARKED_APPLIED: "user_marked_applied",
} as const;

// ── API Base URL ───────────────────────────────────────────────────────────
// Extension calls the backend directly (not through Next.js proxy).
// In development, point to localhost:8000.
export const API_BASE_URL = "http://localhost:8000/api/v1";

// ── Storage Keys ───────────────────────────────────────────────────────────
export const STORAGE_KEYS = {
  ACCESS_TOKEN: "access_token",
  REFRESH_TOKEN: "refresh_token",
  TOKEN_EXPIRES_AT: "token_expires_at",
  USER: "user",
  SESSION_ID: "session_id",
} as const;
