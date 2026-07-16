import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  ReadinessPreferencesForm,
  toReadinessPayload,
  type ReadinessFormState,
} from "@/components/preferences/readiness-preferences-form";

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  upsert: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  readinessPreferencesApi: apiMocks,
  ApiError: class ApiError extends Error {},
}));

vi.mock("sonner", () => ({ toast: toastMocks }));

const EMPTY_RESPONSE = {
  id: 1,
  user_id: 7,
  minimum_annual_salary: null,
  salary_currency: null,
  remote_only: null,
  accepted_job_types: [],
  preferred_locations: [],
  blocked_keywords: [],
  created_at: "2026-07-16T00:00:00Z",
  updated_at: "2026-07-16T00:00:00Z",
};

function renderForm() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ReadinessPreferencesForm />
      </QueryClientProvider>,
    );
  });
  return { container, root, queryClient };
}

function button(container: HTMLDivElement): HTMLButtonElement {
  return Array.from(container.querySelectorAll("button")).find(
    (element) => element.textContent?.includes("Save readiness preferences"),
  ) as HTMLButtonElement;
}

function setInput(container: HTMLDivElement, selector: string, value: string): void {
  const input = container.querySelector(selector) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

async function waitForQuery(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("ReadinessPreferencesForm", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    apiMocks.get.mockReset();
    apiMocks.upsert.mockReset();
    toastMocks.success.mockReset();
    toastMocks.error.mockReset();
  });

  afterEach(() => {
    act(() => root?.unmount());
    container?.remove();
  });

  it("builds a complete payload with trimmed values and empty locations", () => {
    const state: ReadinessFormState = {
      minimumAnnualSalary: "85000",
      salaryCurrency: " usd ",
      remoteOnly: true,
      acceptedJobTypes: " fulltime, , contract, fulltime ",
      blockedKeywords: " commission only,  , unpaid internship ",
    };

    expect(toReadinessPayload(state)).toEqual({
      minimum_annual_salary: 85000,
      salary_currency: "USD",
      remote_only: true,
      accepted_job_types: ["fulltime", "contract"],
      preferred_locations: [],
      blocked_keywords: ["commission only", "unpaid internship"],
    });
  });

  it("hydrates the safe empty state and saves the full profile", async () => {
    apiMocks.get.mockResolvedValue(EMPTY_RESPONSE);
    apiMocks.upsert.mockResolvedValue(EMPTY_RESPONSE);
    ({ container, root } = renderForm());

    await act(async () => {
      await waitForQuery();
    });

    expect((container.querySelector("#minimum-annual-salary") as HTMLInputElement).value).toBe("");
    expect((container.querySelector("#accepted-job-types") as HTMLInputElement).value).toBe("");

    setInput(container, "#blocked-keywords", "  commission only, , commission only ");
    await act(async () => {
      button(container).click();
      await Promise.resolve();
    });

    expect(apiMocks.upsert).toHaveBeenCalledWith({
      minimum_annual_salary: null,
      salary_currency: null,
      remote_only: null,
      accepted_job_types: [],
      preferred_locations: [],
      blocked_keywords: ["commission only"],
    });
  });

  it("hydrates existing values and supports clearing the remote-only rule", async () => {
    apiMocks.get.mockResolvedValue({
      ...EMPTY_RESPONSE,
      minimum_annual_salary: 85000,
      salary_currency: "USD",
      remote_only: true,
      accepted_job_types: ["fulltime", "contract"],
      blocked_keywords: ["commission only"],
    });
    apiMocks.upsert.mockResolvedValue(EMPTY_RESPONSE);
    ({ container, root } = renderForm());

    await act(async () => {
      await waitForQuery();
    });

    expect((container.querySelector("#minimum-annual-salary") as HTMLInputElement).value).toBe(
      "85000",
    );
    expect((container.querySelector('input[aria-label="Salary currency"]') as HTMLInputElement).value).toBe(
      "USD",
    );
    expect((container.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(
      true,
    );
    expect((container.querySelector("#accepted-job-types") as HTMLInputElement).value).toBe(
      "fulltime, contract",
    );

    (container.querySelector('input[type="checkbox"]') as HTMLInputElement).click();
    await act(async () => {
      button(container).click();
      await Promise.resolve();
    });

    expect(apiMocks.upsert).toHaveBeenCalledWith(
      expect.objectContaining({ remote_only: null }),
    );
  });

  it("requires currency for salary and rejects negative values", async () => {
    apiMocks.get.mockResolvedValue(EMPTY_RESPONSE);
    ({ container, root } = renderForm());
    await act(async () => {
      await waitForQuery();
    });

    setInput(container, "#minimum-annual-salary", "-1");
    expect(button(container).disabled).toBe(true);

    setInput(container, "#minimum-annual-salary", "85000");
    expect(button(container).disabled).toBe(true);
  });

  it("preserves entered values when saving fails", async () => {
    apiMocks.get.mockResolvedValue(EMPTY_RESPONSE);
    apiMocks.upsert.mockRejectedValue(new Error("Save failed"));
    ({ container, root } = renderForm());
    await act(async () => {
      await waitForQuery();
    });

    setInput(container, "#accepted-job-types", "contract");
    await act(async () => {
      button(container).click();
      await Promise.resolve();
    });

    expect((container.querySelector("#accepted-job-types") as HTMLInputElement).value).toBe(
      "contract",
    );
    expect(toastMocks.error).toHaveBeenCalled();
  });
});
