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
vi.mock("@/components/jobs/job-card", () => ({ JobCard: () => null }));

const emptyMatches = {
  items: [], total: 0, limit: 20, offset: 0, next_cursor: null, has_more: false,
};

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
});
