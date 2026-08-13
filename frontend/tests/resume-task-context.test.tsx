import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResumeTaskProvider, useResumeTask } from "@/context/resume-task-context";

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

vi.mock("@/lib/api", () => ({ resumeApi: apiMocks, ApiError: apiMocks.ApiError }));
vi.mock("@/context/auth-context", () => ({ useAuth: () => ({ status: authMocks.status }) }));
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
    toastMocks.warning.mockReset();
    toastMocks.error.mockReset();
    routerMocks.push.mockReset();
    authMocks.status = "authenticated";
    sessionStorage.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    sessionStorage.clear();
  });

  it("waits for authentication before polling a restored task", async () => {
    sessionStorage.setItem("resume_task_id", "restored-task");
    sessionStorage.setItem("resume_task_name", "restored.pdf");
    authMocks.status = "loading";
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ResumeTaskProvider><Harness /></ResumeTaskProvider>
        </QueryClientProvider>,
      );
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(apiMocks.getTaskStatus).not.toHaveBeenCalled();

    authMocks.status = "authenticated";
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ResumeTaskProvider><Harness /></ResumeTaskProvider>
        </QueryClientProvider>,
      );
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(apiMocks.getTaskStatus).toHaveBeenCalledWith("restored-task");
  });

  it("clears a stale restored task and shows one recovery toast", async () => {
    sessionStorage.setItem("resume_task_id", "stale-task");
    sessionStorage.setItem("resume_task_name", "stale.pdf");
    apiMocks.getTaskStatus.mockRejectedValue(new apiMocks.ApiError("Task not found", 404));
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ResumeTaskProvider><Harness /></ResumeTaskProvider>
        </QueryClientProvider>,
      );
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
    expect(sessionStorage.getItem("resume_task_id")).toBeNull();
    expect(sessionStorage.getItem("resume_task_name")).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledTimes(1);
    expect(toastMocks.error).toHaveBeenCalledWith(
      "Upload status is no longer available. Please upload the résumé again.",
      expect.objectContaining({ action: expect.any(Object) }),
    );
    const calls = apiMocks.getTaskStatus.mock.calls.length;
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
    expect(apiMocks.getTaskStatus).toHaveBeenCalledTimes(calls);
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
    expect(toastMocks.warning).not.toHaveBeenCalled();
    expect(queryClient.getQueryCache().find({ queryKey: ["profile"] })).toBeUndefined();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["resume-imports"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["profile"] });
    const draftToast = toastMocks.success.mock.calls.at(-1)?.[1] as { action?: { onClick: () => void } };
    draftToast.action?.onClick();
    expect(routerMocks.push).toHaveBeenCalledWith("/dashboard/settings/profile");
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
    expect(toastMocks.warning).not.toHaveBeenCalled();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["profile"] });
  });

  it("shows warning messaging without changing active import behavior", async () => {
    apiMocks.getTaskStatus.mockResolvedValueOnce({
      type: "resume_parse",
      status: "done",
      message: "done",
      result: {
        resume_id: 6,
        filename: "short.pdf",
        is_active: true,
        lifecycle_status: "active",
        extraction_warnings: [
          {
            code: "short_extracted_text",
            message: "Review the imported profile for missing text.",
          },
        ],
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

    expect(toastMocks.warning).toHaveBeenCalledWith(
      "Review the imported profile for missing text.",
      { duration: 8000 },
    );
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["profile"] });
  });

  it("shows scanned-document limitations while preserving draft review routing", async () => {
    apiMocks.getTaskStatus.mockResolvedValueOnce({
      type: "resume_parse",
      status: "done",
      message: "done",
      result: {
        resume_id: 7,
        filename: "scanned.pdf",
        is_active: false,
        lifecycle_status: "draft",
        extraction_warnings: [
          {
            code: "likely_scanned_pdf",
            message: "OCR is not available; review the imported profile for missing text.",
          },
        ],
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

    expect(toastMocks.warning).toHaveBeenCalledWith(
      "OCR is not available; review the imported profile for missing text.",
      { duration: 8000 },
    );
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["resume-imports"] });
    const draftToast = toastMocks.success.mock.calls.at(-1)?.[1] as { action?: { onClick: () => void } };
    draftToast.action?.onClick();
    expect(routerMocks.push).toHaveBeenCalledWith("/dashboard/settings/profile");
  });

  it("shows completeness warnings while preserving active-profile routing", async () => {
    apiMocks.getTaskStatus.mockResolvedValueOnce({
      type: "resume_parse",
      status: "done",
      message: "done",
      result: {
        resume_id: 8,
        filename: "incomplete.pdf",
        is_active: true,
        lifecycle_status: "active",
        extraction_warnings: [
          {
            code: "source_email_not_parsed",
            message: "Review the contact details.",
          },
        ],
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

    expect(toastMocks.warning).toHaveBeenCalledWith(
      "Review the contact details.",
      { duration: 8000 },
    );
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["profile"] });
  });
});
