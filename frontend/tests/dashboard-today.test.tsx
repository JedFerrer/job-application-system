import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import DashboardHome from "../src/app/dashboard/page";

const reactQueryMocks = vi.hoisted(() => {
  const useQuery = vi.fn();
  const useMutation = vi.fn();
  const useQueryClient = vi.fn();
  return { useQuery, useMutation, useQueryClient };
});

const apiMocks = vi.hoisted(() => {
  const applicationsApi = {
    list: vi.fn(),
    listDueNextActions: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  };
  const jobsApi = {
    list: vi.fn(),
  };
  const matchesApi = {
    list: vi.fn(),
  };
  class ApiError extends Error {
    status: number;
    detail: unknown;

    constructor(message: string, status: number, detail: unknown) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.detail = detail;
    }
  }
  return { applicationsApi, jobsApi, matchesApi, ApiError };
});

const toastMocks = vi.hoisted(() => {
  const success = vi.fn();
  const error = vi.fn();
  return { success, error };
});

vi.mock("@tanstack/react-query", () => ({
  useQuery: reactQueryMocks.useQuery,
  useMutation: reactQueryMocks.useMutation,
  useQueryClient: reactQueryMocks.useQueryClient,
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastMocks.success,
    error: toastMocks.error,
  },
}));

vi.mock("../src/lib/api", () => apiMocks);

describe("Dashboard Today section", () => {
  let container: HTMLDivElement;
  let root: Root;
  let invalidateQueries: ReturnType<typeof vi.fn>;
  let queryCalls: Array<{ queryKey: unknown; queryFn: () => unknown }> = [];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T12:00:00Z"));

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    invalidateQueries = vi.fn();

    reactQueryMocks.useQuery.mockReset();
    reactQueryMocks.useMutation.mockReset();
    reactQueryMocks.useQueryClient.mockReset();
    apiMocks.applicationsApi.update.mockReset();
    apiMocks.applicationsApi.list.mockReset();
    apiMocks.applicationsApi.listDueNextActions.mockReset();
    apiMocks.jobsApi.list.mockReset();
    apiMocks.matchesApi.list.mockReset();
    toastMocks.success.mockReset();
    toastMocks.error.mockReset();
    apiMocks.applicationsApi.update.mockReturnValue(
      Promise.resolve({
        id: 11,
        status: "applied",
        cover_letter_id: null,
        notes: null,
        next_action_type: "follow_up",
        next_action_note: "Ping recruiter after panel review.",
        next_action_due_date: "2026-07-13",
        next_action_completed_at: "2026-07-14T12:00:00.000Z",
        created_at: "2026-07-10T00:00:00Z",
        updated_at: "2026-07-14T12:00:00.000Z",
      }),
    );

    reactQueryMocks.useQueryClient.mockReturnValue({
      invalidateQueries,
    });

    reactQueryMocks.useMutation.mockImplementation((config: {
      mutationFn: (arg: number) => Promise<unknown>;
      onSuccess?: (data: unknown) => void;
      onError?: (error: unknown) => void;
    }) => ({
      mutate: (arg: number) => {
        void config
          .mutationFn(arg)
          .then((data) => config.onSuccess?.(data))
          .catch((error) => config.onError?.(error));
      },
      isPending: false,
      variables: undefined,
    }));

    queryCalls = [];
    reactQueryMocks.useQuery.mockImplementation((options: any) => {
      queryCalls.push({
        queryKey: options.queryKey,
        queryFn: options.queryFn,
      });

      const key = Array.isArray(options.queryKey)
        ? options.queryKey[0]
        : options.queryKey;

      if (key === "home-next-actions") {
        void options.queryFn();
        return {
          data: {
            items: [
              {
                id: 11,
                job_id: 201,
                job_title: "Senior Backend Engineer",
                company: "Beta",
                location: "Remote",
                source_url: "https://example.com/jobs/201",
                status: "applied",
                cover_letter_id: null,
                notes: null,
                next_action_type: "follow_up",
                next_action_note: "Ping recruiter after panel review.",
                next_action_due_date: "2026-07-13",
                next_action_completed_at: null,
                created_at: "2026-07-10T00:00:00Z",
                updated_at: "2026-07-13T08:00:00Z",
              },
              {
                id: 12,
                job_id: 202,
                job_title: "Platform Engineer",
                company: "Acme",
                location: "Remote",
                source_url: "https://example.com/jobs/202",
                status: "saved",
                cover_letter_id: null,
                notes: null,
                next_action_type: "prepare_interview",
                next_action_note: "Review system design notes.",
                next_action_due_date: "2026-07-14",
                next_action_completed_at: null,
                created_at: "2026-07-10T00:00:00Z",
                updated_at: "2026-07-13T09:00:00Z",
              },
            ],
            total: 2,
            limit: 2,
            next_cursor: null,
          },
          isLoading: false,
          isError: false,
          refetch: vi.fn(),
        };
      }

      if (key === "home-applications") {
        return {
          data: {
            items: [{ id: 1, job_id: 101, status: "saved" }],
          },
          isLoading: false,
          isError: false,
          refetch: vi.fn(),
        };
      }

      if (key === "home-matches") {
        return {
          data: { total: 3 },
          isLoading: false,
          isError: false,
          refetch: vi.fn(),
        };
      }

      if (key === "home-jobs") {
        return {
          data: { total: 7 },
          isLoading: false,
          isError: false,
          refetch: vi.fn(),
        };
      }

      return {
        data: null,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      };
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it("renders the Today section and completes a next action from the dashboard", async () => {
    await act(async () => {
      root.render(<DashboardHome />);
    });

    const nextActionsQuery = queryCalls.find(
      (call) => Array.isArray(call.queryKey) && call.queryKey[0] === "home-next-actions",
    );
    expect(nextActionsQuery?.queryKey).toEqual(["home-next-actions", "2026-07-14"]);
    expect(apiMocks.applicationsApi.listDueNextActions).toHaveBeenCalledWith(
      "2026-07-14",
    );

    expect(container.textContent).toContain(
      "User-managed next actions due today or overdue.",
    );
    expect(container.textContent).toContain("Senior Backend Engineer");
    expect(container.textContent).toContain("Platform Engineer");
    expect(container.textContent).toContain("Overdue");
    expect(container.textContent).toContain("Due today");

    const buttons = Array.from(container.querySelectorAll("button")).filter(
      (button) => button.textContent?.includes("Complete"),
    );
    expect(buttons).toHaveLength(2);

    await act(async () => {
      buttons[0]?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.update).toHaveBeenCalledWith(11, {
      next_action_completed_at: "2026-07-14T12:00:00.000Z",
    });
    expect(toastMocks.success).toHaveBeenCalledWith(
      "Marked next action complete",
    );
  });
});
