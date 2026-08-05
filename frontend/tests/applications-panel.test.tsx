import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApplicationsPanel } from "@/components/applications/applications-panel";
import { formatNextActionDateLabel } from "@/lib/next-actions";

const apiMocks = vi.hoisted(() => {
  const applicationsApi = {
    list: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    create: vi.fn(),
    listDueNextActions: vi.fn(),
    getActivity: vi.fn(),
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

  return { applicationsApi, ApiError };
});

const toastMocks = vi.hoisted(() => {
  const success = vi.fn();
  const error = vi.fn();
  return { success, error };
});

vi.mock("@/lib/api", () => apiMocks);

vi.mock("sonner", () => ({
  toast: {
    success: toastMocks.success,
    error: toastMocks.error,
  },
}));

vi.mock("@/components/cover/cover-letter-dialog", () => ({
  CoverLetterDialog: () => null,
}));

type ApplicationItem = {
  id: number;
  job_id: number;
  job_title: string;
  company: string | null;
  location: string | null;
  company_logo: string | null;
  source_url: string;
  status: string;
  cover_letter_id: number | null;
  notes: string | null;
  next_action_type: string | null;
  next_action_note: string | null;
  next_action_due_date: string | null;
  next_action_completed_at: string | null;
  created_at: string;
  updated_at: string;
};

function renderTextValue(
  el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
  descriptor?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function findButton(container: HTMLElement, label: string) {
  const button = Array.from(container.querySelectorAll("button")).find((el) =>
    el.textContent?.includes(label),
  );
  if (!button) {
    throw new Error(`Could not find button containing: ${label}`);
  }
  return button;
}

function findRowByJobTitle(container: HTMLElement, jobTitle: string) {
  const row = Array.from(container.querySelectorAll("tr")).find((el) =>
    el.textContent?.includes(jobTitle),
  );
  if (!row) {
    throw new Error(`Could not find row containing: ${jobTitle}`);
  }
  return row;
}

function findButtonInRow(row: HTMLElement, label: string) {
  const button = Array.from(row.querySelectorAll("button")).find((el) =>
    el.textContent?.includes(label),
  );
  if (!button) {
    throw new Error(`Could not find row button containing: ${label}`);
  }
  return button;
}

async function openRowMenu(row: HTMLElement) {
  const trigger = row.querySelector<HTMLButtonElement>('button[aria-label^="More actions for"]');
  if (!trigger) {
    throw new Error("Could not find the application actions menu trigger");
  }
  trigger.dispatchEvent(
    new MouseEvent("pointerdown", { bubbles: true, button: 0 }),
  );
  trigger.click();
  await Promise.resolve();
  await Promise.resolve();
}

async function clickRowMenuItem(container: HTMLElement, row: HTMLElement, label: string) {
  await openRowMenu(row);
  const item = Array.from(document.querySelectorAll('[role="menuitem"]')).find((el) =>
    el.textContent?.includes(label),
  );
  if (!item) {
    throw new Error(`Could not find menu item containing: ${label}`);
  }
  (item as HTMLElement).click();
}

function findRowContainingText(container: HTMLElement, text: string) {
  const row = Array.from(container.querySelectorAll("tr")).find((el) =>
    el.textContent?.includes(text),
  );
  if (!row) {
    throw new Error(`Could not find row containing: ${text}`);
  }
  return row;
}

describe("ApplicationsPanel CRM polish", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let applications: ApplicationItem[];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T12:00:00Z"));

    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe() {}
        disconnect() {}
        unobserve() {}
      },
    );

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    });

    toastMocks.success.mockReset();
    toastMocks.error.mockReset();
    apiMocks.applicationsApi.list.mockReset();
    apiMocks.applicationsApi.update.mockReset();
    apiMocks.applicationsApi.delete.mockReset();
    apiMocks.applicationsApi.create.mockReset();
    apiMocks.applicationsApi.listDueNextActions.mockReset();
    apiMocks.applicationsApi.getActivity.mockReset();

    applications = [
      {
        id: 31,
        job_id: 201,
        job_title: "Senior Backend Engineer",
        company: "Beta",
        location: "Remote",
        company_logo: "https://cdn.example.com/beta-logo.png",
        source_url: "https://example.com/jobs/201",
        status: "applied",
        cover_letter_id: null,
        notes: null,
        next_action_type: "follow_up",
        next_action_note: "Ping recruiter after panel review.",
        next_action_due_date: "2026-07-13",
        next_action_completed_at: "2026-07-14T12:00:00.000Z",
        created_at: "2026-07-10T00:00:00Z",
        updated_at: "2026-07-14T12:00:00.000Z",
      },
      {
        id: 42,
        job_id: 202,
        job_title: "Platform Engineer",
        company: "Acme",
        location: "Remote",
        company_logo: null,
        source_url: "https://example.com/jobs/202",
        status: "saved",
        cover_letter_id: null,
        notes: "General application note for the Acme role.",
        next_action_type: "prepare_interview",
        next_action_note: "Review system design notes.",
        next_action_due_date: "2026-07-13",
        next_action_completed_at: null,
        created_at: "2026-07-11T00:00:00Z",
        updated_at: "2026-07-14T11:00:00.000Z",
      },
    ];

    apiMocks.applicationsApi.list.mockImplementation(async () => ({
      items: applications,
      total: applications.length,
      limit: 25,
      next_cursor: null,
    }));

    apiMocks.applicationsApi.update.mockImplementation(
      async (id: number, payload: Partial<ApplicationItem>) => {
        const current = applications.find((app) => app.id === id);
        if (!current) {
          throw new Error(`Missing application ${id}`);
        }
        const updated: ApplicationItem = {
          ...current,
          ...payload,
          updated_at: "2026-07-14T12:00:00.000Z",
        };
        applications = applications.map((app) => (app.id === id ? updated : app));
        return updated;
      },
    );
    apiMocks.applicationsApi.delete.mockImplementation(async (id: number) => {
      applications = applications.filter((app) => app.id !== id);
    });
    apiMocks.applicationsApi.getActivity.mockImplementation(
      async (id: number) => ({
        application_id: id,
        events: [],
      }),
    );

    queryClient.setQueryData(
      ["applications", "infinite"],
      {
        pages: [
          {
            items: applications,
            total: applications.length,
            limit: 25,
            next_cursor: null,
          },
        ],
        pageParams: [null],
      },
    );
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    queryClient.clear();
    container.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function renderPanel() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ApplicationsPanel />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  it("shows status in the application identity and keeps row actions compact", async () => {
    await renderPanel();
    const savedRow = findRowByJobTitle(container, "Platform Engineer");
    const appliedRow = findRowByJobTitle(container, "Senior Backend Engineer");
    const actionsButton = savedRow.querySelector(
      'button[aria-label^="More actions for"]',
    ) as HTMLButtonElement;
    expect(actionsButton).toBeDefined();
    expect(savedRow.textContent).not.toContain("saved");
    expect(container.textContent).not.toContain("Cover letter");
    expect(appliedRow.querySelector('button[aria-label^="More actions for"]')).toBeDefined();
    expect(appliedRow.querySelector('img[alt="Beta logo"]')).toBeDefined();
  });

  it("shows completed details and creates a fresh active next action", async () => {
    await renderPanel();

    expect(container.textContent).toContain("Senior Backend Engineer");
    expect(container.textContent).toContain("Completed");
    expect(container.textContent).toContain("Ping recruiter after panel review.");
    expect(container.textContent).toContain("Due");
    expect(container.textContent).toContain(
      formatNextActionDateLabel("2026-07-13"),
    );
    expect(container.textContent).toContain("Add next action");

    await act(async () => {
      findButton(container, "Add next action").click();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Create a new next action");
    expect(container.textContent).toContain("Next-action note");

    const textarea = container.querySelector("textarea");
    if (!textarea) {
      throw new Error("Expected next action textarea");
    }
    await act(async () => {
      renderTextValue(textarea, "Send a thank-you note and ask about timeline.");
      await Promise.resolve();
    });

    await act(async () => {
      findButton(container, "Save new next action").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.update).toHaveBeenCalledWith(31, {
      next_action_type: null,
      next_action_note: "Send a thank-you note and ask about timeline.",
      next_action_due_date: null,
      next_action_completed_at: null,
    });
  });

  it("still completes and clears incomplete next actions", async () => {
    await renderPanel();

    await act(async () => {
      findButton(container, "Complete").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.update).toHaveBeenCalledWith(42, {
      next_action_type: "prepare_interview",
      next_action_note: "Review system design notes.",
      next_action_due_date: "2026-07-13",
      next_action_completed_at: "2026-07-14T12:00:00.000Z",
    });

    await act(async () => {
      await clickRowMenuItem(
        container,
        findRowByJobTitle(container, "Platform Engineer"),
        "Edit next action",
      );
      await Promise.resolve();
    });

    await act(async () => {
      findButton(container, "Clear").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.update).toHaveBeenCalledWith(42, {
      next_action_type: null,
      next_action_note: null,
      next_action_due_date: null,
      next_action_completed_at: null,
    });
  });

  it("opens, saves, cancels, and clears general application notes", async () => {
    await renderPanel();

    const row = findRowByJobTitle(container, "Platform Engineer");
    await act(async () => {
      await clickRowMenuItem(container, row, "Notes");
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Application notes");
    const notesRow = findRowContainingText(container, "Application notes");
    const textarea = notesRow.querySelector("textarea");
    if (!textarea) {
      throw new Error("Expected application notes textarea");
    }
    expect((textarea as HTMLTextAreaElement).value).toBe(
      "General application note for the Acme role.",
    );

    await act(async () => {
      renderTextValue(
        textarea as HTMLTextAreaElement,
        "General application note for the Acme role. Mention PTO.",
      );
      await Promise.resolve();
    });

    await act(async () => {
      findButton(container, "Save").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.update).toHaveBeenCalledWith(42, {
      notes: "General application note for the Acme role. Mention PTO.",
    });

    await act(async () => {
      await clickRowMenuItem(container, findRowByJobTitle(container, "Platform Engineer"), "Notes");
      await Promise.resolve();
    });

    const reopenedRow = findRowContainingText(container, "Application notes");
    const reopenedTextarea = reopenedRow.querySelector("textarea");
    if (!reopenedTextarea) {
      throw new Error("Expected application notes textarea");
    }

    await act(async () => {
      renderTextValue(
        reopenedTextarea as HTMLTextAreaElement,
        "Temporary draft that gets cancelled",
      );
      await Promise.resolve();
    });

    await act(async () => {
      findButton(container, "Cancel").click();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.update).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("Application notes");

    await act(async () => {
      await clickRowMenuItem(container, findRowByJobTitle(container, "Platform Engineer"), "Notes");
      await Promise.resolve();
    });

    await act(async () => {
      findButton(container, "Clear").click();
      await vi.runAllTimersAsync();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.update).toHaveBeenLastCalledWith(42, {
      notes: null,
    });
    expect(container.textContent).not.toContain("Application notes");

    await act(async () => {
      await clickRowMenuItem(container, findRowByJobTitle(container, "Platform Engineer"), "Notes");
      await Promise.resolve();
    });

    expect(container.textContent).toContain("No application notes yet.");
    const emptyNotesRow = findRowContainingText(container, "Application notes");
    const emptyTextarea = emptyNotesRow.querySelector("textarea");
    if (!emptyTextarea) {
      throw new Error("Expected application notes textarea");
    }
    expect((emptyTextarea as HTMLTextAreaElement).value).toBe("");
  });

  it("shows a notes save error and keeps the draft open", async () => {
    apiMocks.applicationsApi.update.mockRejectedValueOnce(new Error("boom"));

    await renderPanel();

    await act(async () => {
      await clickRowMenuItem(container, findRowByJobTitle(container, "Platform Engineer"), "Notes");
      await Promise.resolve();
    });

    const notesRow = findRowContainingText(container, "Application notes");
    const textarea = notesRow.querySelector("textarea");
    if (!textarea) {
      throw new Error("Expected application notes textarea");
    }

    await act(async () => {
      renderTextValue(
        textarea as HTMLTextAreaElement,
        "Draft that should remain after the failed save",
      );
      await Promise.resolve();
    });

    await act(async () => {
      findButton(container, "Save").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(toastMocks.error).toHaveBeenCalledWith("Update failed");
    expect(container.textContent).toContain("Application notes");
    expect((textarea as HTMLTextAreaElement).value).toBe(
      "Draft that should remain after the failed save",
    );
  });

  it("lazily loads and renders recent manual activity", async () => {
    let resolveActivity: (value: any) => void = () => {};

    const activityPromise = new Promise<any>((resolve) => {
      resolveActivity = resolve;
    });

    apiMocks.applicationsApi.getActivity.mockReturnValueOnce(activityPromise);

    await renderPanel();

    expect(apiMocks.applicationsApi.getActivity).not.toHaveBeenCalled();

    const row = findRowByJobTitle(container, "Platform Engineer");
    await act(async () => {
      await clickRowMenuItem(container, row, "Recent activity");
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.getActivity).toHaveBeenCalledWith(42, {
      limit: 10,
    });
    expect(container.textContent).toContain("Loading activity…");

    await act(async () => {
      resolveActivity({
        application_id: 42,
        events: [
          {
            id: "evt-4",
            event_type: "manual_next_action_cleared",
            metadata: {
              source: "browser_application_update",
              note_body: "Secret note body",
              previous: {
                type: "follow_up",
                due_date: "2026-07-20",
                completed: false,
                has_note: true,
              },
              current: {
                type: null,
                due_date: null,
                completed: false,
                has_note: false,
              },
              changed_fields: [
                "next_action_type",
                "next_action_note",
                "next_action_due_date",
              ],
            },
            created_at: "2026-07-14T13:00:00.000Z",
          },
          {
            id: "evt-3",
            event_type: "manual_next_action_completed",
            metadata: {
              source: "browser_application_update",
              previous: {
                type: "networking",
                due_date: "2026-07-22",
                completed: false,
                has_note: false,
              },
              current: {
                type: "networking",
                due_date: "2026-07-22",
                completed: true,
                has_note: false,
              },
              changed_fields: ["next_action_completed_at"],
            },
            created_at: "2026-07-14T12:30:00.000Z",
          },
          {
            id: "evt-2",
            event_type: "manual_next_action_updated",
            metadata: {
              source: "browser_application_update",
              previous: {
                type: "follow_up",
                due_date: "2026-07-20",
                completed: false,
                has_note: true,
              },
              current: {
                type: "networking",
                due_date: "2026-07-22",
                completed: false,
                has_note: false,
              },
              changed_fields: [
                "next_action_type",
                "next_action_due_date",
              ],
            },
            created_at: "2026-07-14T12:00:00.000Z",
          },
          {
            id: "evt-1",
            event_type: "manual_next_action_created",
            metadata: {
              source: "browser_application_update",
              previous: {
                type: null,
                due_date: null,
                completed: false,
                has_note: false,
              },
              current: {
                type: "follow_up",
                due_date: "2026-07-20",
                completed: false,
                has_note: true,
              },
              changed_fields: [
                "next_action_type",
                "next_action_note",
                "next_action_due_date",
              ],
            },
            created_at: "2026-07-14T11:00:00.000Z",
          },
        ],
      });
      await vi.runAllTimersAsync();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Next action created");
    expect(container.textContent).toContain("Next action updated");
    expect(container.textContent).toContain("Next action completed");
    expect(container.textContent).toContain("Next action cleared");
    expect(container.textContent).toContain("Type: Networking");
    expect(container.textContent).toContain("Due:");
    expect(container.textContent).toContain("Has note");
    expect(container.textContent).not.toContain("Secret note body");
    expect(container.textContent).not.toContain(
      "General application note for the Acme role.",
    );

    const closeButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Hide recent activity"]',
    );
    expect(closeButton).toBeDefined();
    await act(async () => {
      closeButton?.click();
      await Promise.resolve();
    });
    expect(
      Array.from(container.querySelectorAll("tr")).some((row) =>
        row.textContent?.includes("Recent activity"),
      ),
    ).toBe(false);
  });

  it("shows an empty state and invalidates activity after next-action updates", async () => {
    apiMocks.applicationsApi.getActivity.mockResolvedValueOnce({
      application_id: 42,
      events: [],
    });

    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await renderPanel();

    await act(async () => {
      await clickRowMenuItem(container, findRowByJobTitle(container, "Platform Engineer"), "Recent activity");
      await vi.runAllTimersAsync();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("No manual activity yet.");

    await act(async () => {
      findButton(container, "Complete").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      invalidateSpy.mock.calls.some(
        ([arg]) =>
          Boolean(arg) &&
          typeof arg === "object" &&
          JSON.stringify((arg as { queryKey?: unknown }).queryKey) ===
            JSON.stringify(["applications", 42, "activity"]),
      ),
    ).toBe(true);
  });

  it("shows an error state when activity cannot be loaded", async () => {
    apiMocks.applicationsApi.getActivity.mockRejectedValueOnce(
      new Error("boom"),
    );

    await renderPanel();

    await act(async () => {
      await clickRowMenuItem(container, findRowByJobTitle(container, "Platform Engineer"), "Recent activity");
      await vi.runAllTimersAsync();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Activity could not be loaded.");
  });
});
