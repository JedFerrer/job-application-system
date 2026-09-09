import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobsPanel } from "@/components/jobs/jobs-panel";
import type { Job } from "@/lib/types";

const apiMocks = vi.hoisted(() => ({
  jobsApi: { list: vi.fn(), archive: vi.fn(), restore: vi.fn(), importJob: vi.fn() },
  applicationsApi: { create: vi.fn(), delete: vi.fn() },
  listAllApplications: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

const navigationMocks = vi.hoisted(() => ({
  search: "",
  replace: vi.fn(),
  push: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/api", () => apiMocks);
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigationMocks.replace, push: navigationMocks.push }),
  useSearchParams: () => new URLSearchParams(navigationMocks.search),
}));
vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("@tanstack/react-virtual", () => ({
  useWindowVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, start: index * 180 })),
    getTotalSize: () => count * 180,
    measureElement: vi.fn(),
  }),
}));

const archivedJob: Job = {
  id: 701,
  title: "Archived engineer",
  company: "Acme",
  location: "Remote",
  is_remote: true,
  job_type: "fulltime",
  description: "Build useful things.",
  salary_min: null,
  salary_max: null,
  salary_currency: null,
  source: "test",
  source_url: "https://example.com/701",
  posted_at: null,
  fetched_at: "2026-01-01T00:00:00Z",
  shift_type: null,
  company_logo: null,
  visibility: "public",
  ingest_source: "scraper",
  created_by_user_id: null,
  imported_at: null,
};

const emptyPage = { items: [], total: 0, limit: 25, next_cursor: null };

const expiredJob: Job = {
  ...archivedJob,
  id: 702,
  title: "Expired engineer",
  status: "expired",
  first_seen_at: "2026-01-01T00:00:00Z",
  last_seen_at: "2026-01-02T00:00:00Z",
  expired_at: "2026-02-01T00:00:00Z",
  not_recently_seen: true,
};

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitForText(container: HTMLElement, text: string) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await flush();
    if (container.textContent?.includes(text)) return;
  }
  throw new Error(`Timed out waiting for ${text}`);
}

describe("JobsPanel archived view", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    window.scrollTo = vi.fn();
    sessionStorage.clear();
    navigationMocks.search = "";
    vi.clearAllMocks();
    apiMocks.listAllApplications.mockResolvedValue([]);
    apiMocks.jobsApi.importJob.mockResolvedValue(archivedJob);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderPanel() {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobsPanel />
        </QueryClientProvider>,
      );
    });
  }

  it("uses active URL state and keeps active tiles free of archive/restore actions", async () => {
    navigationMocks.search = "archive_state=active";
    apiMocks.jobsApi.list.mockResolvedValue({ ...emptyPage, items: [archivedJob], total: 1 });
    renderPanel();
    await waitForText(container, "Archived engineer");

    expect(apiMocks.jobsApi.list).toHaveBeenCalledWith(expect.objectContaining({ archiveState: "active" }));
    expect(
      [...container.querySelectorAll("div")].some((element) => element.textContent === "Archived"),
    ).toBe(false);
    expect(container.textContent).not.toContain("Restore job");
    expect(container.textContent).toContain("Archive job");

    const archivedToggle = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Archived",
    );
    act(() => archivedToggle?.click());
    expect(navigationMocks.replace).toHaveBeenCalledWith("/dashboard/jobs?archive_state=archived");
  });

  it("keeps lifecycle selection in the query state and uses responsive layout wrappers", async () => {
    apiMocks.jobsApi.list.mockResolvedValue({ ...emptyPage, items: [archivedJob], total: 1 });
    renderPanel();
    await waitForText(container, "Archived engineer");

    expect(apiMocks.jobsApi.list).toHaveBeenCalledWith(expect.objectContaining({ lifecycle: "active" }));
    const filters = container.querySelector("aside");
    expect(filters?.className).toContain("w-full");
    expect(filters?.className).toContain("lg:w-72");
    const lifecycleGroup = container.querySelector('[role="group"][aria-label="Job lifecycle state"]');
    expect(lifecycleGroup?.parentElement?.className).toContain("flex-wrap");

    const expiredToggle = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Outside age window",
    );
    act(() => expiredToggle?.click());
    expect(navigationMocks.replace).toHaveBeenCalledWith("/dashboard/jobs?lifecycle=expired");

    navigationMocks.search = "lifecycle=expired";
    renderPanel();
    await waitForText(container, "Archived engineer");
    expect(apiMocks.jobsApi.list).toHaveBeenCalledWith(expect.objectContaining({ lifecycle: "expired" }));
    const currentToggle = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Current",
    );
    act(() => currentToggle?.click());
    expect(navigationMocks.replace).toHaveBeenCalledWith("/dashboard/jobs");
  });

  it("honors an exact-company link and clears it when the company filter is edited", async () => {
    navigationMocks.search = "company_exact=Acme%20Corp";
    apiMocks.jobsApi.list.mockResolvedValue({ ...emptyPage, items: [archivedJob], total: 1 });
    renderPanel();
    await waitForText(container, "Archived engineer");

    expect(apiMocks.jobsApi.list).toHaveBeenCalledWith(expect.objectContaining({
      companyExact: "Acme Corp",
      company: undefined,
    }));
    const companyInput = [...container.querySelectorAll("input")].find(
      (input) => input.value === "Acme Corp",
    ) as HTMLInputElement;
    expect(companyInput).toBeDefined();

    act(() => {
      const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      valueSetter?.call(companyInput, "Acme");
      companyInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(navigationMocks.replace).toHaveBeenCalledWith("/dashboard/jobs");
  });

  it("shows archived cards, disables Restore while pending, then removes the card with scoped invalidation", async () => {
    navigationMocks.search = "archive_state=archived";
    apiMocks.jobsApi.list.mockResolvedValueOnce({ ...emptyPage, items: [archivedJob], total: 1 });
    apiMocks.jobsApi.list.mockResolvedValue(emptyPage);
    let resolveRestore!: () => void;
    apiMocks.jobsApi.restore.mockImplementation(
      () => new Promise<void>((resolve) => { resolveRestore = resolve; }),
    );
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderPanel();
    await waitForText(container, "Archived engineer");

    expect(apiMocks.jobsApi.list).toHaveBeenCalledWith(expect.objectContaining({ archiveState: "archived" }));
    expect(container.textContent).toContain("Archived");
    const restoreButton = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("Restore job"),
    ) as HTMLButtonElement;
    expect(restoreButton).toBeDefined();
    expect(restoreButton.className).toContain("whitespace-nowrap");
    expect(restoreButton.className).not.toContain("w-9");

    act(() => restoreButton.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await flush();
    expect(apiMocks.jobsApi.restore).toHaveBeenCalledTimes(1);
    expect(restoreButton.disabled).toBe(true);

    await act(async () => { resolveRestore(); });
    await flush();

    expect(container.textContent).not.toContain("Archived engineer");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["imported-jobs"] });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["applications"] });
  });

  it("derives active save UI from application status and follows refreshed data", async () => {
    navigationMocks.search = "archive_state=active";
    apiMocks.jobsApi.list.mockResolvedValue({ ...emptyPage, items: [archivedJob], total: 1 });
    apiMocks.listAllApplications.mockResolvedValue([
      { id: 1, job_id: archivedJob.id, status: "closed" },
    ]);
    apiMocks.applicationsApi.create.mockResolvedValue({
      id: 1, job_id: archivedJob.id, status: "saved",
    });
    renderPanel();
    await waitForText(container, "Archived engineer");

    const saveButton = [...container.querySelectorAll("button")].find(
      (button) => button.getAttribute("aria-label") === "Save job",
    ) as HTMLButtonElement;
    expect(saveButton).toBeDefined();
    act(() => saveButton.click());
    await flush();
    expect(apiMocks.applicationsApi.create).toHaveBeenCalledWith(archivedJob.id);
    expect(toastMocks.success).toHaveBeenCalledWith("Saved to applications");

    act(() => {
      queryClient.setQueryData(["applications", "list"], [
        { id: 1, job_id: archivedJob.id, status: "saved" },
      ]);
    });
    await flush();
    expect([...container.querySelectorAll("button")].some(
      (button) => button.getAttribute("aria-label") === "Save job",
    )).toBe(false);
  });

  it("keeps expired cards navigable and factual while omitting all mutation controls", async () => {
    navigationMocks.search = "archive_state=active&lifecycle=expired";
    apiMocks.jobsApi.list.mockResolvedValue({ ...emptyPage, items: [expiredJob], total: 1 });
    renderPanel();
    await waitForText(container, "Expired engineer");

    expect(container.textContent).toContain("Outside search age window");
    expect(container.textContent).toContain("First found:");
    expect(container.textContent).toContain("Last observed:");
    expect(container.querySelector('[aria-label="Save job"]')).toBeNull();
    expect(container.querySelector('[aria-label="Unsave job"]')).toBeNull();
    expect(container.textContent).not.toContain("Archive job");
    expect(container.textContent).not.toContain("Restore job");

    const title = [...container.querySelectorAll("h3")].find(
      (heading) => heading.textContent === "Expired engineer",
    );
    act(() => title?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(navigationMocks.push).toHaveBeenCalledWith("/jobs/702");
  });

  it("omits Restore for an expired archived card", async () => {
    navigationMocks.search = "archive_state=archived&lifecycle=expired";
    apiMocks.jobsApi.list.mockResolvedValue({ ...emptyPage, items: [expiredJob], total: 1 });
    renderPanel();
    await waitForText(container, "Expired engineer");

    expect(container.textContent).toContain("Outside search age window");
    expect(container.textContent).not.toContain("Restore job");
  });

  it("does not show Save for active CRM application statuses", async () => {
    navigationMocks.search = "archive_state=active";
    apiMocks.jobsApi.list.mockResolvedValue({ ...emptyPage, items: [archivedJob], total: 1 });
    apiMocks.listAllApplications.mockResolvedValue([
      { id: 1, job_id: archivedJob.id, status: "applied" },
    ]);
    renderPanel();
    await waitForText(container, "Archived engineer");
    expect([...container.querySelectorAll("button")].some(
      (button) => button.textContent?.trim() === "Save",
    )).toBe(false);
  });
});
