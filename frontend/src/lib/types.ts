// Shared API types mirroring the FastAPI Pydantic schemas (backend/app/domain/*).
// Keep these in sync with the backend response_models.

// ---- Auth ------------------------------------------------------------------
export interface AuthUser {
  id: number;
  email: string;
  name: string;
  avatar_url: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

// ---- Async task (background jobs) ------------------------------------------
export interface TaskQueued {
  task_id: string;
}

export interface TaskStatus {
  type: string;
  status: "queued" | "running" | "done" | "failed" | "skipped";
  message: string;
  // Ingest-specific counters (updated incrementally)
  fetched?: number;
  inserted?: number;
  updated?: number;
  embedded?: number;
  sources?: string[];
  // Terminal fields
  error?: string | null;
  result?: IngestResponse | ResumeResponse | RankResponse | null;
}

// ---- Resume (Phase 1) -------------------------------------------------------
export interface WorkItem {
  company: string | null;
  title: string | null;
  start: string | null;
  end: string | null;
  highlights: string[];
}

export interface EducationItem {
  degree: string | null;
  institution: string | null;
  year: string | null;
}

export interface ProfessionalLinks {
  linkedin: string | null;
  github: string | null;
  portfolio: string | null;
  website: string | null;
}

export interface ProjectItem {
  name: string | null;
  description: string | null;
  technologies: string[];
  achievements: string[];
  url: string | null;
}

export interface LanguageItem {
  language: string;
  proficiency: string | null;
}

export interface ProfessionalCredential {
  name: string;
  issuer: string | null;
}

export interface ParsedProfile {
  full_name: string | null;
  email: string | null;
  phone: string | null;
  headline: string | null;
  years_experience: number | null;
  skills: string[];
  education: EducationItem[];
  certifications: string[];
  languages: LanguageItem[];
  licenses: ProfessionalCredential[];
  links: ProfessionalLinks;
  projects: ProjectItem[];
  work_history: WorkItem[];
  preferred_roles: string[];
  seniority: string | null;
}

export interface ResumeResponse {
  resume_id: number;
  filename: string;
  is_active: boolean;
  lifecycle_status?: "active" | "draft" | "inactive";
  profile: ParsedProfile;
  matching_profile_changed?: boolean;
  extraction_warnings?: ExtractionWarning[];
}

export interface ExtractionWarning {
  code:
    | "short_extracted_text"
    | "low_pdf_text_coverage"
    | "likely_scanned_pdf"
    | "source_email_not_parsed"
    | "source_linkedin_not_parsed"
    | "source_github_not_parsed"
    | "missing_work_history"
    | "missing_education"
    | "missing_certifications"
    | "missing_languages"
    | "missing_licenses";
  message: string;
}

export interface ResumeImportListItem {
  resume_id: number;
  filename: string;
  created_at: string;
  lifecycle_status: "active" | "draft" | "inactive";
}

export interface ResumeImportDetail extends ResumeImportListItem {
  is_active: boolean;
  profile: ParsedProfile;
  extraction_evidence: ExtractionEvidence;
}

export interface EvidenceItem {
  item_index: number;
  excerpt: string;
}

export interface ExtractionEvidence {
  work_history: EvidenceItem[];
  education: EvidenceItem[];
  certifications: EvidenceItem[];
  projects: EvidenceItem[];
  languages: EvidenceItem[];
  licenses: EvidenceItem[];
}

export interface ProfileResponse {
  profile_id: number;
  resume_id: number;
  profile: ParsedProfile;
  matching_profile_changed?: boolean;
}

// ---- Dashboard search readiness --------------------------------------------
export type SearchReadinessOverallState = "needs_setup" | "almost_ready" | "ready";
export type SearchReadinessResumeState = "ready" | "needs_import" | "draft_review";
export type SearchReadinessSetupState = "ready" | "needs_setup";
export type SearchReadinessReason =
  | "draft_review"
  | "import_resume"
  | "complete_profile"
  | "set_job_preferences"
  | "view_matches";

export interface SearchReadinessCheck {
  state: SearchReadinessResumeState | SearchReadinessSetupState;
  has_pending_draft?: boolean | null;
}

export interface SearchReadinessResponse {
  overall_state: SearchReadinessOverallState;
  checks: {
    resume: SearchReadinessCheck;
    profile: SearchReadinessCheck;
    preferences: SearchReadinessCheck;
  };
  next_action: {
    reason: SearchReadinessReason;
    label: string;
  };
}

export interface TailoredWorkItem {
  company: string | null;
  title: string | null;
  start: string | null;
  end: string | null;
  original_highlights: string[];
  tailored_highlights: string[];
  tailored_highlight_source_indices?: number[];
}

export interface TailoredResume {
  job_id: number;
  professional_summary: string | null;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  headline: string | null;
  work_history: TailoredWorkItem[];
  education: EducationItem[];
  certifications: string[];
  matched_skills: string[];
  other_skills: string[];
  missing_keywords: string[];
  requirement_classifications?: RequirementClassification[];
}

export interface RequirementClassification {
  requirement: string;
  status: "verified" | "missing";
  priority: "unclassified";
  evidence_categories: string[];
}

export interface TailoredResumeDraftCreate {
  job_id: number;
  content: TailoredResume;
}

export interface TailoredResumeDraftListItem {
  id: number;
  job_id: number | null;
  source_profile_id: number | null;
  job_title: string;
  job_company: string | null;
  created_at: string;
  updated_at: string;
}

export interface TailoredResumeDraft extends TailoredResumeDraftListItem {
  content: TailoredResume;
}

// ---- Jobs (Phase 2) ---------------------------------------------------------
export interface IngestRequest {
  search_term: string;
  location?: string | null;
  country?: string | null;
  is_remote?: boolean | null;
  hours_old?: number | null;
  results_wanted?: number | null;
  sources?: string[] | null;
}

export interface IngestResponse {
  fetched: number;
  inserted: number;
  updated: number;
  embedded: number;
  sources: string[];
}

export interface Job {
  id: number;
  title: string;
  company: string | null;
  location: string | null;
  is_remote: boolean | null;
  job_type: string | null;
  description: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  source: string;
  source_url: string;
  posted_at: string | null;
  fetched_at: string;
  shift_type: string | null;
  company_logo: string | null;
  // Feed-separation fields
  visibility: "public" | "private";
  ingest_source: "scraper" | "manual" | "extension";
  created_by_user_id: number | null;
  imported_at: string | null;
  match_score?: number | null;
  match_reason?: Record<string, unknown> | null;
  employer_summary?: EmployerSummary | null;
}

export interface EmployerSummary {
  company: string;
  company_logo: string | null;
  job_count: number;
}

export interface JobListResponse {
  items: Job[];
  total: number;
  limit: number;
  next_cursor: string | null;
}

// ---- Matches ----------------------------------------------------------------
export type MatchReadinessDecision = "apply" | "save" | "stretch" | "skip";

export interface MatchReadiness {
  decision: MatchReadinessDecision;
  reasons: string[];
  caveats: string[];
  deterministic_blockers: string[];
  deterministic_warnings: string[];
  decision_basis: "score" | "blocker";
}

export interface MatchItem {
  job_id: number;
  title: string;
  company: string | null;
  location: string | null;
  is_remote: boolean | null;
  job_type: string | null;
  description: string | null;
  shift_type: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  source: string;
  source_url: string;
  company_logo: string | null;
  vector_score: number;
  confidence: string | null;
  llm_score: number | null;
  llm_confidence?: number | null;
  matched_skills: string[] | null;
  readiness: MatchReadiness;
  posted_at: string | null;
}

export interface MatchListResponse {
  items: MatchItem[];
  total: number;
  limit: number;
  offset: number;
  next_cursor: string | null;
  has_more: boolean;
}

export type MatchRecommendationReadiness = "apply" | "save" | "stretch";

export interface MatchRecommendationItem {
  job_id: number;
  title: string;
  company: string | null;
  location: string | null;
  remote: boolean | null;
  source: string;
  source_url: string;
  score: number;
  confidence: string | null;
  readiness: MatchRecommendationReadiness;
  matched_skills: string[];
  missing_skill: string | null;
  application_status: string | null;
}

export interface MatchRecommendationsResponse {
  items: MatchRecommendationItem[];
}

export interface RankResponse {
  ranked: number;
  profile_id: number;
}

export interface RankTaskResponse {
  task_id: string | null;
  state: "queued" | "active" | "cooldown";
  retry_after_seconds?: number | null;
}

export interface MatchExplanation {
  job_id: number;
  vector_score: number;
  confidence: string | null;
  llm_score: number | null;
  llm_confidence: number | null;
  llm_available: boolean;
  llm_error: string | null;
  explanation: string | null;
  matched_skills: string[];
  missing_keywords: string[];
  readiness: MatchReadiness;
}

export interface MatchSummary {
  job_id: number;
  vector_score: number;
  confidence: string | null;
  llm_score: number | null;
  llm_confidence?: number | null;
  explanation?: string | null;
  matched_skills: string[];
  missing_keywords: string[];
  readiness: MatchReadiness;
}

export interface MatchSkillAnalysis {
  job_id: number;
  vector_score: number;
  matched_skills: string[];
  missing_keywords: string[];
  readiness: MatchReadiness;
}

// ---- Cover letters (Phase 4) ------------------------------------------------
export interface CoverLetter {
  id: number;
  job_id: number;
  content: string;
  tone: string | null;
  model: string;
  is_edited: boolean;
  created_at: string;
  updated_at: string;
}

// ---- Applications (Phase 4 CRM) ---------------------------------------------
export type ApplicationStatus =
  | "saved"
  | "applied"
  | "interview"
  | "offer"
  | "rejected"
  | "closed";

export const APPLICATION_STATUSES: ApplicationStatus[] = [
  "saved",
  "applied",
  "interview",
  "offer",
  "rejected",
  "closed",
];

export interface Application {
  id: number;
  job_id: number;
  status: string;
  cover_letter_id: number | null;
  notes: string | null;
  next_action_type: string | null;
  next_action_note: string | null;
  next_action_due_date: string | null;
  next_action_completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationListItem {
  id: number;
  job_id: number;
  job_title: string;
  company: string | null;
  location: string | null;
  company_logo: string | null;
  source_url: string;
  status: string;
  cover_letter_id: number | null;
  notes: string | null;
  next_action_type: string | null;
  next_action_note: string | null;
  next_action_due_date: string | null;
  next_action_completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export const APPLICATION_NEXT_ACTION_TYPES = [
  "apply",
  "follow_up",
  "prepare_interview",
  "submit_assessment",
  "networking",
  "review_offer",
  "other",
] as const;

export type ApplicationNextActionType =
  (typeof APPLICATION_NEXT_ACTION_TYPES)[number];

export const APPLICATION_NEXT_ACTION_LABELS: Record<ApplicationNextActionType, string> = {
  apply: "Apply",
  follow_up: "Follow up",
  prepare_interview: "Prepare interview",
  submit_assessment: "Submit assessment",
  networking: "Networking",
  review_offer: "Review offer",
  other: "Other",
};

export interface ApplicationUpdate {
  status?: ApplicationStatus;
  cover_letter_id?: number | null;
  notes?: string | null;
  next_action_type?: ApplicationNextActionType | null;
  next_action_note?: string | null;
  next_action_due_date?: string | null;
  next_action_completed_at?: string | null;
}

export interface ApplicationListResponse {
  items: ApplicationListItem[];
  total: number;
  limit: number;
  next_cursor: string | null;
}

export interface ApplicationDashboardSummary {
  total_applications: number;
  counts_by_status: Record<ApplicationStatus, number>;
  active_pipeline_count: number;
  offer_count: number;
  offer_rate_percent: number;
}

export interface ApplicationActionCenterDatedItem {
  application_id: number;
  job_id: number;
  job_title: string;
  company: string | null;
  application_status: string;
  next_action_type: string | null;
  next_action_note: string | null;
  next_action_due_date: string;
  updated_at: string;
}

export interface ApplicationActionCenterMissingItem {
  application_id: number;
  job_id: number;
  job_title: string;
  company: string | null;
  application_status: string;
  updated_at: string;
}

export interface ApplicationActionCenterResponse {
  overdue: {
    count: number;
    items: ApplicationActionCenterDatedItem[];
  };
  due_today: {
    count: number;
    items: ApplicationActionCenterDatedItem[];
  };
  upcoming: {
    count: number;
    items: ApplicationActionCenterDatedItem[];
  };
  needs_next_action: {
    count: number;
    items: ApplicationActionCenterMissingItem[];
  };
}

export const APPLICATION_ACTIVITY_EVENT_TYPES = [
  "manual_next_action_created",
  "manual_next_action_updated",
  "manual_next_action_completed",
  "manual_next_action_cleared",
] as const;

export type ApplicationActivityEventType =
  (typeof APPLICATION_ACTIVITY_EVENT_TYPES)[number];

export interface ManualNextActionActivitySnapshot {
  type: string | null;
  due_date: string | null;
  completed: boolean;
  has_note: boolean;
}

export interface ManualNextActionActivityMetadata {
  source: string;
  previous: ManualNextActionActivitySnapshot;
  current: ManualNextActionActivitySnapshot;
  changed_fields: string[];
}

export interface ApplicationActivityEvent {
  id: string;
  event_type: ApplicationActivityEventType;
  metadata: ManualNextActionActivityMetadata;
  created_at: string;
}

export interface ApplicationActivityResponse {
  application_id: number;
  events: ApplicationActivityEvent[];
}

// ---- Job preferences (Phase 2) ----------------------------------------------
export type RemotePreference = "remote" | "onsite" | "hybrid" | "any";

export interface PreferenceResponse {
  id: number;
  user_id: number;
  job_titles: string[];
  keywords: string[];
  excluded_keywords: string[];
  locations: string[];
  job_types: string[];
  enabled_sources: string[];
  remote_preference: RemotePreference;
  salary_min: number | null;
  results_per_source: number;
  hours_old: number;
  is_active: boolean;
  last_scraped_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PreferenceUpdate {
  job_titles?: string[];
  keywords?: string[];
  excluded_keywords?: string[];
  locations?: string[];
  job_types?: string[];
  enabled_sources?: string[];
  remote_preference?: RemotePreference;
  salary_min?: number | null;
  results_per_source?: number;
  hours_old?: number;
  is_active?: boolean;
}

export interface ReadinessPreferenceResponse {
  id: number;
  user_id: number;
  minimum_annual_salary: number | null;
  salary_currency: string | null;
  remote_only: boolean | null;
  accepted_job_types: string[];
  preferred_locations: string[];
  blocked_keywords: string[];
  created_at: string;
  updated_at: string;
}

export interface ReadinessPreferenceUpdate {
  minimum_annual_salary: number | null;
  salary_currency: string | null;
  remote_only: boolean | null;
  accepted_job_types: string[];
  preferred_locations: string[];
  blocked_keywords: string[];
}

export interface RefreshResponse {
  task_id: string;
  dispatched_at: string;
}

// ---- Skills gap (Phase 2) ---------------------------------------------------
export interface SkillGapItem {
  keyword: string;
  frequency: number;
}

export interface SkillsGapSummary {
  items: SkillGapItem[];
  total_matches_considered: number;
}

// ---- Interview prep (Phase 2) -----------------------------------------------
export type QuestionCategory =
  | "technical"
  | "behavioral"
  | "system_design"
  | "role_specific"
  | "culture_fit";

export interface InterviewQuestion {
  question: string;
  category: QuestionCategory;
  why_it_matters: string;
  suggested_angle: string;
}

export interface InterviewPrepRequest {
  focus_areas?: string[];
  question_count?: number;
}

export interface InterviewPrepResponse {
  job_id: number;
  questions: InterviewQuestion[];
  focus_areas: string[];
  model: string;
  cached: boolean;
  created_at: string;
}
