import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MatchesPanel } from "@/components/matches/matches-panel";

const api = vi.hoisted(() => ({
  matchesApi: { rank: vi.fn(), getRankTaskStatus: vi.fn(), list: vi.fn() },
  applicationsApi: { create: vi.fn(), delete: vi.fn() },
  jobsApi: { archive: vi.fn() },
  listAllApplications: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;

    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), message: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/components/matches/skills-gap-summary", () => ({ SkillsGapSummary: () => null }));

const emptyMatches = {
  items: [], total: 0, limit: 20, offset: 0, next_cursor: null, has_more: false,
};

function matchItem(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 1,
    title: "Backend Engineer",
    company: "Acme",
    location: "Remote",
    is_remote: true,
    job_type: "fulltime",
    description: "Build APIs",
    shift_type: null,
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    source: "test",
    source_url: "https://example.com/job/1",
    company_logo: null,
    vector_score: 80,
    confidence: "weak",
    llm_score: null,
    llm_confidence: null,
    matched_skills: ["python"],
    readiness: { decision: "apply", reasons: [], caveats: [], deterministic_blockers: [], deterministic_warnings: [], decision_basis: "score" },
    posted_at: null,
    ...overrides,
  };
}

async function flushReact() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

describe("MatchesPanel ranking task", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    sessionStorage.clear();
    class Observer { observe() {} disconnect() {} }
    Object.defineProperty(window, "IntersectionObserver", { value: Observer, configurable: true });
    api.matchesApi.list.mockResolvedValue(emptyMatches);
    api.listAllApplications.mockResolvedValue([]);
    api.matchesApi.rank.mockReset();
    api.matchesApi.getRankTaskStatus.mockReset();
    toast.success.mockReset(); toast.error.mockReset(); toast.message.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => root.render(<QueryClientProvider client={client}><MatchesPanel /></QueryClientProvider>));
  }

  it("queues one refresh, persists its task, and reloads matches when complete", async () => {
    api.matchesApi.rank.mockResolvedValue({ task_id: "rank-1", state: "queued" });
    api.matchesApi.getRankTaskStatus.mockResolvedValue({ type: "match_ranking", status: "done", message: "done" });
    render();

    await flushReact();
    const button = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("Refresh Match Rankings"),
    ) as HTMLButtonElement;
    await act(async () => { button.click(); });
    await flushReact();
    await flushReact();

    expect(api.matchesApi.rank).toHaveBeenCalledTimes(1);
    expect(api.matchesApi.getRankTaskStatus).toHaveBeenCalledWith("rank-1");
    expect(sessionStorage.getItem("match_ranking_task_id")).toBeNull();
    expect(toast.success).toHaveBeenCalledWith("Match rankings refreshed");
  });

  it("restores and polls an active task after reload", async () => {
    sessionStorage.setItem("match_ranking_task_id", "rank-reloaded");
    api.matchesApi.getRankTaskStatus.mockResolvedValue({ type: "match_ranking", status: "running", message: "Refreshing" });
    render();

    await flushReact();
    await flushReact();
    expect(api.matchesApi.getRankTaskStatus).toHaveBeenCalledWith("rank-reloaded");
    expect(container.textContent).toContain("Refreshing");
  });

  it("shows cooldown feedback and clears an expired restored task", async () => {
    sessionStorage.setItem("match_ranking_task_id", "expired-task");
    api.matchesApi.getRankTaskStatus.mockRejectedValue(new api.ApiError("Task not found", 404));
    render();

    await flushReact();
    await flushReact();
    expect(sessionStorage.getItem("match_ranking_task_id")).toBeNull();

    api.matchesApi.rank.mockResolvedValue({ task_id: "recent-task", state: "cooldown", retry_after_seconds: 120 });
    api.matchesApi.getRankTaskStatus.mockResolvedValue({ type: "match_ranking", status: "done", message: "done" });
    const button = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("Refresh Match Rankings"),
    ) as HTMLButtonElement;
    await act(async () => { button.click(); });
    await flushReact();

    expect(toast.message).toHaveBeenCalledWith("Rankings were refreshed recently. Try again in 2 minutes.");
  });

  it("shows vector relevance and deterministic fit separately", async () => {
    api.matchesApi.list.mockResolvedValue({
      items: [matchItem({
        fit_score: 68.4,
        llm_score: 95,
        llm_confidence: 0.9,
        evidence_confidence: "high",
        score_components: {
          required_skill_coverage: { score: 100, weight: 35, status: "known", reason: "matched_required_coverage" },
        },
      })],
      total: 1,
      limit: 20,
      offset: 0,
      next_cursor: null,
      has_more: false,
    });
    render();

    await flushReact();
    await flushReact();

    expect(container.textContent).toContain("80%Relevance");
    expect(container.textContent).toContain("Deterministic fit · 68%");
    expect(container.textContent).not.toContain("High evidence");
    expect(container.textContent).not.toContain("Fit components");
  });

  it("renders a known zero deterministic fit without replacing vector relevance", async () => {
    api.matchesApi.list.mockResolvedValue({ ...emptyMatches, items: [matchItem({ fit_score: 0 })], total: 1 });
    render();
    await flushReact();
    await flushReact();
    expect(container.textContent).toContain("80%Relevance");
    expect(container.textContent).toContain("Deterministic fit · 0%");
  });

  it("omits deterministic fit when it is null or omitted and preserves input order", async () => {
    api.matchesApi.list.mockResolvedValue({
      ...emptyMatches,
      items: [
        matchItem({ job_id: 2, title: "Second", vector_score: 40, fit_score: null, confidence: null, matched_skills: [], readiness: null }),
        matchItem({ job_id: 1, title: "First", vector_score: 90, confidence: null, matched_skills: [], readiness: null }),
      ],
      total: 2,
    });
    render();
    await flushReact();
    await flushReact();
    expect(container.textContent).toContain("40%Relevance");
    expect(container.textContent).toContain("90%Relevance");
    expect(container.textContent?.indexOf("Second")).toBeLessThan(container.textContent?.indexOf("First") ?? -1);
    expect(container.textContent).not.toContain("Deterministic fit");
  });
});
