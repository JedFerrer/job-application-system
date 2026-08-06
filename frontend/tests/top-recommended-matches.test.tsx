import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TopRecommendedMatches } from "../src/components/dashboard/top-recommended-matches";

const queryMocks = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  useQueryClient: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  matchesApi: { recommendations: vi.fn() },
  applicationsApi: { create: vi.fn() },
}));

vi.mock("@tanstack/react-query", () => queryMocks);
vi.mock("../src/lib/api", () => apiMocks);
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("TopRecommendedMatches", () => {
  let container: HTMLDivElement;
  let root: Root;
  let invalidateQueries: ReturnType<typeof vi.fn>;
  let queryState: {
    data: unknown;
    isLoading: boolean;
    isError: boolean;
    refetch: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    invalidateQueries = vi.fn();
    queryMocks.useQueryClient.mockReturnValue({ invalidateQueries });
    queryMocks.useMutation.mockImplementation((config: {
      mutationFn: (jobId: number) => Promise<unknown>;
      onSuccess?: (data: unknown) => void;
      onError?: (error: unknown) => void;
    }) => ({
      mutate: (jobId: number) =>
        void config.mutationFn(jobId)
          .then((data) => config.onSuccess?.(data))
          .catch((error) => config.onError?.(error)),
      isPending: false,
      variables: undefined,
    }));
    queryState = {
      data: {
        items: [
          {
            job_id: 11,
            title: "Backend Engineer",
            company: "Acme",
            location: "Remote",
            remote: true,
            source: "test",
            source_url: "https://example.test/11",
            score: 91,
            confidence: "strong",
            readiness: "apply",
            matched_skills: ["Python", "FastAPI", "Postgres", "Docker"],
            missing_skill: "Kubernetes",
            application_status: null,
          },
          {
            job_id: 12,
            title: "Platform Engineer",
            company: "Beta",
            location: null,
            remote: false,
            source: "test",
            source_url: "https://example.test/12",
            score: 72,
            confidence: "moderate",
            readiness: "save",
            matched_skills: [],
            missing_skill: null,
            application_status: "applied",
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    };
    queryMocks.useQuery.mockImplementation(() => queryState);
    apiMocks.matchesApi.recommendations.mockReset();
    apiMocks.applicationsApi.create.mockReset();
    apiMocks.applicationsApi.create.mockResolvedValue({ status: "saved" });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function render() {
    await act(async () => root.render(<TopRecommendedMatches />));
  }

  it("renders ordered rows, capped skills, tracked state, and links", async () => {
    await render();
    expect(container.textContent).toContain("Top Recommended Matches");
    expect(container.textContent).toContain("Backend Engineer");
    expect(container.textContent).toContain("Missing: Kubernetes");
    expect(container.textContent).not.toContain("Docker");
    expect(container.textContent).toContain("Applied");
    expect(container.querySelector('[aria-label="Recommended matches"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="recommendation-fit-11"]')?.className).toContain(
      "border-emerald-300",
    );
    expect(container.querySelector('a[href="/dashboard/matches"]')).not.toBeNull();
    expect(container.querySelector('a[href="/jobs/11"]')).not.toBeNull();
    expect(container.querySelector('button[aria-label="Save Platform Engineer"]')).toBeNull();
  });

  it("renders loading, error retry, and empty states", async () => {
    queryState = { ...queryState, data: null, isLoading: true };
    await render();
    expect(container.textContent).toContain("Loading recommendations");

    queryState = { ...queryState, isLoading: false, isError: true };
    await render();
    expect(container.textContent).toContain("Couldn’t load recommendations");
    const retry = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Retry");
    expect(retry).not.toBeUndefined();
    await act(async () => retry?.click());
    expect(queryState.refetch).toHaveBeenCalled();

    queryState = { ...queryState, isError: false, data: { items: [] } };
    await render();
    expect(container.textContent).toContain("No recommendations yet");
  });

  it("saves only an untracked job and invalidates dashboard queries", async () => {
    await render();
    const save = container.querySelector('button[aria-label="Save Backend Engineer"]');
    expect(save).not.toBeNull();
    await act(async () => {
      save?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    expect(apiMocks.applicationsApi.create).toHaveBeenCalledWith(11);
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["applications"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["dashboard-recommendations"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["dashboard-summary"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["dashboard-action-center"] });
  });
});
