import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SearchReadinessCard } from "../src/components/dashboard/search-readiness-card";

const queryMocks = vi.hoisted(() => ({ useQuery: vi.fn() }));
const apiMocks = vi.hoisted(() => ({ dashboardApi: { getSearchReadiness: vi.fn() } }));

vi.mock("@tanstack/react-query", () => queryMocks);
vi.mock("../src/lib/api", () => apiMocks);
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

describe("SearchReadinessCard", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryState: { data: unknown; isLoading: boolean; isError: boolean; refetch: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryState = {
      data: {
        overall_state: "almost_ready",
        checks: {
          resume: { state: "ready", has_pending_draft: true },
          profile: { state: "ready" },
          preferences: { state: "needs_setup" },
        },
        next_action: { reason: "set_job_preferences", label: "Set job preferences" },
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    };
    queryMocks.useQuery.mockImplementation(() => queryState);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function render() {
    await act(async () => root.render(<SearchReadinessCard />));
  }

  it("renders checks, one primary action, and a non-blocking draft link", async () => {
    await render();
    expect(container.textContent).toContain("Search Readiness");
    expect(container.textContent).toContain("Almost ready");
    expect(container.textContent).toContain("Job preferences");
    expect(container.querySelector('a[href="/dashboard/settings/preferences"]')?.textContent).toBe(
      "Set job preferences",
    );
    expect(container.querySelector('a[href="/dashboard/settings/profile"]')?.textContent).toBe(
      "Review draft",
    );
  });

  it("uses View matches for a ready user", async () => {
    queryState.data = {
      overall_state: "ready",
      checks: {
        resume: { state: "ready", has_pending_draft: false },
        profile: { state: "ready" },
        preferences: { state: "ready" },
      },
      next_action: { reason: "view_matches", label: "View matches" },
    };
    await render();
    expect(container.textContent).not.toContain("Search Readiness");
    expect(container.textContent).not.toContain("Review draft");
  });

  it.each([
    ["draft_review", "Review resume draft", "/dashboard/settings/profile"],
    ["import_resume", "Import resume", "/dashboard/settings/profile"],
    ["complete_profile", "Complete profile", "/dashboard/settings/profile"],
  ] as const)("maps %s to its existing destination", async (reason, label, href) => {
    queryState.data = {
      overall_state: "needs_setup",
      checks: {
        resume: { state: reason === "draft_review" ? "draft_review" : "needs_import", has_pending_draft: reason === "draft_review" },
        profile: { state: "needs_setup" },
        preferences: { state: "needs_setup" },
      },
      next_action: { reason, label },
    };
    await render();
    expect(container.querySelector(`a[href="${href}"]`)?.textContent).toBe(label);
  });

  it("renders loading and final error retry states", async () => {
    queryState = { data: null, isLoading: true, isError: false, refetch: vi.fn() };
    await render();
    expect(container.textContent).toContain("Loading search readiness");

    queryState = { data: null, isLoading: false, isError: true, refetch: vi.fn() };
    await render();
    expect(container.textContent).toContain("Couldn’t load search readiness.");
    const retry = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Retry");
    await act(async () => retry?.click());
    expect(queryState.refetch).toHaveBeenCalled();
  });
});
