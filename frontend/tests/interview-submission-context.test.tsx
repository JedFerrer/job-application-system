import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApplicationInterviewPrep } from "@/components/applications/application-interview-prep";
import type { ApplicationSubmissionSummary } from "@/lib/types";

const mocks = vi.hoisted(() => ({ prepareInterview: vi.fn() }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { constructor(message: string, public status: number) { super(message); } },
  applicationsApi: mocks,
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

const submission = (id: number, material_choice: ApplicationSubmissionSummary["material_choice"] = "used_selected_package") => ({
  id, application_id: 7, attempt_id: null, package_id: material_choice === "used_selected_package" ? id + 20 : null,
  artifact_ids: [], artifacts: [], material_choice, provenance: "user_confirmed",
  submitted_at: "2026-09-01T12:00:00Z", submitted_at_is_user_reported: false, note: null,
  created_at: "2026-09-02T12:00:00Z",
} satisfies ApplicationSubmissionSummary);
const result = (source_label: "confirmed_submission" | "current_profile_fallback" = "confirmed_submission") => ({
  application_id: 7, submission_id: source_label === "confirmed_submission" ? 11 : null, source_label,
  stage: "technical", focus_areas: ["systems"], question_count: 3, context_hash: "hash", cached: true,
  questions: [{ question: "Explain a system", suggested_answer: "Use a specific example", source_locators: ["resume:one"] }],
  evidence: [{ locator: "resume:one", source: "resume" }], model: "fake", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
});

describe("application interview context", () => {
  let host: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); });
  afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.clearAllMocks(); });
  const button = (label: string) => [...document.querySelectorAll("button")].find((item) => item.textContent?.trim() === label) as HTMLButtonElement | undefined;
  const render = async (rows: ApplicationSubmissionSummary[]) => {
    await act(async () => root.render(<QueryClientProvider client={new QueryClient()}><ApplicationInterviewPrep applicationId={7} submissions={rows} loading={false} /></QueryClientProvider>));
    await act(async () => button("Prepare for interview")?.click());
  };
  const change = async (id: string, value: string) => {
    const field = document.getElementById(id) as HTMLInputElement | HTMLSelectElement;
    await act(async () => {
      const prototype = field instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(field, value);
      field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
    });
  };
  const activateByKeyboard = async (target: HTMLButtonElement) => {
    target.focus();
    await act(async () => {
      target.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      target.click(); // jsdom does not synthesize native keyboard button activation.
    });
  };

  it("returns focus to the keyboard opener after Escape and Close", async () => {
    await act(async () => root.render(<QueryClientProvider client={new QueryClient()}><ApplicationInterviewPrep applicationId={7} submissions={[submission(11)]} loading={false} /></QueryClientProvider>));
    const trigger = button("Prepare for interview")!;
    trigger.focus();
    await act(async () => {
      trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      trigger.click(); // jsdom does not synthesize native keyboard button activation.
    });
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    await vi.waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    await act(async () => dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    await vi.waitFor(() => expect(document.activeElement).toBe(trigger));

    await act(async () => trigger.click());
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
    await act(async () => button("Close")?.click());
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    await vi.waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("uses the only confirmed package and displays source, cached result, and evidence", async () => {
    mocks.prepareInterview.mockResolvedValue(result());
    await render([submission(11)]);
    expect(document.body.textContent).toContain("Based on confirmed historical submission #11");
    await change("interview-stage", "technical");
    await change("interview-focus", "systems");
    await change("interview-count", "3");
    await act(async () => button("Generate questions")?.click());
    await vi.waitFor(() => expect(mocks.prepareInterview).toHaveBeenCalled());
    expect(mocks.prepareInterview.mock.calls[0]?.[1]).toMatchObject({ submission_id: 11, current_profile_fallback: false, stage: "technical", focus_areas: ["systems"], question_count: 3, force_refresh: false });
    await vi.waitFor(() => expect(document.body.textContent).toContain("Use a specific example"));
    expect(document.body.textContent).toContain("Resume: resume:one");
    expect(document.body.textContent).toContain("Saved result");
    await act(async () => button("Regenerate questions")?.click());
    await vi.waitFor(() => expect(mocks.prepareInterview).toHaveBeenCalledTimes(2));
    expect(mocks.prepareInterview.mock.calls[1]?.[1].force_refresh).toBe(true);
  });

  it("keeps keyboard focus on Generate and Regenerate through successful requests", async () => {
    let resolveFirst!: (value: ReturnType<typeof result>) => void;
    let resolveSecond!: (value: ReturnType<typeof result>) => void;
    mocks.prepareInterview
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    await render([submission(11)]);
    const generateButton = button("Generate questions")!;
    await activateByKeyboard(generateButton);
    expect(mocks.prepareInterview).toHaveBeenCalledTimes(1);
    expect(generateButton.disabled).toBe(false);
    expect(generateButton.getAttribute("aria-disabled")).toBe("true");
    await act(async () => resolveFirst(result()));
    await vi.waitFor(() => expect(document.body.textContent).toContain("Use a specific example"));
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(generateButton);
    expect(button("Generate questions")).toBe(generateButton);

    const regenerateButton = button("Regenerate questions")!;
    await activateByKeyboard(regenerateButton);
    expect(mocks.prepareInterview.mock.calls[1]?.[1].force_refresh).toBe(true);
    expect(regenerateButton.disabled).toBe(false);
    expect(regenerateButton.getAttribute("aria-disabled")).toBe("true");
    await act(async () => resolveSecond({ ...result(), questions: [{ question: "Updated question", suggested_answer: "Updated answer", source_locators: [] }] }));
    await vi.waitFor(() => expect(document.body.textContent).toContain("Updated question"));
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(regenerateButton);
    expect(button("Regenerate questions")).toBe(regenerateButton);
  });

  it("requires an explicit selection with multiple submissions", async () => {
    await render([submission(11), submission(12)]);
    expect(button("Generate questions")?.disabled).toBe(true);
    await change("interview-submission", "12");
    expect(button("Generate questions")?.disabled).toBe(false);
    expect(document.body.textContent).toContain("Based on confirmed historical submission #12");
  });

  it.each(["used_other_material", "materials_unknown"] as const)("requires current profile opt in for %s", async (choice) => {
    mocks.prepareInterview.mockResolvedValue(result("current_profile_fallback"));
    await render([submission(11, choice)]);
    expect(button("Generate questions")?.disabled).toBe(true);
    const fallback = document.querySelector('input[type="checkbox"]') as HTMLInputElement;
    await act(async () => fallback.click());
    await act(async () => button("Generate questions")?.click());
    await vi.waitFor(() => expect(mocks.prepareInterview).toHaveBeenCalled());
    expect(mocks.prepareInterview.mock.calls[0]?.[1]).toMatchObject({ submission_id: 11, current_profile_fallback: true });
    await vi.waitFor(() => expect(document.body.textContent).toContain("Using current profile as fallback"));
  });

  it("requires fallback without history and keeps a previous result after provider failure", async () => {
    const { ApiError } = await import("@/lib/api");
    mocks.prepareInterview.mockResolvedValueOnce(result("current_profile_fallback")).mockRejectedValueOnce(new ApiError("secret provider error", 502, null));
    await render([]);
    expect(button("Generate questions")?.disabled).toBe(true);
    await act(async () => (document.querySelector('input[type="checkbox"]') as HTMLInputElement).click());
    await act(async () => button("Generate questions")?.click());
    await vi.waitFor(() => expect(document.body.textContent).toContain("Use a specific example"));
    const regenerateButton = button("Regenerate questions")!;
    await activateByKeyboard(regenerateButton);
    await vi.waitFor(() => expect(document.body.textContent).toContain("temporarily unavailable"));
    expect(document.body.textContent).toContain("Use a specific example");
    expect(document.body.textContent).not.toContain("secret provider error");
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(regenerateButton);
  });
});
