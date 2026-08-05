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
    getDashboardSummary: vi.fn(),
    getActionCenter: vi.fn(),
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
  let summaryState: {
    data: unknown;
    isLoading: boolean;
    isError: boolean;
    isFetching?: boolean;
    isPending?: boolean;
  };

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
    apiMocks.applicationsApi.getDashboardSummary.mockReset();
    apiMocks.applicationsApi.getActionCenter.mockReset();
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
    summaryState = {
      data: {
        total_applications: 61,
        counts_by_status: {
          saved: 56,
          applied: 1,
          interview: 1,
          offer: 1,
          rejected: 1,
          closed: 1,
        },
        active_pipeline_count: 58,
        offer_count: 1,
        offer_rate_percent: 2,
      },
      isLoading: false,
      isError: false,
    };
    reactQueryMocks.useQuery.mockImplementation((options: any) => {
      queryCalls.push({
        queryKey: options.queryKey,
        queryFn: options.queryFn,
      });

      const key = Array.isArray(options.queryKey)
        ? options.queryKey[0]
        : options.queryKey;

      if (key === "dashboard-action-center") {
        void options.queryFn();
        return {
          data: {
            overdue: { count: 1, items: [
              {
                application_id: 11,
                job_id: 201,
                job_title: "Senior Backend Engineer",
                company: "Beta",
                application_status: "applied",
                next_action_type: "follow_up",
                next_action_note: "Ping recruiter after panel review.",
                next_action_due_date: "2026-07-13",
                updated_at: "2026-07-13T08:00:00Z",
              } ] },
            due_today: { count: 1, items: [
              {
                application_id: 12,
                job_id: 202,
                job_title: "Platform Engineer",
                company: "Acme",
                application_status: "saved",
                next_action_type: "prepare_interview",
                next_action_note: "Review system design notes.",
                next_action_due_date: "2026-07-14",
                updated_at: "2026-07-13T09:00:00Z",
              } ] },
            upcoming: { count: 1, items: [{
              application_id: 13,
              job_id: 203,
              job_title: "Data Platform Engineer",
              company: "Gamma",
              application_status: "interview",
              next_action_type: "prepare_interview",
              next_action_note: null,
              next_action_due_date: "2026-07-21",
              updated_at: "2026-07-13T10:00:00Z",
            }] },
            needs_next_action: { count: 1, items: [{
              application_id: 14,
              job_id: 204,
              job_title: "Product Engineer",
              company: "Delta",
              application_status: "saved",
              updated_at: "2026-07-13T11:00:00Z",
            }] },
          },
          isLoading: false,
          isError: false,
          refetch: vi.fn(),
        };
      }

      if (key === "dashboard-summary") {
        void options.queryFn();
        return {
          ...summaryState,
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
      (call) => Array.isArray(call.queryKey) && call.queryKey[0] === "dashboard-action-center",
    );
    expect(nextActionsQuery?.queryKey).toEqual(["dashboard-action-center", "2026-07-14"]);
    expect(apiMocks.applicationsApi.getActionCenter).toHaveBeenCalledWith(
      "2026-07-14",
    );
    expect(apiMocks.applicationsApi.getDashboardSummary).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("58");
    expect(container.textContent).toContain("2%");

    expect(container.textContent).toContain(
      "User-managed actions that need attention today and this week.",
    );
    expect(container.textContent).toContain("Senior Backend Engineer");
    expect(container.textContent).toContain("Platform Engineer");
    expect(container.textContent).toContain("Overdue");
    expect(container.textContent).toContain("Due today");

    const buttons = Array.from(container.querySelectorAll("button")).filter(
      (button) => button.textContent?.includes("Complete"),
    );
    expect(buttons).toHaveLength(3);
    expect(container.textContent).toContain("Upcoming");
    expect(container.textContent).toContain("Needs a next action");
    expect(container.querySelector('a[href="/dashboard/applications?next_action_application_id=14"]')).not.toBeNull();

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
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["dashboard-action-center"],
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["dashboard-summary"],
    });
  });

  it("shows summary retry state without hiding independent dashboard areas", async () => {
    summaryState = { data: null, isLoading: false, isError: true };

    await act(async () => {
      root.render(<DashboardHome />);
    });

    expect(container.textContent).toContain("load application summary.");
    expect(container.textContent).toContain("Senior Backend Engineer");
    expect(container.textContent).toContain("Jobs in feed");
    expect(container.textContent).toContain("Ranked matches");
    expect(container.textContent).not.toContain("No applications yet.");
    expect(container.textContent).not.toContain("0%");
    expect(container.textContent).not.toContain("58");
  });

  it("keeps unavailable metrics out of the UI while the summary is loading or retrying", async () => {
    summaryState = {
      data: null,
      isLoading: false,
      isError: true,
      isFetching: true,
      isPending: true,
    };

    await act(async () => {
      root.render(<DashboardHome />);
    });

    expect(container.textContent).toContain("Loading");
    expect(container.textContent).not.toContain("No applications yet.");
    expect(container.textContent).not.toContain("0%");
    expect(container.textContent).toContain("Jobs in feed");
    expect(container.textContent).toContain("Ranked matches");
  });

  it("shows legitimate zero metrics for a successful empty summary", async () => {
    summaryState = {
      data: {
        total_applications: 0,
        counts_by_status: {
          saved: 0,
          applied: 0,
          interview: 0,
          offer: 0,
          rejected: 0,
          closed: 0,
        },
        active_pipeline_count: 0,
        offer_count: 0,
        offer_rate_percent: 0,
      },
      isLoading: false,
      isError: false,
    };

    await act(async () => {
      root.render(<DashboardHome />);
    });

    expect(container.textContent).toContain("No applications yet.");
    expect(container.textContent).toContain("0%");
  });

  it("restores populated metrics after a summary retry succeeds", async () => {
    summaryState = { data: null, isLoading: false, isError: true };

    await act(async () => {
      root.render(<DashboardHome />);
    });
    expect(container.textContent).not.toContain("58");

    summaryState = {
      data: {
        total_applications: 61,
        counts_by_status: {
          saved: 56,
          applied: 1,
          interview: 1,
          offer: 1,
          rejected: 1,
          closed: 1,
        },
        active_pipeline_count: 58,
        offer_count: 1,
        offer_rate_percent: 2,
      },
      isLoading: false,
      isError: false,
    };

    await act(async () => {
      root.render(<DashboardHome />);
    });

    expect(container.textContent).toContain("58");
    expect(container.textContent).toContain("2%");
    expect(container.textContent).not.toContain("load application summary.");
  });
});
