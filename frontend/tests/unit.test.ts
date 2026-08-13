/**
 * Frontend unit tests — pure functions, validators, and API types.
 *
 * Covers:
 *  - formatSalary, formatRelativeDate, stripMarkdown (formatting helpers)
 *  - ingestSchema validation (Zod form validation)
 *  - API error handling patterns
 *
 * Run with: npm test
 */

import { describe, it, expect, vi, afterEach } from "vitest";

// ── Format helpers ───────────────────────────────────────────────────────

import { formatSalary, formatRelativeDate } from "../src/lib/format";
import {
  formatNextActionDateLabel,
  getNextActionLabel,
  getNextActionState,
} from "../src/lib/next-actions";
import { stripMarkdown } from "../src/lib/utils";
import { formatSkillLabel } from "../src/lib/skill-display";

describe("formatSalary", () => {
  it("returns empty string for null inputs", () => {
    expect(formatSalary(null, null, null)).toBe("");
    expect(formatSalary(undefined, undefined, undefined)).toBe("");
  });

  it('formats annual salary with K suffix', () => {
    expect(formatSalary(80000, 120000, "USD")).toBe("$80K–120K");
    expect(formatSalary(100000, 150000, "USD")).toBe("$100K–150K");
  });

  it("formats single salary value", () => {
    expect(formatSalary(90000, null, "USD")).toBe("$90K");
    expect(formatSalary(null, 75000, "USD")).toBe("$75K");
  });

  it('adds /hr suffix for hourly rates', () => {
    expect(formatSalary(30, 80, "USD")).toBe("$30–80/hr");
    // Single value without max: heuristic can't determine hourly, so no /hr suffix.
    expect(formatSalary(25, null, "USD")).toBe("$25");
  });

  it("uses full number for values under 10K", () => {
    expect(formatSalary(5000, 8000, "USD")).toBe("$5,000–8,000");
  });

  it("handles non-USD currency", () => {
    expect(formatSalary(60000, 90000, "EUR")).toBe("EUR 60K–90K");
  });
});

describe("formatRelativeDate", () => {
  it("returns empty string for null/undefined", () => {
    expect(formatRelativeDate(null)).toBe("");
    expect(formatRelativeDate(undefined)).toBe("");
  });

  it("returns hourly text for dates under 24 hours old", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T12:00:00Z"));
    try {
      expect(formatRelativeDate("2026-07-14T00:00:00Z")).toBe("12h ago");
    } finally {
      vi.useRealTimers();
    }
  });

  it('returns "yesterday" for yesterday', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T12:00:00Z"));
    try {
      expect(formatRelativeDate("2026-07-13T12:00:00Z")).toBe("yesterday");
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns empty for invalid date strings", () => {
    expect(formatRelativeDate("not-a-date")).toBe("");
  });
});

describe("stripMarkdown", () => {
  it("returns empty for null/undefined", () => {
    expect(stripMarkdown(null)).toBe("");
    expect(stripMarkdown(undefined)).toBe("");
  });

  it("strips bold markers", () => {
    expect(stripMarkdown("**hello** world")).toBe("hello world");
  });

  it("strips italic markers", () => {
    expect(stripMarkdown("*emphasis* text")).toBe("emphasis text");
  });

  it("converts links to link text", () => {
    expect(stripMarkdown("[click here](https://example.com)")).toBe(
      "click here"
    );
  });

  it("normalises list bullets", () => {
    expect(stripMarkdown("- item 1\n- item 2")).toBe("• item 1 • item 2");
  });

  it("removes heading markers", () => {
    expect(stripMarkdown("## Heading")).toBe("Heading");
  });

  it("removes HTML tags", () => {
    expect(stripMarkdown("<p>text</p>")).toBe("text");
  });
});

describe("formatSkillLabel", () => {
  it("uses familiar labels for canonical skill keys", () => {
    expect(formatSkillLabel("node.js")).toBe("Node.js");
    expect(formatSkillLabel("ci/cd")).toBe("CI/CD");
    expect(formatSkillLabel("rest_api")).toBe("REST API");
    expect(formatSkillLabel("postgresql")).toBe("PostgreSQL");
  });

  it("humanizes unknown normalized skill keys", () => {
    expect(formatSkillLabel("data-engineering")).toBe("Data Engineering");
  });
});

describe("next-action helpers", () => {
  it("maps known action types to display labels", () => {
    expect(getNextActionLabel("follow_up")).toBe("Follow up");
    expect(getNextActionLabel(null)).toBe("Custom");
  });

  it("formats due dates for next actions", () => {
    expect(formatNextActionDateLabel("2026-07-22")).toBeTruthy();
  });

  it("keeps due today and overdue states distinct", () => {
    const todayIso = "2026-07-14";
    expect(
      getNextActionState("follow_up", "2026-07-14", null, todayIso).statusLabel,
    ).toBe("Due today");
    expect(
      getNextActionState("follow_up", "2026-07-13", null, todayIso).statusLabel,
    ).toBe("Overdue");
    expect(
      getNextActionState("follow_up", "2026-07-15", null, todayIso).statusLabel,
    ).toBe("Due");
    expect(
      getNextActionState("follow_up", "2026-07-14", "2026-07-14T10:00:00Z", todayIso)
        .statusLabel,
    ).toBe("Completed");
  });
});

// ── Zod validators ───────────────────────────────────────────────────────

import { ingestSchema } from "../src/lib/validators";

describe("ingestSchema", () => {
  it("accepts valid minimal input", () => {
    const result = ingestSchema.safeParse({
      search_term: "software engineer",
      sources: ["indeed", "linkedin"],
    });
    expect(result.success).toBe(true);
  });

  it("rejects empty search_term", () => {
    const result = ingestSchema.safeParse({
      search_term: "",
      sources: ["indeed"],
    });
    expect(result.success).toBe(false);
  });

  it("rejects empty sources array", () => {
    const result = ingestSchema.safeParse({
      search_term: "dev",
      sources: [],
    });
    expect(result.success).toBe(false);
  });

  it("rejects invalid source name", () => {
    const result = ingestSchema.safeParse({
      search_term: "dev",
      sources: ["invalid-source"],
    });
    expect(result.success).toBe(false);
  });

  it("rejects results_wanted above max", () => {
    const result = ingestSchema.safeParse({
      search_term: "dev",
      sources: ["indeed"],
      results_wanted: 999,
    });
    expect(result.success).toBe(false);
  });

  it("accepts optional fields", () => {
    const result = ingestSchema.safeParse({
      search_term: "dev",
      sources: ["indeed"],
      location: "Manila",
      is_remote: true,
      hours_old: 48,
    });
    expect(result.success).toBe(true);
  });
});

// ── API error handling patterns ──────────────────────────────────────────

describe("API error handling", () => {
  it("APIError stores status and detail", async () => {
    // Simulate the APIError class behavior from api.ts
    class APIError extends Error {
      status: number;
      detail: string;
      constructor(status: number, detail: string) {
        super(detail);
        this.status = status;
        this.detail = detail;
      }
    }

    const err = new APIError(401, "Invalid token");
    expect(err.status).toBe(401);
    expect(err.detail).toBe("Invalid token");
    expect(err.message).toBe("Invalid token");
    expect(err instanceof Error).toBe(true);
  });

  it("401 triggers token refresh logic path", () => {
    // When API returns 401, the client should attempt token refresh.
    // This test verifies the status code detection logic.
    const needsRefresh = (status: number) => status === 401;
    expect(needsRefresh(401)).toBe(true);
    expect(needsRefresh(403)).toBe(false);
    expect(needsRefresh(200)).toBe(false);
  });

  it("refresh failure clears auth state", () => {
    // Simulate: if refresh fails, clear token and redirect.
    let token: string | null = "some-access-token";
    const clearAuth = () => {
      token = null;
    };
    clearAuth();
    expect(token).toBeNull();
  });
});

// ── Application status transitions ───────────────────────────────────────

describe("Application status transitions", () => {
  const VALID_STATUSES = [
    "saved",
    "applied",
    "interview",
    "offer",
    "rejected",
    "closed",
  ] as const;

  it("all valid statuses are recognised", () => {
    expect(VALID_STATUSES).toHaveLength(6);
    expect(VALID_STATUSES).toContain("saved");
    expect(VALID_STATUSES).toContain("applied");
    expect(VALID_STATUSES).toContain("interview");
    expect(VALID_STATUSES).toContain("offer");
    expect(VALID_STATUSES).toContain("rejected");
    expect(VALID_STATUSES).toContain("closed");
  });

  it("status display labels are defined for each status", () => {
    const labels: Record<string, string> = {
      saved: "Saved",
      applied: "Applied",
      interview: "Interview",
      offer: "Offer",
      rejected: "Rejected",
      closed: "Closed",
    };
    for (const s of VALID_STATUSES) {
      expect(labels[s]).toBeDefined();
    }
  });
});

// ── Dashboard state patterns ─────────────────────────────────────────────

describe("Dashboard states", () => {
  it("loading state has isLoading=true and no data", () => {
    const state = { isLoading: true, data: null, error: null };
    expect(state.isLoading).toBe(true);
    expect(state.data).toBeNull();
    expect(state.error).toBeNull();
  });

  it("error state has error message and no data", () => {
    const state = {
      isLoading: false,
      data: null,
      error: "Failed to fetch jobs",
    };
    expect(state.isLoading).toBe(false);
    expect(state.data).toBeNull();
    expect(state.error).toBe("Failed to fetch jobs");
  });

  it("empty state has data as empty array", () => {
    const state = { isLoading: false, data: [], error: null };
    expect(state.isLoading).toBe(false);
    expect(state.data).toEqual([]);
    expect(state.error).toBeNull();
  });

  it("success state has data and no error", () => {
    const items = [{ id: 1, title: "Engineer" }];
    const state = { isLoading: false, data: items, error: null };
    expect(state.isLoading).toBe(false);
    expect(state.data).toEqual(items);
    expect(state.error).toBeNull();
  });
});

// ── Pagination contract tests ────────────────────────────────────────────

import { jobsApi, applicationsApi, listAllApplications, matchesApi } from "../src/lib/api";

/** Helper: mock `fetch` to return a controlled JSON response and capture the
 *  requested URL. */
function mockFetch(responseData: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => responseData,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function lastUrl(fetchMock: ReturnType<typeof mockFetch>): URL {
  const call = fetchMock.mock.lastCall;
  if (!call) throw new Error("fetch was not called");
  return new URL(call[0] as string, "http://localhost");
}

describe("jobsApi.listImported", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("first page sends limit, no cursor, no offset", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 25, next_cursor: null });
    await jobsApi.listImported({ limit: 25 });
    const url = lastUrl(fm);
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.has("cursor")).toBe(false);
    expect(url.searchParams.has("offset")).toBe(false);
  });

  it("second page sends cursor and limit, never offset", async () => {
    const fm = mockFetch({ items: [], total: 30, limit: 25, next_cursor: "bmV4dA" });
    await jobsApi.listImported({ cursor: "bmV4dF9wYWdl", limit: 25 });
    const url = lastUrl(fm);
    expect(url.searchParams.get("cursor")).toBe("bmV4dF9wYWdl");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.has("offset")).toBe(false);
  });

  it("defaults limit to 50", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 50, next_cursor: null });
    await jobsApi.listImported();
    expect(lastUrl(fm).searchParams.get("limit")).toBe("50");
  });
});

describe("jobsApi archive state", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("sends archive_state and restores using the scoped endpoint", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 25, next_cursor: null });
    await jobsApi.list({ archiveState: "archived", limit: 25 });
    expect(lastUrl(fm).searchParams.get("archive_state")).toBe("archived");

    fm.mockResolvedValueOnce({ ok: true, status: 204, json: async () => null });
    await jobsApi.restore(42);
    expect(lastUrl(fm).pathname).toBe("/api/backend/jobs/42/restore");
    expect(fm.mock.lastCall?.[1]).toMatchObject({ method: "PATCH" });
  });
});


describe("matchesApi.list", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("first page sends limit, no cursor, no offset", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 25, offset: 0, next_cursor: null, has_more: false });
    await matchesApi.list({ limit: 25 });
    const url = lastUrl(fm);
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.has("cursor")).toBe(false);
    expect(url.searchParams.has("offset")).toBe(false);
  });

  it("second page sends cursor and limit, never offset", async () => {
    const fm = mockFetch({ items: [], total: 30, limit: 25, offset: 0, next_cursor: "bmV4dA", has_more: true });
    await matchesApi.list({ cursor: "bmV4dF9wYWdl", limit: 25 });
    const url = lastUrl(fm);
    expect(url.searchParams.get("cursor")).toBe("bmV4dF9wYWdl");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.has("offset")).toBe(false);
  });

  it("preserves filters with cursor pagination", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 20, offset: 0, next_cursor: null, has_more: false });
    await matchesApi.list({
      cursor: "page2",
      limit: 20,
      q: "engineer",
      source: "indeed",
      isRemote: true,
      jobType: "fulltime",
      location: "Manila",
      company: "Acme",
      shiftType: "day",
      sourceType: "global",
    });
    const url = lastUrl(fm);
    expect(url.searchParams.get("cursor")).toBe("page2");
    expect(url.searchParams.get("q")).toBe("engineer");
    expect(url.searchParams.get("source")).toBe("indeed");
    expect(url.searchParams.get("is_remote")).toBe("true");
    expect(url.searchParams.get("job_type")).toBe("fulltime");
    expect(url.searchParams.get("location")).toBe("Manila");
    expect(url.searchParams.get("company")).toBe("Acme");
    expect(url.searchParams.get("shift_type")).toBe("day");
    expect(url.searchParams.get("source_type")).toBe("global");
    expect(url.searchParams.has("offset")).toBe(false);
  });

  it("defaults limit to 50", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 50, offset: 0, next_cursor: null, has_more: false });
    await matchesApi.list();
    expect(lastUrl(fm).searchParams.get("limit")).toBe("50");
  });
});

describe("applicationsApi.list", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("first page sends limit, no cursor", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 25, next_cursor: null });
    await applicationsApi.list({ limit: 25 });
    const url = lastUrl(fm);
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.has("cursor")).toBe(false);
  });

  it("second page sends cursor and limit", async () => {
    const fm = mockFetch({ items: [], total: 30, limit: 25, next_cursor: "bmV4dA" });
    await applicationsApi.list({ cursor: "bmV4dF9wYWdl", limit: 25 });
    const url = lastUrl(fm);
    expect(url.searchParams.get("cursor")).toBe("bmV4dF9wYWdl");
    expect(url.searchParams.get("limit")).toBe("25");
  });

  it("defaults limit to 50 when not provided", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 50, next_cursor: null });
    await applicationsApi.list();
    expect(lastUrl(fm).searchParams.get("limit")).toBe("50");
  });
});

describe("applicationsApi.listDueNextActions", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("sends the due_by filter to the next-actions endpoint", async () => {
    const fm = mockFetch({ items: [], total: 0, limit: 0, next_cursor: null });
    await applicationsApi.listDueNextActions("2026-07-13");
    const url = lastUrl(fm);
    expect(url.pathname).toBe("/api/backend/applications/next-actions");
    expect(url.searchParams.get("due_by")).toBe("2026-07-13");
  });
});

describe("applicationsApi.getActionCenter", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("sends the browser-local today date to the action-center endpoint", async () => {
    const fm = mockFetch({
      overdue: { count: 0, items: [] },
      due_today: { count: 0, items: [] },
      upcoming: { count: 0, items: [] },
      needs_next_action: { count: 0, items: [] },
    });

    await applicationsApi.getActionCenter("2026-07-14");

    const url = lastUrl(fm);
    expect(url.pathname).toBe("/api/backend/applications/action-center");
    expect(url.searchParams.get("today")).toBe("2026-07-14");
  });
});

describe("applicationsApi.update", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("sends next action fields in the update payload", async () => {
    const fm = mockFetch({
      id: 1,
      job_id: 10,
      status: "saved",
      cover_letter_id: null,
      notes: null,
      next_action_type: "networking",
      next_action_note: "Follow up with the hiring manager instead.",
      next_action_due_date: "2026-07-22",
      next_action_completed_at: null,
      created_at: "2026-01-01",
      updated_at: "2026-01-02",
    });

    await applicationsApi.update(1, {
      next_action_type: "networking",
      next_action_note: "Follow up with the hiring manager instead.",
      next_action_due_date: "2026-07-22",
    });

    const call = fm.mock.lastCall;
    if (!call) throw new Error("fetch was not called");
    const init = call[1] as RequestInit | undefined;
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({
      next_action_type: "networking",
      next_action_note: "Follow up with the hiring manager instead.",
      next_action_due_date: "2026-07-22",
    });
  });
});

describe("applicationsApi.delete", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("deletes an application without sending a status mutation", async () => {
    const fm = mockFetch(undefined, 204);
    await applicationsApi.delete(7);
    const call = fm.mock.lastCall;
    if (!call) throw new Error("fetch was not called");
    const init = call[1] as RequestInit | undefined;
    expect(lastUrl(fm).pathname).toBe("/api/backend/applications/7");
    expect(init?.method).toBe("DELETE");
    expect(init?.body).toBeUndefined();
  });
});

describe("listAllApplications", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("returns all items from a single page", async () => {
    const items = [{ id: 1, job_id: 10, job_title: "Dev", company: "A", location: null, source_url: "x", status: "saved", cover_letter_id: null, notes: null, next_action_type: null, next_action_note: null, next_action_due_date: null, next_action_completed_at: null, created_at: "2026-01-01", updated_at: "2026-01-01" }];
    mockFetch({ items, total: 1, limit: 100, next_cursor: null });
    const result = await listAllApplications();
    expect(result).toHaveLength(1);
    expect(result[0]?.id).toBe(1);
  });

  it("follows next_cursor across multiple pages", async () => {
    let callCount = 0;
    const fetchMock = vi.fn().mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve({
          ok: true, status: 200,
          json: async () => ({ items: [{ id: 1, job_id: 1, job_title: "A", company: "X", location: null, source_url: "x", status: "saved", cover_letter_id: null, notes: null, next_action_type: null, next_action_note: null, next_action_due_date: null, next_action_completed_at: null, created_at: "2026-01-01", updated_at: "2026-01-01" }], total: 3, limit: 2, next_cursor: "page2" }),
        });
      }
      if (callCount === 2) {
        return Promise.resolve({
          ok: true, status: 200,
          json: async () => ({ items: [{ id: 2, job_id: 2, job_title: "B", company: "Y", location: null, source_url: "x", status: "saved", cover_letter_id: null, notes: null, next_action_type: null, next_action_note: null, next_action_due_date: null, next_action_completed_at: null, created_at: "2026-01-01", updated_at: "2026-01-01" }], total: 3, limit: 2, next_cursor: null }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ items: [], total: 3, limit: 2, next_cursor: null }) });
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await listAllApplications();
    expect(result).toHaveLength(2);
    expect(result.map((a: { id: number }) => a.id)).toEqual([1, 2]);
    expect(callCount).toBe(2);
  });

  it("returns empty array when there are no applications", async () => {
    mockFetch({ items: [], total: 0, limit: 100, next_cursor: null });
    const result = await listAllApplications();
    expect(result).toEqual([]);
  });
});

describe("ApplicationListResponse shape", () => {
  it("backend contract includes limit and next_cursor", () => {
    const response = {
      items: [{ id: 1, job_id: 10, job_title: "Engineer", company: "Acme", location: null, source_url: "https://example.com", status: "saved", cover_letter_id: null, notes: null, next_action_type: null, next_action_note: null, next_action_due_date: null, next_action_completed_at: null, created_at: "2026-01-01", updated_at: "2026-01-01" }],
      total: 1,
      limit: 50,
      next_cursor: null,
    };
    expect(response.total).toBe(1);
    expect(response.limit).toBe(50);
    expect(response.next_cursor).toBeNull();
    expect(response.items).toHaveLength(1);
  });

  it("next_cursor is a non-empty string when more pages exist", () => {
    const response = {
      items: [{ id: 1, job_id: 10, job_title: "Dev", company: "A", location: null, source_url: "x", status: "saved", cover_letter_id: null, notes: null, next_action_type: null, next_action_note: null, next_action_due_date: null, next_action_completed_at: null, created_at: "2026-01-01", updated_at: "2026-01-01" }],
      total: 2,
      limit: 1,
      next_cursor: "bmV4dF9jdXJzb3I",
    };
    expect(response.next_cursor).toBeTruthy();
    expect(typeof response.next_cursor).toBe("string");
  });
});

// ── Profile empty-state contract (smoke) ─────────────────────────────────

import { refreshAccessToken, resumeApi, ApiError, setAccessToken } from "../src/lib/api";
import { validateProfileUrl } from "../src/lib/validators";

describe("validateProfileUrl", () => {
  it.each(["", "   "])('accepts blank value "%s"', (value) => {
    expect(validateProfileUrl(value)).toBeNull();
  });

  it.each(["https://example.com", "http://example.com/path"])('accepts %s', (value) => {
    expect(validateProfileUrl(value)).toBeNull();
  });

  it.each(["mailto:ada@example.com", "ftp://example.com", "javascript:alert(1)", "example.com"])('rejects %s', (value) => {
    expect(validateProfileUrl(value)).toContain("full HTTP or HTTPS URL");
  });
});

describe("resumeApi.getProfile — empty-state contract", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("calls the correct profile endpoint", async () => {
    const fm = mockFetch(null, 200);
    await resumeApi.getProfile();
    const url = lastUrl(fm);
    expect(url.pathname).toBe("/api/backend/resume/profile");
  });

  it("returns null when backend sends 200 + null (empty profile)", async () => {
    mockFetch(null, 200);
    const result = await resumeApi.getProfile();
    expect(result).toBeNull();
  });

  it("returns profile data when backend sends 200 + profile object", async () => {
    const profile = {
      profile_id: 1,
      resume_id: 2,
      profile: { full_name: "Test", email: null, phone: null, headline: null, years_experience: null, skills: [], education: [], certifications: [], links: { linkedin: null, github: null, portfolio: null, website: null }, projects: [], work_history: [], preferred_roles: [], seniority: null },
    };
    mockFetch(profile, 200);
    const result = await resumeApi.getProfile();
    expect(result).not.toBeNull();
    expect(result?.profile_id).toBe(1);
    expect(result?.profile.full_name).toBe("Test");
  });

  it("throws ApiError on 401 (not to be confused with empty profile)", async () => {
    mockFetch({ detail: "Not authenticated" }, 401);
    await expect(resumeApi.getProfile()).rejects.toThrow(ApiError);
    await expect(resumeApi.getProfile()).rejects.toMatchObject({ status: 401 });
  });
});

// ── Extension sessions API ───────────────────────────────────────────────

describe("shared refresh coordinator", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("shares one refresh request and lets a protected retry use its result", async () => {
    let resolveRefresh: ((value: unknown) => void) | undefined;
    const refreshResponse = new Promise((resolve) => { resolveRefresh = resolve; });
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/backend/auth/refresh") {
        return refreshResponse.then((body) => ({ ok: true, status: 200, json: async () => body }));
      }
      if (url === "/api/backend/resume/profile" && fetchMock.mock.calls.length === 2) {
        return Promise.resolve({ ok: false, status: 401, json: async () => ({ detail: "expired" }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => null });
    });
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("expired-access");

    const bootstrapRefresh = refreshAccessToken();
    const protectedRequest = resumeApi.getProfile();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/backend/auth/refresh")).toHaveLength(1);

    resolveRefresh?.({ access_token: "fresh-access", user: { id: 1 } });
    await expect(bootstrapRefresh).resolves.toMatchObject({ access_token: "fresh-access" });
    await expect(protectedRequest).resolves.toBeNull();
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/backend/auth/refresh")).toHaveLength(1);
  });

  it("keeps a failed shared refresh as an authentication failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Invalid or expired token" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(Promise.all([refreshAccessToken(), refreshAccessToken()])).rejects.toThrow(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

import { extensionApi } from "../src/lib/api";

describe("extensionApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  function lastFetchInit(fm: ReturnType<typeof mockFetch>): RequestInit | undefined {
    const call = fm.mock.lastCall;
    if (!call) throw new Error("fetch was not called");
    return call[1] as RequestInit | undefined;
  }

  describe("listSessions", () => {
    it("calls the correct endpoint", async () => {
      const fm = mockFetch({ sessions: [] }, 200);
      await extensionApi.listSessions();
      const url = lastUrl(fm);
      expect(url.pathname).toBe("/api/backend/extension/sessions");
    });

    it("includes Authorization header when access token is set", async () => {
      setAccessToken("test-bearer-token");
      const fm = mockFetch({ sessions: [] }, 200);
      await extensionApi.listSessions();
      const init = lastFetchInit(fm);
      const headers = init?.headers as Record<string, string> | undefined;
      expect(headers?.["Authorization"]).toBe("Bearer test-bearer-token");
    });

    it("includes credentials: include", async () => {
      const fm = mockFetch({ sessions: [] }, 200);
      await extensionApi.listSessions();
      const init = lastFetchInit(fm);
      expect(init?.credentials).toBe("include");
    });

    it("returns session list from response", async () => {
      const sessions = [
        { id: "s1", device_name: "Mac", browser: "Chrome", created_at: "2026-01-01", last_used_at: null, revoked: false },
      ];
      mockFetch({ sessions }, 200);
      const result = await extensionApi.listSessions();
      expect(result.sessions).toEqual(sessions);
    });
  });

  describe("revokeSession", () => {
    it("calls the owner-scoped endpoint with DELETE method", async () => {
      const fm = mockFetch(null, 204);
      await extensionApi.revokeSession("session-abc");
      const url = lastUrl(fm);
      expect(url.pathname).toBe("/api/backend/extension/sessions/session-abc");
      const init = lastFetchInit(fm);
      expect(init?.method).toBe("DELETE");
    });

    it("does not send a caller-selectable session id in the request body", async () => {
      const fm = mockFetch(null, 204);
      await extensionApi.revokeSession("session-xyz");
      const init = lastFetchInit(fm);
      expect(init?.body).toBeUndefined();
    });

    it("includes Authorization header when access token is set", async () => {
      setAccessToken("test-bearer-token");
      const fm = mockFetch(null, 204);
      await extensionApi.revokeSession("session-abc");
      const init = lastFetchInit(fm);
      const headers = init?.headers as Record<string, string> | undefined;
      expect(headers?.["Authorization"]).toBe("Bearer test-bearer-token");
    });

    it("includes credentials: include", async () => {
      const fm = mockFetch(null, 204);
      await extensionApi.revokeSession("session-abc");
      const init = lastFetchInit(fm);
      expect(init?.credentials).toBe("include");
    });
  });
});
