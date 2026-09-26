import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SubmissionHistory } from "@/components/applications/submission-history";
import { ApplicationTimeline } from "@/components/applications/application-timeline";
import type { ApplicationWorkspace, ApplicationSubmissionSummary } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  listSubmissions: vi.fn(), getSubmission: vi.fn(), confirmSubmission: vi.fn(), deleteSubmission: vi.fn(), getTimeline: vi.fn(),
  getWorkspace: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { constructor(message: string, public status: number) { super(message); } },
  applicationsApi: mocks,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const row = (overrides: Partial<ApplicationSubmissionSummary> = {}): ApplicationSubmissionSummary => ({
  id: 11, application_id: 7, attempt_id: null, package_id: 21, artifact_ids: [], artifacts: [],
  material_choice: "used_selected_package", provenance: "user_confirmed", submitted_at: "2026-09-01T12:00:00Z",
  submitted_at_is_user_reported: false, note: null, created_at: "2026-09-02T12:00:00Z", ...overrides,
});
const workspace = { package_history: [{ id: 21, revision: 1, created_at: "2026-08-01T00:00:00Z", artifacts: [{ id: 4, filename: "resume.pdf", document_type: "resume", format: "pdf", validation_state: "warning", sha256: "a".repeat(64) }] }] } as unknown as ApplicationWorkspace;

describe("submission history and timeline", () => {
  let host: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let client: QueryClient;
  beforeEach(() => {
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mocks.listSubmissions.mockResolvedValue({ items: [], next_cursor: null });
    mocks.getTimeline.mockResolvedValue({ application_id: 7, items: [], next_cursor: null });
  });
  afterEach(async () => { await act(async () => root.unmount()); host.remove(); document.querySelectorAll("[data-radix-portal]").forEach((node) => node.remove()); vi.clearAllMocks(); });
  const button = (label: string) => [...document.querySelectorAll("button")].find((item) => item.textContent?.trim() === label) as HTMLButtonElement | undefined;
  const detailButtons = () => [...host.querySelectorAll("button")].filter((item) => item.textContent?.trim() === "View details") as HTMLButtonElement[];
  const keyboardOpenDetails = async (trigger: HTMLButtonElement) => {
    trigger.focus();
    await act(async () => {
      trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      trigger.click(); // jsdom does not synthesize native keyboard button activation.
    });
    await vi.waitFor(() => expect((document.querySelector('[role="dialog"]') as HTMLElement).contains(document.activeElement)).toBe(true));
  };
  const render = async (timeline = false) => {
    await act(async () => root.render(<QueryClientProvider client={client}>{timeline ? <ApplicationTimeline applicationId={7} /> : <SubmissionHistory applicationId={7} workspace={workspace} onViewPackage={vi.fn()} />}</QueryClientProvider>));
    await vi.waitFor(() => expect(mocks.listSubmissions).toHaveBeenCalled(), { timeout: 3000 }).catch(() => undefined);
  };

  it("returns focus to the keyboard opener after Escape and Cancel", async () => {
    await render();
    const trigger = button("Confirm materials")!;
    trigger.focus();
    await act(async () => {
      trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      trigger.click(); // jsdom does not synthesize native keyboard button activation.
    });
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    await vi.waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    await act(async () => dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    expect(document.activeElement).toBe(trigger);

    await act(async () => trigger.click());
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
    await act(async () => button("Cancel")?.click());
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it("shows the legacy empty state from the server and confirms a package with zero files", async () => {
    mocks.confirmSubmission.mockResolvedValue(row());
    await render();
    await vi.waitFor(() => expect(host.textContent).toContain("Submitted material not recorded"));
    await act(async () => [...document.querySelectorAll("button")].findLast((item) => item.textContent?.trim() === "Confirm materials")?.click());
    const selected = document.querySelector('input[value="used_selected_package"]') as HTMLInputElement | null;
    const radios = [...document.querySelectorAll('input[name="material-choice"]')] as HTMLInputElement[];
    expect(selected ?? radios.length).toBeTruthy();
    await act(async () => radios[0]?.click());
    const packageSelect = document.querySelector("#submission-package") as HTMLSelectElement;
    await act(async () => { packageSelect.value = "21"; packageSelect.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => [...document.querySelectorAll("button")].findLast((item) => item.textContent?.trim() === "Confirm materials")?.click());
    await vi.waitFor(() => expect(mocks.confirmSubmission).toHaveBeenCalled());
    expect(mocks.confirmSubmission.mock.calls[0]?.[1]).toMatchObject({ material_choice: "used_selected_package", package_id: 21, artifact_ids: [] });
    expect(mocks.confirmSubmission.mock.calls[0]?.[2]).toEqual(expect.any(String));
  });

  it("shows server provenance, pages without duplicates, and exact artifact metadata", async () => {
    const first = row({ id: 11, submitted_at_is_user_reported: true, artifact_ids: [4] });
    const second = row({ id: 10, material_choice: "used_other_material", package_id: null });
    mocks.listSubmissions.mockImplementation(async (_id: number, options: { cursor?: string }) => options.cursor ? { items: [first, second], next_cursor: null } : { items: [first], next_cursor: "next" });
    mocks.getSubmission.mockResolvedValue({ ...first, artifacts: workspace.package_history[0]?.artifacts });
    await render();
    await vi.waitFor(() => expect(host.textContent).toContain("User-reported submitted time"));
    await act(async () => button("Load more")?.click());
    await vi.waitFor(() => expect(mocks.listSubmissions).toHaveBeenCalledWith(7, { cursor: "next", limit: 25 }));
    await vi.waitFor(() => expect(host.querySelectorAll("li").length).toBe(2));
    await act(async () => button("View details")?.click());
    await vi.waitFor(() => expect(document.body.textContent).toContain("SHA-256"));
    expect(document.body.textContent).toContain("warning");
    expect(document.body.textContent).toContain("History recorded");
  });

  it("keeps Load more focused while pending and after a non-final page", async () => {
    let finishPage!: (value: { items: ApplicationSubmissionSummary[]; next_cursor: string }) => void;
    mocks.listSubmissions.mockImplementation((_id: number, options: { cursor?: string }) => options.cursor
      ? new Promise((resolve) => { finishPage = resolve; })
      : Promise.resolve({ items: [row()], next_cursor: "next" }));
    await render();
    const trigger = await vi.waitFor(() => { expect(button("Load more")).toBeTruthy(); return button("Load more")!; });
    trigger.focus();
    await act(async () => { trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); trigger.click(); });
    await vi.waitFor(() => expect(button("Loading…")).toBe(trigger));
    expect(document.activeElement).toBe(trigger);
    expect(trigger.disabled).toBe(false);
    await act(async () => trigger.click());
    expect(mocks.listSubmissions).toHaveBeenCalledTimes(2);
    await act(async () => finishPage({ items: [row({ id: 10 })], next_cursor: "last" }));
    await vi.waitFor(() => expect(button("Load more")).toBe(trigger));
    expect(detailButtons()).toHaveLength(2);
    expect(document.activeElement).toBe(trigger);
    expect(document.activeElement).not.toBe(document.body);
  });

  it("focuses the first newly appended detail control after the final page", async () => {
    mocks.listSubmissions.mockImplementation(async (_id: number, options: { cursor?: string }) => options.cursor
      ? { items: [row({ id: 11 }), row({ id: 10 }), row({ id: 9 })], next_cursor: null }
      : { items: [row({ id: 11 })], next_cursor: "last" });
    await render();
    const trigger = await vi.waitFor(() => { expect(button("Load more")).toBeTruthy(); return button("Load more")!; });
    trigger.focus();
    await act(async () => { trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); trigger.click(); });
    await vi.waitFor(() => expect(button("Load more")).toBeUndefined());
    expect(detailButtons()).toHaveLength(3);
    expect(document.activeElement).toBe(detailButtons()[1]);
    expect(document.activeElement).not.toBe(document.body);
  });

  it("focuses the history heading when the final page adds no usable row", async () => {
    mocks.listSubmissions.mockImplementation(async (_id: number, options: { cursor?: string }) => options.cursor
      ? { items: [row({ id: 11 })], next_cursor: null }
      : { items: [row({ id: 11 })], next_cursor: "last" });
    await render();
    const trigger = await vi.waitFor(() => { expect(button("Load more")).toBeTruthy(); return button("Load more")!; });
    trigger.focus();
    await act(async () => trigger.click());
    await vi.waitFor(() => expect(button("Load more")).toBeUndefined());
    expect(document.activeElement).toBe(host.querySelector("h2"));
    expect(document.activeElement).not.toBe(document.body);
  });

  it("does not steal focus during a passive submission-list refresh", async () => {
    mocks.listSubmissions.mockResolvedValue({ items: [row()], next_cursor: "next" });
    await render();
    const otherControl = button("Confirm materials")!;
    otherControl.focus();
    await act(async () => { await client.invalidateQueries({ queryKey: ["application-submissions", 7] }); });
    expect(mocks.listSubmissions).toHaveBeenCalledTimes(2);
    expect(document.activeElement).toBe(otherControl);
  });

  it("keeps Load more focused and retryable after a page failure", async () => {
    mocks.listSubmissions.mockImplementation(async (_id: number, options: { cursor?: string }) => {
      if (options.cursor) throw new Error("network failure");
      return { items: [row()], next_cursor: "next" };
    });
    await render();
    const trigger = await vi.waitFor(() => { expect(button("Load more")).toBeTruthy(); return button("Load more")!; });
    trigger.focus();
    await act(async () => { trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); trigger.click(); });
    await vi.waitFor(() => expect(mocks.listSubmissions).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(trigger.getAttribute("aria-busy")).toBe("false"));
    expect(button("Load more")).toBe(trigger);
    expect(detailButtons()).toHaveLength(1);
    expect(document.activeElement).toBe(trigger);
    expect(document.activeElement).not.toBe(document.body);
  });

  it("uses neutral time wording when the server generated the submitted time", async () => {
    mocks.listSubmissions.mockResolvedValue({ items: [row()], next_cursor: null });
    await render();
    await vi.waitFor(() => expect(host.textContent).toContain("Submitted time:"));
    expect(host.textContent).not.toContain("User-reported submitted time");
  });

  it.each(["used_other_material", "materials_unknown"] as const)("does not link package or files for %s", async (choice) => {
    mocks.confirmSubmission.mockResolvedValue(row({ material_choice: choice, package_id: null }));
    await render();
    await act(async () => button("Confirm materials")?.click());
    const radios = [...document.querySelectorAll('input[name="material-choice"]')] as HTMLInputElement[];
    await act(async () => radios[choice === "used_other_material" ? 1 : 2]?.click());
    await act(async () => [...document.querySelectorAll("button")].findLast((item) => item.textContent?.trim() === "Confirm materials")?.click());
    await vi.waitFor(() => expect(mocks.confirmSubmission).toHaveBeenCalled());
    expect(mocks.confirmSubmission.mock.calls[0]?.[1]).not.toHaveProperty("package_id");
    expect(mocks.confirmSubmission.mock.calls[0]?.[1]).not.toHaveProperty("artifact_ids");
  });

  it("reuses its key for the same confirmation after a network failure", async () => {
    mocks.confirmSubmission.mockRejectedValue(new TypeError("network"));
    await render();
    await act(async () => button("Confirm materials")?.click());
    const submit = () => [...document.querySelectorAll("button")].findLast((item) => item.textContent?.trim() === "Confirm materials");
    await act(async () => submit()?.click());
    await vi.waitFor(() => expect(mocks.confirmSubmission).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(submit()?.disabled).toBe(false));
    await act(async () => submit()?.click());
    await vi.waitFor(() => expect(mocks.confirmSubmission).toHaveBeenCalledTimes(2));
    expect(mocks.confirmSubmission.mock.calls[0]?.[2]).toBe(mocks.confirmSubmission.mock.calls[1]?.[2]);
  });

  it("requires deletion confirmation and refreshes authoritative history", async () => {
    mocks.listSubmissions.mockResolvedValueOnce({ items: [row()], next_cursor: null }).mockResolvedValue({ items: [], next_cursor: null });
    mocks.getSubmission.mockResolvedValue(row());
    mocks.deleteSubmission.mockResolvedValue(undefined);
    await render();
    await vi.waitFor(() => expect(host.textContent).toContain("Used selected package"));
    await act(async () => button("View details")?.click());
    await vi.waitFor(() => expect(button("Delete history")).toBeTruthy());
    await act(async () => button("Delete history")?.click());
    expect(document.body.textContent).toContain("does not undo an application sent to an employer");
    expect(mocks.deleteSubmission).not.toHaveBeenCalled();
    await act(async () => [...document.querySelectorAll("button")].findLast((item) => item.textContent?.trim() === "Delete history")?.click());
    await vi.waitFor(() => expect(mocks.deleteSubmission).toHaveBeenCalledWith(7, 11));
    await vi.waitFor(() => expect(host.textContent).toContain("Submitted material not recorded"));
  });

  it("returns focus to Delete history after keyboard Cancel and Escape", async () => {
    mocks.listSubmissions.mockResolvedValue({ items: [row()], next_cursor: null });
    mocks.getSubmission.mockResolvedValue(row());
    await render();
    await vi.waitFor(() => expect(button("View details")).toBeTruthy());
    await act(async () => button("View details")?.click());
    const trigger = await vi.waitFor(() => {
      const element = button("Delete history");
      expect(element).toBeTruthy();
      return element!;
    });
    for (const close of ["Cancel", "Escape"]) {
      trigger.focus();
      await act(async () => {
        trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        trigger.click(); // jsdom does not synthesize native keyboard button activation.
      });
      const confirmation = document.querySelector('[role="alertdialog"]') as HTMLElement;
      await vi.waitFor(() => expect(confirmation.contains(document.activeElement)).toBe(true));
      if (close === "Cancel") {
        const cancel = button("Cancel")!;
        cancel.focus();
        await act(async () => {
          cancel.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
          cancel.click();
        });
      } else {
        await act(async () => confirmation.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
      }
      await vi.waitFor(() => expect(document.querySelector('[role="alertdialog"]')).toBeNull());
      expect(document.querySelector('[role="dialog"]')).not.toBeNull();
      expect(document.activeElement).toBe(trigger);
      expect(document.activeElement).not.toBe(document.body);
    }
  });

  it("returns focus to the same history detail opener after Escape", async () => {
    mocks.listSubmissions.mockResolvedValue({ items: [row()], next_cursor: null });
    mocks.getSubmission.mockResolvedValue(row());
    await render();
    const trigger = await vi.waitFor(() => { expect(detailButtons()).toHaveLength(1); return detailButtons()[0]!; });
    await keyboardOpenDetails(trigger);
    await act(async () => document.querySelector('[role="dialog"]')?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    await vi.waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(document.activeElement).not.toBe(document.body);
  });

  it("returns focus to the same history detail opener after Close", async () => {
    mocks.listSubmissions.mockResolvedValue({ items: [row()], next_cursor: null });
    mocks.getSubmission.mockResolvedValue(row());
    await render();
    const trigger = await vi.waitFor(() => { expect(detailButtons()).toHaveLength(1); return detailButtons()[0]!; });
    await keyboardOpenDetails(trigger);
    await act(async () => button("Close")?.click());
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    await vi.waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("returns focus to the second row when its detail opener is used", async () => {
    mocks.listSubmissions.mockResolvedValue({ items: [row(), row({ id: 12, package_id: null })], next_cursor: null });
    mocks.getSubmission.mockResolvedValue(row({ id: 12, package_id: null }));
    await render();
    const second = await vi.waitFor(() => { expect(detailButtons()).toHaveLength(2); return detailButtons()[1]!; });
    await keyboardOpenDetails(second);
    await act(async () => button("Close")?.click());
    await vi.waitFor(() => expect(document.activeElement).toBe(second));
    expect(document.activeElement).not.toBe(detailButtons()[0]);
  });

  it("focuses the persistent history heading after successful deletion", async () => {
    mocks.listSubmissions.mockResolvedValueOnce({ items: [row()], next_cursor: null }).mockResolvedValue({ items: [], next_cursor: null });
    mocks.getSubmission.mockResolvedValue(row());
    mocks.deleteSubmission.mockResolvedValue(undefined);
    await render();
    await vi.waitFor(() => expect(button("View details")).toBeTruthy());
    await act(async () => button("View details")?.click());
    const trigger = await vi.waitFor(() => {
      const element = button("Delete history");
      expect(element).toBeTruthy();
      return element!;
    });
    trigger.focus();
    await act(async () => trigger.click());
    const confirm = [...document.querySelectorAll('button')].findLast((item) => item.textContent?.trim() === "Delete history")!;
    confirm.focus();
    await act(async () => {
      confirm.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      confirm.click();
    });
    await vi.waitFor(() => expect(mocks.deleteSubmission).toHaveBeenCalledWith(7, 11));
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    await vi.waitFor(() => expect(document.querySelector('[role="alertdialog"]')).toBeNull());
    await vi.waitFor(() => expect(document.activeElement).toBe(host.querySelector("h2")));
    expect((document.activeElement as HTMLElement).tabIndex).toBe(-1);
    expect(document.activeElement).not.toBe(trigger);
    expect(document.activeElement).not.toBe(document.body);
  });

  it("recovers from a stale 404 delete using the server list", async () => {
    const { ApiError } = await import("@/lib/api");
    mocks.listSubmissions.mockResolvedValueOnce({ items: [row()], next_cursor: null }).mockResolvedValue({ items: [], next_cursor: null });
    mocks.getSubmission.mockResolvedValue(row());
    mocks.deleteSubmission.mockRejectedValue(new ApiError("missing", 404, null));
    await render();
    await vi.waitFor(() => expect(button("View details")).toBeTruthy());
    await act(async () => button("View details")?.click());
    await vi.waitFor(() => expect(button("Delete history")).toBeTruthy());
    await act(async () => button("Delete history")?.click());
    await act(async () => [...document.querySelectorAll("button")].findLast((item) => item.textContent?.trim() === "Delete history")?.click());
    await vi.waitFor(() => expect(host.textContent).toContain("Submitted material not recorded"));
  });

  it("loads history from the server after a fresh mount", async () => {
    mocks.listSubmissions.mockResolvedValueOnce({ items: [], next_cursor: null }).mockResolvedValue({ items: [row()], next_cursor: null });
    await render();
    await vi.waitFor(() => expect(host.textContent).toContain("Submitted material not recorded"));
    await act(async () => root.unmount());
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await render();
    await vi.waitFor(() => expect(host.textContent).toContain("Used selected package"));
    expect(mocks.listSubmissions).toHaveBeenCalledTimes(2);
  });

  it("keeps page confirmation distinct and reports deleted submission detail", async () => {
    mocks.getTimeline.mockResolvedValue({ application_id: 7, next_cursor: null, items: [
      { id: "e1", type: "confirmation_detected", submission_id: null, summary: "Application confirmation detected", occurred_at: "2026-09-01T00:00:00Z", counts: null },
      { id: "e2", type: "submission_confirmed", submission_id: 11, summary: "Submitted material explicitly confirmed", occurred_at: "2026-09-02T00:00:00Z", counts: null },
    ] });
    const { ApiError } = await import("@/lib/api");
    mocks.getSubmission.mockRejectedValue(new ApiError("missing", 404, null));
    await render(true);
    await vi.waitFor(() => expect(host.textContent).toContain("Submission details no longer available"));
    expect(host.textContent).toContain("Application page confirmation detected");
    expect(host.textContent).toContain("Submission #11");
  });
});
