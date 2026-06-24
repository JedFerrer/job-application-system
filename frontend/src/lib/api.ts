import type {
  Application,
  ApplicationListResponse,
  ApplicationStatus,
  CoverLetter,
  IngestRequest,
  IngestResponse,
  Job,
  JobListResponse,
  MatchExplanation,
  MatchListResponse,
  ParsedProfile,
  ProfileResponse,
  RankResponse,
  ResumeResponse,
  TaskQueued,
  TaskStatus,
} from "./types";

// All browser calls go through the Next.js rewrite proxy (see next.config.ts),
// which forwards `/api/backend/*` to the FastAPI `/api/v1/*` routes. Same-origin
// from the browser's perspective: no CORS, ready for cookie auth later.
export const API_URL = "/api/backend";

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

async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};

  // Don't set Content-Type for FormData (browser sets boundary itself).
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  // CSRF-style guard for cookie auth later; harmless now.
  headers["X-Requested-With"] = "XMLHttpRequest";

  if (options.headers) {
    const incoming =
      options.headers instanceof Headers
        ? Object.fromEntries(options.headers.entries())
        : (options.headers as Record<string, string>);
    Object.assign(headers, incoming);
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });

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

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
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
  getProfile: () => apiRequest<ProfileResponse>("/resume/profile"),
  updateProfile: (profile: ParsedProfile) =>
    apiRequest<ProfileResponse>("/resume/profile", {
      method: "PUT",
      body: JSON.stringify({ profile }),
    }),
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
  list: (
    opts: {
      source?: string;
      isRemote?: boolean;
      jobType?: string;
      location?: string;
      minSalary?: number;
      includeArchived?: boolean;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    if (opts.source) qs.set("source", opts.source);
    if (opts.isRemote !== undefined) qs.set("is_remote", String(opts.isRemote));
    if (opts.jobType) qs.set("job_type", opts.jobType);
    if (opts.location) qs.set("location", opts.location);
    if (opts.minSalary !== undefined) qs.set("min_salary", String(opts.minSalary));
    if (opts.includeArchived) qs.set("include_archived", "true");
    qs.set("limit", String(opts.limit ?? 50));
    qs.set("offset", String(opts.offset ?? 0));
    return apiRequest<JobListResponse>(`/jobs?${qs.toString()}`);
  },
  archive: (jobId: number, archived: boolean) =>
    apiRequest<Job>(`/jobs/${jobId}/archive?archived=${archived}`, { method: "PATCH" }),
};

// ---- Matches ----------------------------------------------------------------
export const matchesApi = {
  rank: (limit = 100) =>
    apiRequest<RankResponse>("/matches/rank", {
      method: "POST",
      body: JSON.stringify({ limit }),
    }),
  list: (opts: { limit?: number; offset?: number } = {}) => {
    const qs = new URLSearchParams({
      limit: String(opts.limit ?? 50),
      offset: String(opts.offset ?? 0),
    });
    return apiRequest<MatchListResponse>(`/matches?${qs.toString()}`);
  },
  explain: (jobId: number) =>
    apiRequest<MatchExplanation>(`/matches/${jobId}/explain`),
};

// ---- Cover letters ----------------------------------------------------------
export const coverLettersApi = {
  generate: (jobId: number, tone?: string) =>
    apiRequest<CoverLetter>("/cover-letters", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, tone: tone ?? null }),
    }),
  get: (id: number) => apiRequest<CoverLetter>(`/cover-letters/${id}`),
  update: (id: number, content: string) =>
    apiRequest<CoverLetter>(`/cover-letters/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    }),
};

// ---- Applications -----------------------------------------------------------
export const applicationsApi = {
  list: () => apiRequest<ApplicationListResponse>("/applications"),
  create: (jobId: number) =>
    apiRequest<Application>("/applications", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId }),
    }),
  update: (
    id: number,
    payload: {
      status?: ApplicationStatus;
      cover_letter_id?: number | null;
      notes?: string | null;
    },
  ) =>
    apiRequest<Application>(`/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
};
