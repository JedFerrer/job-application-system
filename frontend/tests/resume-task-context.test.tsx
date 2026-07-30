import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResumeTaskProvider, useResumeTask } from "@/context/resume-task-context";

const apiMocks = vi.hoisted(() => ({ getTaskStatus: vi.fn() }));
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
const routerMocks = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("@/lib/api", () => ({ resumeApi: apiMocks }));
vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMocks }));

function Harness() {
  const { setTask } = useResumeTask();
  return <button onClick={() => setTask("task-1", "replacement.pdf")}>start</button>;
}

describe("resume task completion messaging", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    apiMocks.getTaskStatus.mockResolvedValue({
      type: "resume_parse",
      status: "done",
      message: "done",
      result: {
        resume_id: 4,
        filename: "replacement.pdf",
        is_active: false,
        lifecycle_status: "draft",
        profile: {},
      },
    });
    toastMocks.success.mockReset();
    routerMocks.push.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
  });

  it("explains that replacement imports are drafts", async () => {
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ResumeTaskProvider><Harness /></ResumeTaskProvider>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      (container.querySelector("button") as HTMLButtonElement).click();
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });

    expect(toastMocks.success).toHaveBeenCalledWith(
      'Résumé parsed — "replacement.pdf"',
      expect.objectContaining({
        description: "Saved as a draft awaiting review. Your active profile was not changed.",
      }),
    );
    expect(queryClient.getQueryCache().find({ queryKey: ["profile"] })).toBeUndefined();
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("keeps the active-import message and invalidates the profile", async () => {
    apiMocks.getTaskStatus.mockResolvedValueOnce({
      type: "resume_parse",
      status: "done",
      message: "done",
      result: {
        resume_id: 5,
        filename: "first.pdf",
        is_active: true,
        lifecycle_status: "active",
        profile: {},
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ResumeTaskProvider><Harness /></ResumeTaskProvider>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      (container.querySelector("button") as HTMLButtonElement).click();
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });

    expect(toastMocks.success).toHaveBeenCalledWith(
      'Résumé parsed — "first.pdf"',
      expect.objectContaining({ description: "Your profile has been updated." }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["profile"] });
  });
});
