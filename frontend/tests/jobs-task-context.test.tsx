import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JobsTaskProvider, useJobsTask } from "@/context/jobs-task-context";

const apiMocks = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  return { getTaskStatus: vi.fn(), ApiError: MockApiError };
});
const authMocks = vi.hoisted(() => ({ status: "authenticated" as "loading" | "authenticated" }));
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
const routerMocks = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("@/lib/api", () => ({ jobsApi: apiMocks, ApiError: apiMocks.ApiError }));
vi.mock("@/context/auth-context", () => ({ useAuth: () => ({ status: authMocks.status }) }));
vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMocks }));

function Harness() {
  const { setTask } = useJobsTask();
  return <button onClick={() => setTask("job-task-1", ["linkedin"])}>start</button>;
}

describe("jobs task recovery", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    apiMocks.getTaskStatus.mockReset();
    authMocks.status = "authenticated";
    toastMocks.success.mockReset();
    toastMocks.warning.mockReset();
    toastMocks.error.mockReset();
    routerMocks.push.mockReset();
    sessionStorage.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    sessionStorage.clear();
  });

  it("does not poll a restored task while auth is loading", async () => {
    sessionStorage.setItem("jobs_task_id", "restored-job");
    sessionStorage.setItem("jobs_task_sources", JSON.stringify(["linkedin"]));
    authMocks.status = "loading";
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><JobsTaskProvider><Harness /></JobsTaskProvider></QueryClientProvider>);
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(apiMocks.getTaskStatus).not.toHaveBeenCalled();
  });

  it("clears a stale restored task and shows one recovery toast", async () => {
    sessionStorage.setItem("jobs_task_id", "stale-job");
    sessionStorage.setItem("jobs_task_sources", JSON.stringify(["linkedin"]));
    apiMocks.getTaskStatus.mockRejectedValue(new apiMocks.ApiError("Task not found", 404));
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><JobsTaskProvider><Harness /></JobsTaskProvider></QueryClientProvider>);
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
    expect(sessionStorage.getItem("jobs_task_id")).toBeNull();
    expect(sessionStorage.getItem("jobs_task_sources")).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledTimes(1);
    expect(toastMocks.error).toHaveBeenCalledWith(
      "Job refresh status is no longer available. Start a new refresh.",
      expect.objectContaining({ action: expect.any(Object) }),
    );
  });
});
