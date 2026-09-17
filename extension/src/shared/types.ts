// ── Extension Message Types ────────────────────────────────────────────────
// Typed message envelope for content-script ↔ service-worker ↔ popup
// communication. Every message carries a `type` discriminant and an optional
// `payload`. Receivers switch on `type` for type-narrowed handling.

// ── Extracted Job (normalized across all sites) ────────────────────────────
export interface ExtractedJob {
  title: string;
  company: string;
  location?: string;
  description: string;
  salaryText?: string;
  sourceUrl: string;
  source: string;
  atsType?: "linkedin" | "indeed" | "greenhouse" | "lever" | "workday" | "generic";
  externalApplyUrl?: string;
  rawHtmlHash: string;
  extractedAt: string;
}

// ── Detected Form Field ────────────────────────────────────────────────────
export interface DetectedField {
  elementId: string;
  tagName: string;
  inputType?: string;
  name?: string;
  id?: string;
  label?: string;
  placeholder?: string;
  ariaLabel?: string;
  autocomplete?: string;
  options?: string[];
  isRequired?: boolean;
  visible: boolean;
  // Serialized selector path for reliable re-location
  selectorPath: string;
}

// ── Field Mapping Result ───────────────────────────────────────────────────
export interface FieldMapping {
  elementId: string;
  profilePath: string | null;
  confidence: number;
  action: "fill" | "review" | "skip";
  value?: string;
  reasonSkipped?: string;
  /** CSS selector path for reliable DOM re-location during fill. */
  selectorPath?: string;
}

export interface FillExecutionResult {
  attempted: number;
  filled: number;
  skipped: number;
  failed: number;
}

export interface AttemptCandidate {
  attemptId: string;
  applicationId: number;
  role: string;
  company?: string;
  expiresAt: string;
  progress: string;
}

export interface AttemptBinding {
  attemptId: string;
  applicationId: number;
  expiresAt: string;
}

export interface TabAttemptContext {
  attemptId: string;
  applicationId: number;
  jobId: number;
  expiresAt: string;
  host: string;
  bindingState?: "bound" | "reconfirmation_required";
  boundPageUrl?: string;
}

// ── Autofill Profile (subset of backend profile) ───────────────────────────
export interface AutofillProfile {
  personal: {
    first_name: string | null;
    last_name: string | null;
    email: string | null;
    phone: string | null;
    city: string | null;
    country: string | null;
  };
  links: {
    linkedin: string | null;
    github: string | null;
    portfolio: string | null;
    website: string | null;
  };
  work: {
    current_title: string | null;
    current_company: string | null;
    years_experience: number | null;
    expected_salary: string | null;
  };
  authorization: {
    requires_sponsorship: boolean | null;
    authorized_to_work: boolean | null;
  };
  education: Array<{
    school: string | null;
    degree: string | null;
    field_of_study: string | null;
    graduation_year: number | null;
  }>;
  experience: Array<{
    company: string | null;
    title: string | null;
    start_date: string | null;
    end_date: string | null;
    description: string | null;
  }>;
  skills: string[];
}

// ── Application Event (from backend) ───────────────────────────────────────
export interface ApplicationEvent {
  id: string;
  event_type: string;
  source_url: string | null;
  page_url: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

// ── Extension Session State ────────────────────────────────────────────────
export interface ExtensionSession {
  accessToken: string;
  expiresAt: number;
  user: {
    id: number;
    email: string;
    name: string;
  };
}

// ── API Response Types ─────────────────────────────────────────────────────
export interface JobImportResponse {
  job_id: number;
  application_id: string | null;
  status: string;
  was_existing_job: boolean;
  was_existing_application: boolean;
  match_status: string;
}

export interface ApplicationEventResponse {
  application_id: string;
  job_id: number;
  status: string;
  event_recorded: boolean;
}

// ── Message Envelope ───────────────────────────────────────────────────────
export type ExtensionMessage =
  // Popup → Service Worker
  | { type: "GET_SESSION_STATE" }
  | { type: "START_PAIRING"; pairingCode: string }
  | { type: "LOGOUT" }
  | { type: "IMPORT_JOB"; payload: ExtractedJob }
  | { type: "GET_AUTOFILL_PROFILE" }
  | { type: "FILL_FORM"; payload: { fields: DetectedField[]; mappings: FieldMapping[] } }
  | { type: "MARK_APPLIED"; payload: { jobUrl: string } }
  | { type: "DID_NOT_SUBMIT" }
  | { type: "GET_TAB_CONTEXT" }
  | { type: "RESOLVE_ATTEMPTS"; payload: { pageUrl: string } }
  | { type: "BIND_ATTEMPT"; payload: AttemptBinding }
  | { type: "CLEAR_TAB_CONTEXT" }
  | { type: "PAGE_CONTEXT"; payload: { pageUrl: string } }

  // Content Script → Service Worker
  | { type: "JOB_DETECTED"; payload: ExtractedJob }
  | { type: "APPLY_CLICKED"; payload: { pageUrl: string; buttonText: string; href: string | null } }
  | { type: "FORM_DETECTED"; payload: { fields: DetectedField[]; domain: string; atsType?: string; pageUrl?: string } }
  | { type: "SUBMIT_CLICKED"; payload: { pageUrl: string } }
  | { type: "CONFIRMATION_DETECTED"; payload: { pageUrl: string; confirmationText: string; eventId: string } }

  // Service Worker → Content Script
  | { type: "FILL_FIELDS"; payload: { mappings: FieldMapping[] } }
  | { type: "SCAN_FORM" }

  // Service Worker → Popup
  | { type: "SESSION_STATE"; payload: { connected: boolean; user?: { id: number; email: string; name: string } } }
  | { type: "JOB_IMPORTED"; payload: JobImportResponse }
  | { type: "EVENT_RECORDED"; payload: ApplicationEventResponse }
  | { type: "AUTOFILL_PROFILE"; payload: AutofillProfile }
  | { type: "AUTOFILL_COMPLETE"; payload: FillExecutionResult }
  | { type: "ATTEMPT_CANDIDATES"; payload: { candidates: AttemptCandidate[] } }
  | { type: "TAB_CONTEXT"; payload: { context: TabAttemptContext | null; fields?: DetectedField[] } }
  | { type: "MARK_APPLIED_RESULT"; payload: { success: boolean } }
  | { type: "SHOW_CONFIRMATION_PROMPT"; payload: { pageUrl: string } }
  | { type: "ERROR"; payload: { message: string; code?: string } };
