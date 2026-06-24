// Shared API types mirroring the FastAPI Pydantic schemas (backend/app/domain/*).
// Keep these in sync with the backend response_models.

// ---- Async task (background jobs) ------------------------------------------
export interface TaskQueued {
  task_id: string;
}

export interface TaskStatus {
  type: string;
  status: "queued" | "running" | "done" | "failed";
  message: string;
  // Ingest-specific counters (updated incrementally)
  fetched?: number;
  inserted?: number;
  updated?: number;
  embedded?: number;
  sources?: string[];
  // Terminal fields
  error?: string | null;
  result?: IngestResponse | ResumeResponse | null;
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

export interface ParsedProfile {
  full_name: string | null;
  email: string | null;
  phone: string | null;
  headline: string | null;
  years_experience: number | null;
  skills: string[];
  education: EducationItem[];
  certifications: string[];
  work_history: WorkItem[];
  preferred_roles: string[];
  seniority: string | null;
}

export interface ResumeResponse {
  resume_id: number;
  filename: string;
  is_active: boolean;
  profile: ParsedProfile;
}

export interface ProfileResponse {
  profile_id: number;
  resume_id: number;
  profile: ParsedProfile;
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
  is_archived: boolean;
  shift_type: string | null;
}

export interface JobListResponse {
  items: Job[];
  total: number;
  limit: number;
  offset: number;
}

// ---- Matches (Phase 3) ------------------------------------------------------
export interface MatchItem {
  job_id: number;
  title: string;
  company: string | null;
  location: string | null;
  is_remote: boolean | null;
  source: string;
  source_url: string;
  vector_score: number;
  confidence: string | null;
  llm_score: number | null;
  posted_at: string | null;
}

export interface MatchListResponse {
  items: MatchItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface RankResponse {
  ranked: number;
  profile_id: number;
}

export interface MatchExplanation {
  job_id: number;
  vector_score: number;
  confidence: string | null;
  llm_score: number | null;
  llm_confidence: number | null;
  explanation: string | null;
  matched_skills: string[];
  missing_keywords: string[];
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
  created_at: string;
  updated_at: string;
}

export interface ApplicationListItem {
  id: number;
  job_id: number;
  job_title: string;
  company: string | null;
  location: string | null;
  source_url: string;
  status: string;
  cover_letter_id: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationListResponse {
  items: ApplicationListItem[];
  total: number;
}
