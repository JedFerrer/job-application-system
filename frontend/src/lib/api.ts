import type {
  Application,
  ApplicationPackage,
  ApplicationPreparation,
  ApplicationPreparationPatch,
  ApplicationActivityResponse,
  ApplicationActionCenterResponse,
  ApplicationDashboardSummary,
  ApplicationListItem,
  ApplicationListResponse,
  ApplicationUpdate,
  ApplicationWorkspace,
  ApplicationAttempt,
  ApplicationSubmissionConfirmation,
  ApplicationSubmissionDetail,
  ApplicationSubmissionPage,
  ApplicationInterviewPrepRequest,
  ApplicationInterviewPrepResponse,
  ArtifactDetail,
  ArtifactDocumentType,
  ArtifactDownload,
  ArtifactFormat,
  ArtifactUploadResponse,
  ApplicationTimelinePage,
  AuthUser,
  CoverLetter,
  IngestRequest,
  IngestResponse,
  InterviewPrepRequest,
  InterviewPrepResponse,
  Job,
  JobSourceLink,
  SourceCapability,
  JobListResponse,
  MatchExplanation,
  MatchSummary,
  MatchSkillAnalysis,
  MatchListResponse,
  MatchRecommendationsResponse,
  ParsedProfile,
  PreferenceResponse,
  PreferenceUpdate,
  ReadinessPreferenceResponse,
  ReadinessPreferenceUpdate,
  ProfileResponse,
  SearchReadinessResponse,
  RankResponse,
  RankTaskResponse,
  RefreshResponse,
  ResumeResponse,
  ResumeImportDetail,
  ResumeImportListItem,
  SkillsGapSummary,
  TailoredResume,
  TailoredResumeDraft,
  TailoredResumeDraftCreate,
  TailoredResumeDraftListItem,
  TaskQueued,
  TaskStatus,
  TokenResponse,
} from "./types";

// All browser calls go through the Next.js rewrite proxy (see next.config.ts),
// which forwards `/api/backend/*` to the FastAPI `/api/v1/*` routes. Same-origin
// from the browser's perspective: no CORS, and the httpOnly refresh cookie is
// forwarded transparently.
export const API_URL = "/api/backend";

// ---- Access token (in-memory) ----------------------------------------------
// The short-lived access token lives only in memory (never localStorage) to
// minimise XSS exposure. The long-lived refresh token is an httpOnly cookie the
// browser sends automatically. On a full page reload the in-memory token is
// lost, so the session provider silently calls `/auth/refresh` on mount.
let accessToken: string | null = null;
export type RefreshFailure = "expired" | "unavailable";
let onRefreshFailure: ((failure: RefreshFailure) => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Only a refresh 401 proves that the browser session has expired. */
export function isExpiredRefresh(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function setRefreshFailureHandler(
  handler: ((failure: RefreshFailure) => void) | null,
): void {
  onRefreshFailure = handler;
}

/** Render a FastAPI/pydantic error payload into a human-readable string. */
function formatApiError(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (!d || typeof d !== "object") return String(d);
        const item = d as { loc?: unknown[]; msg?: string; message?: string };
        const field =
          Array.isArray(item.loc) && item.loc.length
            ? String(item.loc[item.loc.length - 1])
            : null;
        const message = item.msg || item.message || JSON.stringify(item);
        return field ? `${field}: ${message}` : message;
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      /* fall through */
    }
  }
  return `Request failed (HTTP ${status})`;
}

/** Typed error for any non-2xx response; carries status + raw detail. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function apiResponse(
  path: string,
  options: RequestInit & { skipAuth?: boolean; _retried?: boolean } = {},
): Promise<Response> {
  const { skipAuth = false, _retried = false, ...init } = options;
  const headers: Record<string, string> = {};

  // Don't set Content-Type for FormData (browser sets boundary itself).
  if (!(init.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  // CSRF-style guard for cookie auth; harmless for token auth.
  headers["X-Requested-With"] = "XMLHttpRequest";

  if (!skipAuth && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  if (init.headers) {
    const incoming =
      init.headers instanceof Headers
        ? Object.fromEntries(init.headers.entries())
        : (init.headers as Record<string, string>);
    Object.assign(headers, incoming);
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  // Transparent single-shot refresh on an expired access token.
  if (response.status === 401 && !skipAuth && !_retried && accessToken) {
    try {
      await refreshAccessToken();
    } catch (error) {
      setAccessToken(null);
      onRefreshFailure?.(isExpiredRefresh(error) ? "expired" : "unavailable");
      throw error;
    }
    return apiResponse(path, { ...options, _retried: true });
  }

  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: `HTTP ${response.status}` }));
    throw new ApiError(
      formatApiError(error.detail, response.status),
      response.status,
      error.detail,
    );
  }

  return response;
}

async function apiRequest<T>(
  path: string,
  options: RequestInit & { skipAuth?: boolean; _retried?: boolean } = {},
): Promise<T> {
  const response = await apiResponse(path, options);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function attachmentFilename(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const encoded = /(?:^|;)\s*filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header)?.[1];
  if (encoded) {
    try { return safeFilename(decodeURIComponent(encoded), fallback); } catch { /* use plain filename */ }
  }
  const plain = /(?:^|;)\s*filename\s*=\s*(?:"([^"]*)"|([^;]*))/i.exec(header);
  return safeFilename(plain?.[1] ?? plain?.[2] ?? "", fallback);
}

function safeFilename(value: string, fallback: string): string {
  const name = value.trim().replace(/[\\/\x00-\x1f\x7f]/g, "").replace(/^\.+/, "");
  return name || fallback;
}

/**
 * Exchange the httpOnly refresh cookie for a fresh access token.
 * Every browser consumer uses this single-flight coordinator so refresh-token
 * rotation cannot race two refresh requests against one another.
 */
let refreshInFlight: Promise<TokenResponse> | null = null;
export function refreshAccessToken(): Promise<TokenResponse> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = apiRequest<TokenResponse>("/auth/refresh", {
    method: "POST",
    skipAuth: true,
  }).then((res) => {
    setAccessToken(res.access_token);
    return res;
  }).finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

// ---- Resume -----------------------------------------------------------------
export const resumeApi = {
  /** Upload file — returns a task_id immediately (202). Poll getTaskStatus. */
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiRequest<TaskQueued>("/resume", { method: "POST", body: form });
  },
  getTaskStatus: (taskId: string) =>
    apiRequest<TaskStatus>(`/resume/tasks/${taskId}`),
  getProfile: () => apiRequest<ProfileResponse | null>("/resume/profile"),
  listImportDrafts: () =>
    apiRequest<ResumeImportListItem[]>("/resume/imports"),
  getImportDraft: (resumeId: number) =>
    apiRequest<ResumeImportDetail>(`/resume/imports/${resumeId}`),
  approveImport: (resumeId: number) =>
    apiRequest<ProfileResponse>(`/resume/imports/${resumeId}/approve`, {
      method: "POST",
    }),
  rejectImport: (resumeId: number) =>
    apiRequest<void>(`/resume/imports/${resumeId}/reject`, { method: "POST" }),
  updateProfile: (profile: ParsedProfile) =>
    apiRequest<ProfileResponse>("/resume/profile", {
      method: "PUT",
      body: JSON.stringify({ profile }),
    }),
  tailorResume: (jobId: number) =>
    apiRequest<TailoredResume>(`/resume/tailor/${jobId}`),
  previewCoverage: (content: TailoredResume) => {
    // Coverage is advisory; omit any client analysis so the server can
    // recompute Candidate Fit from the authenticated profile and job.
    const { requirement_classifications: _ignored, ...snapshot } = content;
    return apiRequest<TailoredResume>("/resume/coverage", {
      method: "POST",
      body: JSON.stringify(snapshot),
    });
  },
  validateExport: (content: TailoredResume) =>
    apiRequest<TailoredResume>("/resume/export-validation", {
      method: "POST",
      body: JSON.stringify(content),
    }),
  createDraft: (payload: TailoredResumeDraftCreate) =>
    apiRequest<TailoredResumeDraft>("/resume/drafts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listDrafts: () =>
    apiRequest<TailoredResumeDraftListItem[]>("/resume/drafts"),
  getDraft: (draftId: number) =>
    apiRequest<TailoredResumeDraft>(`/resume/drafts/${draftId}`),
  deleteDraft: (draftId: number) =>
    apiRequest<void>(`/resume/drafts/${draftId}`, { method: "DELETE" }),
};

// ---- Jobs -------------------------------------------------------------------
export const jobsApi = {
  /** Queue ingest — returns a task_id immediately (202). Poll getTaskStatus. */
  ingest: (payload: IngestRequest) =>
    apiRequest<TaskQueued>("/jobs/ingest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getTaskStatus: (taskId: string) =>
    apiRequest<TaskStatus>(`/jobs/tasks/${taskId}`),
  listSourceCapabilities: () => apiRequest<SourceCapability[]>("/jobs/sources"),
  list: (
    opts: {
      q?: string;
      source?: string;
      isRemote?: boolean;
      jobType?: string;
      location?: string;
      company?: string;
      companyExact?: string;
      minSalary?: number;
      sourceType?: string;
      shiftType?: string;
      importedOnly?: boolean;
      archiveState?: "active" | "archived";
      lifecycle?: "active" | "expired";
      cursor?: string;
      limit?: number;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    if (opts.q) qs.set("q", opts.q);
    if (opts.source) qs.set("source", opts.source);
    if (opts.isRemote !== undefined) qs.set("is_remote", String(opts.isRemote));
    if (opts.jobType) qs.set("job_type", opts.jobType);
    if (opts.location) qs.set("location", opts.location);
    if (opts.company) qs.set("company", opts.company);
    if (opts.companyExact) qs.set("company_exact", opts.companyExact);
    if (opts.minSalary !== undefined) qs.set("min_salary", String(opts.minSalary));
    if (opts.sourceType) qs.set("source_type", opts.sourceType);
    if (opts.shiftType) qs.set("shift_type", opts.shiftType);
    if (opts.importedOnly) qs.set("imported_only", "true");
    if (opts.archiveState) qs.set("archive_state", opts.archiveState);
    if (opts.lifecycle) qs.set("lifecycle", opts.lifecycle);
    if (opts.cursor) qs.set("cursor", opts.cursor);
    qs.set("limit", String(opts.limit ?? 50));
    return apiRequest<JobListResponse>(`/jobs?${qs.toString()}`);
  },
  /** Per-user archive (affects only the current user). */
  archive: (jobId: number) =>
    apiRequest<void>(`/jobs/${jobId}/archive`, { method: "PATCH" }),
  /** Restore a per-user archived job relationship to the active feed. */
  restore: (jobId: number) =>
    apiRequest<void>(`/jobs/${jobId}/restore`, { method: "PATCH" }),
  importJob: (payload: { url: string; title: string; company?: string; description?: string }) =>
    apiRequest<Job>("/jobs/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** List jobs imported by the current user (manual + extension). */
  listImported: (opts: { cursor?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (opts.cursor) qs.set("cursor", opts.cursor);
    qs.set("limit", String(opts.limit ?? 50));
    return apiRequest<JobListResponse>(`/jobs/imported?${qs.toString()}`);
  },
  /** Fetch a single job by id. */
  get: (jobId: number) => apiRequest<Job>(`/jobs/${jobId}`),
  listSources: (jobId: number) => apiRequest<JobSourceLink[]>(`/jobs/${jobId}/sources`),
};

// ---- Matches ----------------------------------------------------------------
export const matchesApi = {
  rank: () => apiRequest<RankTaskResponse>("/matches/rank", { method: "POST" }),
  getRankTaskStatus: (taskId: string) =>
    apiRequest<TaskStatus>(`/matches/tasks/${taskId}`),
  analyze: (jobId: number) =>
    apiRequest<MatchSummary>(`/matches/${jobId}/analyze`, { method: "POST" }),
  list: (
    opts: {
      limit?: number;
      cursor?: string;
      q?: string;
      source?: string;
      isRemote?: boolean;
      jobType?: string;
      location?: string;
      company?: string;
      shiftType?: string;
      sourceType?: string;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    qs.set("limit", String(opts.limit ?? 50));
    if (opts.cursor) qs.set("cursor", opts.cursor);
    if (opts.q) qs.set("q", opts.q);
    if (opts.source) qs.set("source", opts.source);
    if (opts.isRemote !== undefined) qs.set("is_remote", String(opts.isRemote));
    if (opts.jobType) qs.set("job_type", opts.jobType);
    if (opts.location) qs.set("location", opts.location);
    if (opts.company) qs.set("company", opts.company);
    if (opts.shiftType) qs.set("shift_type", opts.shiftType);
    if (opts.sourceType) qs.set("source_type", opts.sourceType);
    return apiRequest<MatchListResponse>(`/matches?${qs.toString()}`);
  },
  recommendations: (limit = 4) =>
    apiRequest<MatchRecommendationsResponse>(`/matches/recommendations?limit=${limit}`),
  explain: (jobId: number) =>
    apiRequest<MatchExplanation>(`/matches/${jobId}/explain`),
  summary: (jobId: number) =>
    apiRequest<MatchSummary | null>(`/matches/${jobId}/summary`),
  skills: (jobId: number) =>
    apiRequest<MatchSkillAnalysis>(`/matches/${jobId}/skills`),
  skillsGapSummary: (topN = 5) =>
    apiRequest<SkillsGapSummary>(`/matches/skills-gap-summary?top_n=${topN}`),
};

// ---- Preferences (Phase 2) --------------------------------------------------
export const preferencesApi = {
  get: () => apiRequest<PreferenceResponse>("/preferences"),
  upsert: (payload: PreferenceUpdate) =>
    apiRequest<PreferenceResponse>("/preferences", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  patch: (payload: PreferenceUpdate) =>
    apiRequest<PreferenceResponse>("/preferences", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  refresh: () =>
    apiRequest<RefreshResponse>("/preferences/refresh", { method: "POST" }),
};

// ---- Readiness preferences -------------------------------------------------
export const readinessPreferencesApi = {
  get: () => apiRequest<ReadinessPreferenceResponse>("/readiness-preferences"),
  upsert: (payload: ReadinessPreferenceUpdate) =>
    apiRequest<ReadinessPreferenceResponse>("/readiness-preferences", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
};

// ---- Dashboard --------------------------------------------------------------
export const dashboardApi = {
  getSearchReadiness: () =>
    apiRequest<SearchReadinessResponse>("/dashboard/search-readiness"),
};

// ---- Interview prep (Phase 2) -----------------------------------------------
export const interviewApi = {
  generate: (
    jobId: number,
    payload: InterviewPrepRequest = {},
    forceRefresh = false,
  ) =>
    apiRequest<InterviewPrepResponse>(
      `/interview-prep/${jobId}?force_refresh=${forceRefresh}`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
};

// ---- Cover letters ----------------------------------------------------------
export const coverLettersApi = {
  generate: (jobId: number, tone?: string) =>
    apiRequest<CoverLetter>("/cover-letters", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, tone: tone ?? null }),
    }),
  get: (id: number) => apiRequest<CoverLetter>(`/cover-letters/${id}`),
  getByJobId: (jobId: number) =>
    apiRequest<CoverLetter>(`/cover-letters/by-job/${jobId}`),
  update: (id: number, content: string) =>
    apiRequest<CoverLetter>(`/cover-letters/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    }),
};

// ---- Applications -----------------------------------------------------------
export const applicationsApi = {
  list: (opts: { cursor?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (opts.cursor) qs.set("cursor", opts.cursor);
    qs.set("limit", String(opts.limit ?? 50));
    return apiRequest<ApplicationListResponse>(`/applications?${qs.toString()}`);
  },
  getDashboardSummary: () =>
    apiRequest<ApplicationDashboardSummary>("/applications/dashboard-summary"),
  listDueNextActions: (dueBy: string) =>
    apiRequest<ApplicationListResponse>(
      `/applications/next-actions?${new URLSearchParams({ due_by: dueBy }).toString()}`,
    ),
  getActionCenter: (today: string) =>
    apiRequest<ApplicationActionCenterResponse>(
      `/applications/action-center?${new URLSearchParams({ today }).toString()}`,
    ),
  getActivity: (
    id: number,
    opts: { limit?: number } = {},
  ) => {
    const qs = new URLSearchParams();
    qs.set("limit", String(opts.limit ?? 10));
    return apiRequest<ApplicationActivityResponse>(
      `/applications/${id}/activity?${qs.toString()}`,
    );
  },
  create: (jobId: number) =>
    apiRequest<Application>("/applications", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId }),
    }),
  update: (
    id: number,
    payload: ApplicationUpdate,
  ) =>
    apiRequest<Application>(`/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  delete: (id: number, opts: { confirmDiscardPreparation?: boolean } = {}) => {
    const qs = opts.confirmDiscardPreparation ? "?confirm_discard_preparation=true" : "";
    return apiRequest<void>(`/applications/${id}${qs}`, {
      method: "DELETE",
    });
  },
  getPreparation: (id: number) =>
    apiRequest<ApplicationPreparation>(`/applications/${id}/preparation`),
  getWorkspace: (id: number) =>
    apiRequest<ApplicationWorkspace>(`/applications/${id}/workspace`),
  createAttempt: (id: number, expectedVersion: number, packageId: number, packageHash: string) =>
    apiRequest<ApplicationAttempt>(`/applications/${id}/attempts`, {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion, package_id: packageId, package_hash: packageHash }),
    }),
  getTimeline: (id: number, cursor?: string) =>
    apiRequest<ApplicationTimelinePage>(`/applications/${id}/timeline?limit=25${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
  confirmSubmission: (id: number, payload: ApplicationSubmissionConfirmation, idempotencyKey: string) =>
    apiRequest<ApplicationSubmissionDetail>(`/applications/${id}/submissions`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload) }),
  listSubmissions: (id: number, opts: { limit?: number; cursor?: string } = {}) => {
    const qs = new URLSearchParams({ limit: String(opts.limit ?? 25) });
    if (opts.cursor) qs.set("cursor", opts.cursor);
    return apiRequest<ApplicationSubmissionPage>(`/applications/${id}/submissions?${qs}`);
  },
  getSubmission: (id: number, submissionId: number) =>
    apiRequest<ApplicationSubmissionDetail>(`/applications/${id}/submissions/${submissionId}`),
  deleteSubmission: (id: number, submissionId: number) =>
    apiRequest<void>(`/applications/${id}/submissions/${submissionId}`, { method: "DELETE" }),
  prepareInterview: (id: number, payload: ApplicationInterviewPrepRequest) =>
    apiRequest<ApplicationInterviewPrepResponse>(`/applications/${id}/interview-prep`, { method: "POST", body: JSON.stringify(payload) }),
  patchPreparation: (id: number, payload: ApplicationPreparationPatch) =>
    apiRequest<ApplicationPreparation>(`/applications/${id}/preparation`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  createPackage: (id: number, expectedVersion: number, idempotencyKey: string) =>
    apiRequest<ApplicationPackage>(`/applications/${id}/packages`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ expected_version: expectedVersion }),
    }),
  getPackage: (applicationId: number, packageId: number) =>
    apiRequest<ApplicationPackage>(`/applications/${applicationId}/packages/${packageId}`),
  uploadArtifact: (applicationId: number, packageId: number, file: Blob, filename: string, documentType: ArtifactDocumentType, format: ArtifactFormat, exporterVersion: string) => {
    const form = new FormData();
    form.append("file", file, filename);
    form.append("document_type", documentType);
    form.append("format", format);
    form.append("exporter_version", exporterVersion);
    return apiRequest<ArtifactUploadResponse>(`/applications/${applicationId}/packages/${packageId}/artifacts`, { method: "POST", body: form });
  },
  getArtifact: (applicationId: number, artifactId: number) =>
    apiRequest<ArtifactDetail>(`/applications/${applicationId}/artifacts/${artifactId}`),
  downloadArtifact: async (applicationId: number, artifactId: number, fallbackFilename = "artifact") : Promise<ArtifactDownload> => {
    const response = await apiResponse(`/applications/${applicationId}/artifacts/${artifactId}/download`);
    return { blob: await response.blob(), filename: attachmentFilename(response.headers.get("Content-Disposition"), fallbackFilename) };
  },
  retryArtifactValidation: (applicationId: number, artifactId: number) =>
    apiRequest<ArtifactUploadResponse>(`/applications/${applicationId}/artifacts/${artifactId}/retry`, { method: "POST" }),
  deleteArtifact: (applicationId: number, artifactId: number) =>
    apiRequest<void>(`/applications/${applicationId}/artifacts/${artifactId}`, { method: "DELETE" }),
  approvePreparation: (id: number, expectedVersion: number, packageId: number, contentSha256: string) =>
    apiRequest<ApplicationPreparation>(`/applications/${id}/preparation/approve`, {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion, package_id: packageId, content_sha256: contentSha256 }),
    }),
  createDocumentReview: (applicationId: number, packageId: number, idempotencyKey: string, rerun = false) => apiRequest<import("./types").DocumentReview | import("./types").DocumentReviewQueued>(`/applications/${applicationId}/packages/${packageId}/reviews`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ rerun }) }),
  getDocumentReview: (applicationId: number, packageId: number, reviewId: string) => apiRequest<import("./types").DocumentReview>(`/applications/${applicationId}/packages/${packageId}/reviews/${reviewId}`),
  getTaskStatus: (taskId: string) => apiRequest<TaskStatus>(`/applications/tasks/${taskId}`),
  acknowledgeDocumentReview: (applicationId: number, packageId: number, reviewId: string, packageHash: string, acknowledgments: { finding_id: string; disposition: "keep_wording" | "will_revise" }[]) => apiRequest<import("./types").DocumentReview>(`/applications/${applicationId}/packages/${packageId}/reviews/${reviewId}/acknowledgments`, { method: "POST", body: JSON.stringify({ package_content_sha256: packageHash, acknowledgments }) }),
};

/** Fetch all application pages via cursor pagination.
 *  Used for saved-state detection in jobs/matches panels so that
 *  users with >50 applications are not silently affected. */
export async function listAllApplications(): Promise<ApplicationListItem[]> {
  const all: ApplicationListItem[] = [];
  let cursor: string | undefined;
  while (true) {
    const page = await applicationsApi.list({ cursor, limit: 100 });
    all.push(...page.items);
    if (!page.next_cursor) break;
    cursor = page.next_cursor;
  }
  return all;
}

// ---- Auth -------------------------------------------------------------------
export const authApi = {
  register: (payload: { email: string; name: string; password: string }) =>
    apiRequest<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
      skipAuth: true,
    }),
  login: (payload: { email: string; password: string }) =>
    apiRequest<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
      skipAuth: true,
    }),
  google: (idToken: string) =>
    apiRequest<TokenResponse>("/auth/google", {
      method: "POST",
      body: JSON.stringify({ id_token: idToken }),
      skipAuth: true,
    }),
  /** Exchange the httpOnly refresh cookie for a fresh access token. */
  refresh: refreshAccessToken,
  logout: () =>
    apiRequest<void>("/auth/logout", { method: "POST", skipAuth: true }),
  me: () => apiRequest<AuthUser>("/auth/me"),
  forgotPassword: (email: string) =>
    apiRequest<{ message: string; reset_url?: string }>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
      skipAuth: true,
    }),
  resetPassword: (token: string, password: string) =>
    apiRequest<void>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, password }),
      skipAuth: true,
    }),
};

// ---- Extension sessions (web dashboard) -------------------------------------

export interface ExtensionSessionItem {
  id: string;
  device_name: string | null;
  browser: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}

export interface ExtensionSessionListResponse {
  sessions: ExtensionSessionItem[];
}

export const extensionApi = {
  /** List extension sessions for the current user (web dashboard). */
  listSessions: () =>
    apiRequest<ExtensionSessionListResponse>("/extension/sessions"),

  /** Revoke an extension session by id (web dashboard). */
  revokeSession: (sessionId: string) =>
    apiRequest<void>(`/extension/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),
};
