import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApplicationWorkspace } from "@/components/applications/application-workspace";
import { PackagePreview } from "@/components/applications/package-preview";

const apiMocks = vi.hoisted(() => ({
  getWorkspace: vi.fn(), getPackage: vi.fn(), patchPreparation: vi.fn(), createPackage: vi.fn(), approvePreparation: vi.fn(), createAttempt: vi.fn(), getTimeline: vi.fn(),
  listDrafts: vi.fn(), getDraft: vi.fn(), getByJobId: vi.fn(), tailorResume: vi.fn(), generate: vi.fn(),
  ApiError: class ApiError extends Error { status: number; constructor(status = 500) { super("API error"); this.status = status; } },
}));

vi.mock("@/lib/api", () => ({
  ApiError: apiMocks.ApiError,
  applicationsApi: { getWorkspace: apiMocks.getWorkspace, getPackage: apiMocks.getPackage, patchPreparation: apiMocks.patchPreparation, createPackage: apiMocks.createPackage, approvePreparation: apiMocks.approvePreparation, createAttempt: apiMocks.createAttempt, getTimeline: apiMocks.getTimeline },
  resumeApi: { listDrafts: apiMocks.listDrafts, getDraft: apiMocks.getDraft, tailorResume: apiMocks.tailorResume },
  coverLettersApi: { getByJobId: apiMocks.getByJobId, generate: apiMocks.generate },
}));
vi.mock("@/components/resume/tailor-resume-dialog", () => ({ TailorResumeDialog: ({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) => <div data-testid="resume-editor"><span>Local resume editor stays mounted</span><button onClick={() => onDirtyChange?.(true)}>Make resume dirty</button><button onClick={() => onDirtyChange?.(false)}>Mark resume clean</button></div> }));
vi.mock("@/components/cover/cover-letter-dialog", () => ({ CoverLetterDialog: ({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) => <div data-testid="letter-editor"><button onClick={() => onDirtyChange?.(true)}>Make letter dirty</button></div> }));

const workspace = (overrides: Record<string, unknown> = {}) => ({
  application: { id: 7, job_id: 41, status: "saved", cover_letter_id: null, notes: null, next_action_type: null, next_action_note: null, next_action_due_date: null, next_action_completed_at: null, created_at: "2026-01-01", updated_at: "2026-01-01" },
  job: { id: 41, title: "Backend Engineer", company: "Acme", location: "Remote", source_url: null, description: null, status: "active", live_context_available: true, open_job_available: false },
  match_summary: null,
  preparation: { application_id: 7, version: 3, state: "not_started", fit_acknowledged: false, fit_context_available: false, resume_draft_id: null, resume_current: false, cover_letter_mode: "unset", cover_letter_id: null, cover_letter_current: false, manual_questions: [], approved_package_id: null, approval_current: false },
  selected_resume: null, selected_cover_letter: null, approved_package: null, package_history: [], activity: { application_id: 7, events: [] }, ...overrides,
});
const preparation = (overrides: Record<string, unknown> = {}) => ({ ...workspace().preparation, ...overrides });
const reviewableWorkspace = (overrides: Record<string, unknown> = {}) => workspace({
  job: { ...workspace().job, open_job_available: true },
  preparation: preparation({ state: "needs_review" }),
  ...overrides,
});

function button(node: HTMLElement, label: string) {
  return Array.from(node.querySelectorAll("button")).find((item) => item.textContent === label);
}

async function fill(node: HTMLElement, id: string, value: string) {
  const field = node.querySelector(`#${id}`) as HTMLTextAreaElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("PackagePreview", () => {
  it("renders the frozen package payload rather than a mutable source", async () => {
    const node = document.createElement("div");
    const root = createRoot(node);
    const item = {
      id: 1, application_id: 2, revision: 3, content_sha256: "a".repeat(64),
      schema_version: "application-package-v1", created_at: "2026-01-01T00:00:00Z",
      content: { job: { title: "Frozen role" }, resume: { professional_summary: "Frozen resume" }, cover_letter: { content: "Frozen letter" } }, artifacts: [],
    };
    await act(async () => root.render(<PackagePreview package={item} />));
    expect(node.textContent).toContain("Frozen package preview");
    expect(node.textContent).toContain("Frozen resume");
    expect(node.textContent).toContain("Frozen letter");
    root.unmount();
  });
});

describe("ApplicationWorkspace saved-document actions", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let node: HTMLDivElement | null = null;

  afterEach(() => { root?.unmount(); node?.remove(); root = null; node = null; vi.clearAllMocks(); });

  it("creates a fresh approved-package attempt and opens only the server URL", async () => {
    const approved = { id: 12, application_id: 7, revision: 2, content_sha256: "b".repeat(64), schema_version: "application-package-v1", created_at: "2026-01-02T00:00:00Z", content: {} };
    apiMocks.getWorkspace.mockResolvedValue(workspace({ job: { ...workspace().job, open_job_available: true }, preparation: preparation({ state: "ready", approval_current: true, approved_package_id: 12 }), approved_package: approved }));
    apiMocks.getTimeline.mockResolvedValue({ application_id: 7, items: [], next_cursor: null });
    apiMocks.createAttempt.mockResolvedValue({ attempt_id: "attempt-1", expires_at: "2026-01-03T00:00:00Z", external_url: "https://employer.example.test/apply" });
    apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    const handoff = { close: vi.fn(), location: { assign: vi.fn() }, opener: window } as unknown as Window;
    const open = vi.spyOn(window, "open").mockImplementation(() => handoff);
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    const openButton = () => Array.from(node!.querySelectorAll("button")).find((item) => item.textContent?.includes("Open application"));
    await vi.waitFor(() => expect(openButton()).toBeTruthy());
    await act(async () => { openButton()?.click(); });
    await vi.waitFor(() => expect(apiMocks.createAttempt).toHaveBeenCalledWith(7, 3, 12, "b".repeat(64)));
    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(handoff.location.assign).toHaveBeenCalledWith("https://employer.example.test/apply");
    open.mockRestore();
  });

  it("does not open an external page when attempt creation fails", async () => {
    const approved = { id: 12, application_id: 7, revision: 2, content_sha256: "b".repeat(64), schema_version: "application-package-v1", created_at: "2026-01-02T00:00:00Z", content: {} };
    apiMocks.getWorkspace.mockResolvedValue(workspace({ job: { ...workspace().job, open_job_available: true }, preparation: preparation({ state: "ready", approval_current: true, approved_package_id: 12 }), approved_package: approved }));
    apiMocks.getTimeline.mockResolvedValue({ application_id: 7, items: [], next_cursor: null });
    apiMocks.createAttempt.mockRejectedValue(new apiMocks.ApiError(409));
    apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    const handoff = { close: vi.fn(), location: { assign: vi.fn() }, opener: window } as unknown as Window;
    const open = vi.spyOn(window, "open").mockImplementation(() => handoff);
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    const openButton = () => Array.from(node!.querySelectorAll("button")).find((item) => item.textContent?.includes("Open application"));
    await vi.waitFor(() => expect(openButton()).toBeTruthy());
    await act(async () => { openButton()?.click(); });
    await vi.waitFor(() => expect(apiMocks.getWorkspace).toHaveBeenCalledTimes(2));
    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(handoff.close).toHaveBeenCalled();
    open.mockRestore();
  });

  it("lazily views a non-approved historical package and returns to the current preview", async () => {
    const current = { id: 12, application_id: 7, revision: 2, content_sha256: "b".repeat(64), schema_version: "application-package-v1", created_at: "2026-01-02T00:00:00Z", content: { job: { title: "Current role" }, resume: { professional_summary: "Current resume" }, cover_letter: { content: "Current letter" } } };
    const historical = { id: 11, application_id: 7, revision: 1, content_sha256: "a".repeat(64), schema_version: "application-package-v1", created_at: "2026-01-01T00:00:00Z", content: { job: { title: "Frozen historical role" }, resume: { professional_summary: "Frozen historical resume" }, cover_letter: { content: "Frozen historical letter" } } };
    apiMocks.getWorkspace.mockResolvedValue(workspace({ approved_package: current, current_reviewable_package: current, package_history: [{ id: 11, revision: 1, content_sha256: historical.content_sha256, created_at: historical.created_at, is_approved: false }, { id: 12, revision: 2, content_sha256: current.content_sha256, created_at: current.created_at, is_approved: true }] }));
    apiMocks.getPackage.mockResolvedValue(historical); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Current resume"));
    expect(apiMocks.getPackage).not.toHaveBeenCalled();
    await act(async () => { button(node!, "View")?.click(); });
    await vi.waitFor(() => expect(apiMocks.getPackage).toHaveBeenCalledWith(7, 11));
    await vi.waitFor(() => expect(node?.textContent).toContain("Frozen historical resume"));
    expect(node?.textContent).not.toContain("Current resume");
    expect(apiMocks.generate).not.toHaveBeenCalled();
    await act(async () => { button(node!, "Back to current package")?.click(); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Current resume"));
    await act(async () => { button(node!, "View")?.click(); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Frozen historical resume"));
  });

  it("shows explicit loading and handled historical package errors without a mutable fallback", async () => {
    const current = { id: 12, application_id: 7, revision: 2, content_sha256: "b".repeat(64), schema_version: "application-package-v1", created_at: "2026-01-02T00:00:00Z", content: { resume: { professional_summary: "Current resume" } } };
    const history = [{ id: 11, revision: 1, content_sha256: "a".repeat(64), created_at: "2026-01-01T00:00:00Z", is_approved: false }];
    apiMocks.getWorkspace.mockResolvedValue(workspace({ approved_package: current, current_reviewable_package: current, package_history: history })); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    apiMocks.getPackage.mockReturnValue(new Promise(() => {}));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(button(node!, "View")).toBeTruthy());
    await act(async () => { button(node!, "View")?.click(); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Loading package revision"));
    expect(node?.textContent).not.toContain("Current resume");
    root?.unmount(); node?.remove(); root = null; node = null; vi.clearAllMocks();

    apiMocks.getWorkspace.mockResolvedValue(workspace({ approved_package: current, current_reviewable_package: current, package_history: history })); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404)); apiMocks.getPackage.mockRejectedValueOnce(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(button(node!, "View")).toBeTruthy()); await act(async () => { button(node!, "View")?.click(); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Package revision is unavailable."));
    expect(node?.textContent).not.toContain("Current resume");
    root?.unmount(); node?.remove(); root = null; node = null; vi.clearAllMocks();

    apiMocks.getWorkspace.mockResolvedValue(workspace({ approved_package: current, current_reviewable_package: current, package_history: history })); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404)); apiMocks.getPackage.mockRejectedValueOnce(new Error("network"));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(button(node!, "View")).toBeTruthy()); await act(async () => { button(node!, "View")?.click(); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Could not load this package revision."));
  });

  it("selects same-job saved records without generation", async () => {
    apiMocks.getWorkspace.mockResolvedValue(workspace());
    apiMocks.listDrafts.mockResolvedValue([{ id: 11, job_id: 41, job_title: "Backend Engineer", job_company: "Acme", created_at: "2026-01-01", updated_at: "2026-01-02" }]);
    apiMocks.getByJobId.mockResolvedValue({ id: 12, job_id: 41, content: "Saved letter", tone: null, model: "fixture", is_edited: false, created_at: "2026-01-01", updated_at: "2026-01-02" });
    apiMocks.patchPreparation.mockResolvedValue(workspace().preparation);
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { root?.render(<QueryClientProvider client={client}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Select saved resume"));
    const buttons = Array.from(node!.querySelectorAll("button"));
    await act(async () => { buttons.find((button) => button.textContent === "Select saved resume")?.click(); });
    expect(apiMocks.patchPreparation).toHaveBeenCalledWith(7, { expected_version: 3, resume_draft_id: 11 });
    await vi.waitFor(() => expect(node?.textContent).toContain("Select saved letter"));
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Select saved letter")?.click(); });
    expect(apiMocks.patchPreparation).toHaveBeenCalledWith(7, { expected_version: 3, cover_letter_mode: "selected", cover_letter_id: 12 });
    expect(apiMocks.tailorResume).not.toHaveBeenCalled();
    expect(apiMocks.generate).not.toHaveBeenCalled();
  });

  it("keeps not-needed independent of saved letter selection", async () => {
    apiMocks.getWorkspace.mockResolvedValue(workspace()); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockResolvedValue({ id: 12, job_id: 41, content: "Saved letter" }); apiMocks.patchPreparation.mockResolvedValue(workspace().preparation);
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(node?.textContent).toContain("No cover letter needed"));
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "No cover letter needed")?.click(); });
    expect(apiMocks.patchPreparation).toHaveBeenCalledWith(7, { expected_version: 3, cover_letter_mode: "not_needed", cover_letter_id: null });
  });

  it("blocks package creation while an editor has unsaved content", async () => {
    apiMocks.getWorkspace.mockResolvedValue(reviewableWorkspace()); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Tailor new resume"));
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Tailor new resume")?.click(); });
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Make resume dirty")?.click(); });
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Review package")?.click(); });
    expect(apiMocks.createPackage).not.toHaveBeenCalled();
  });

  it("blocks package creation while an otherwise eligible letter editor is dirty", async () => {
    apiMocks.getWorkspace.mockResolvedValue(reviewableWorkspace()); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Create cover letter"));
    await act(async () => { button(node!, "Create cover letter")?.click(); });
    await act(async () => { button(node!, "Make letter dirty")?.click(); });
    await act(async () => { button(node!, "Review package")?.click(); });
    expect(apiMocks.createPackage).not.toHaveBeenCalled();
  });

  it("refetches after a 409 without unmounting an open editor", async () => {
    apiMocks.getWorkspace.mockResolvedValue(reviewableWorkspace()); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    apiMocks.createPackage.mockRejectedValueOnce(new apiMocks.ApiError(409));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Tailor new resume"));
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Tailor new resume")?.click(); });
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Mark resume clean")?.click(); });
    await act(async () => { Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Review package")?.click(); });
    await vi.waitFor(() => expect(apiMocks.createPackage).toHaveBeenCalledWith(7, 3, expect.any(String)));
    await vi.waitFor(() => expect(apiMocks.getWorkspace).toHaveBeenCalledTimes(2));
    expect(node?.textContent).toContain("Local resume editor stays mounted");
  });

  it.each([
    ["not_started", reviewableWorkspace({ preparation: preparation({ state: "not_started" }) })],
    ["in_progress", reviewableWorkspace({ preparation: preparation({ state: "in_progress" }) })],
    ["an incomplete manual question", reviewableWorkspace({ preparation: preparation({ state: "in_progress", manual_questions: [{ id: "00000000-0000-0000-0000-000000000001", question: "Portfolio?", answer: "", acknowledged: false }] }) })],
    ["a stale resume", reviewableWorkspace({ preparation: preparation({ state: "in_progress", resume_draft_id: 11, resume_current: false }) })],
    ["a stale letter", reviewableWorkspace({ preparation: preparation({ state: "in_progress", cover_letter_mode: "selected", cover_letter_id: 12, cover_letter_current: false }) })],
    ["a closed job", workspace({ job: { ...workspace().job, status: "closed", open_job_available: false }, preparation: preparation({ state: "needs_review" }) })],
  ])("does not create a package for %s", async (_label, value) => {
    apiMocks.getWorkspace.mockResolvedValue(value); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(button(node!, "Review package")?.disabled).toBe(true));
    await act(async () => { button(node!, "Review package")?.click(); });
    expect(apiMocks.createPackage).not.toHaveBeenCalled();
  });

  it("allows clean needs-review and ready preparation to review packages", async () => {
    for (const state of ["needs_review", "ready"] as const) {
      apiMocks.getWorkspace.mockResolvedValue(reviewableWorkspace({ preparation: preparation({ state }) })); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404)); apiMocks.createPackage.mockResolvedValue({ id: 1, application_id: 7, revision: 1, content: { job: {}, resume: {}, cover_letter: {} }, content_sha256: "a".repeat(64), schema_version: "application-package-v1", created_at: "2026-01-01" });
      node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
      await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
      await vi.waitFor(() => expect(button(node!, "Review package")?.disabled).toBe(false));
      await act(async () => { button(node!, "Review package")?.click(); });
      await vi.waitFor(() => expect(apiMocks.createPackage).toHaveBeenCalled());
      root.unmount(); node.remove(); root = null; node = null; vi.clearAllMocks();
    }
  });

  it("adds, completes, edits, and removes questions using the authoritative full collection", async () => {
    const first = { id: "00000000-0000-0000-0000-000000000001", question: "Work authorization?", answer: "Yes", acknowledged: true };
    const second = { id: "00000000-0000-0000-0000-000000000002", question: "Portfolio?", answer: "", acknowledged: false };
    const initial = reviewableWorkspace({ preparation: preparation({ state: "in_progress", manual_questions: [first, second] }) });
    const added = reviewableWorkspace({ preparation: preparation({ state: "in_progress", version: 4, manual_questions: [first, second, { id: "00000000-0000-0000-0000-000000000003", question: "Availability?", answer: "Immediately", acknowledged: false }] }) });
    apiMocks.getWorkspace.mockResolvedValueOnce(initial).mockResolvedValue(added); apiMocks.patchPreparation.mockResolvedValue(added.preparation); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(node?.textContent).toContain("Work authorization?"));
    await fill(node!, "new-manual-question", "Availability?"); await fill(node!, "new-manual-answer", "Immediately");
    await act(async () => { button(node!, "Add question")?.click(); });
    expect(apiMocks.patchPreparation).toHaveBeenCalledWith(7, { expected_version: 3, manual_questions: [first, second, { question: "Availability?", answer: "Immediately", acknowledged: false }] });
    await vi.waitFor(() => expect(node?.textContent).toContain("Availability?"));
    await act(async () => { button(node!, "Mark complete")?.click(); });
    await vi.waitFor(() => expect(apiMocks.patchPreparation).toHaveBeenLastCalledWith(7, { expected_version: 4, manual_questions: [first, { ...second, acknowledged: true }, added.preparation.manual_questions[2]] }));
  });

  it("edits and removes only the targeted persisted item", async () => {
    const first = { id: "00000000-0000-0000-0000-000000000001", question: "Work authorization?", answer: "Yes", acknowledged: true };
    const second = { id: "00000000-0000-0000-0000-000000000002", question: "Portfolio?", answer: "Old link", acknowledged: true };
    const editedFirst = { ...first, answer: "Updated yes", acknowledged: false };
    const edited = reviewableWorkspace({ preparation: preparation({ state: "in_progress", version: 4, manual_questions: [editedFirst, second] }) });
    apiMocks.getWorkspace.mockResolvedValueOnce(reviewableWorkspace({ preparation: preparation({ manual_questions: [first, second] }) })).mockResolvedValue(edited); apiMocks.patchPreparation.mockResolvedValue(edited.preparation); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(button(node!, "Edit")).toBeTruthy());
    await act(async () => { button(node!, "Edit")?.click(); }); await fill(node!, `manual-answer-${first.id}`, "Updated yes");
    await act(async () => { button(node!, "Save changes")?.click(); });
    expect(apiMocks.patchPreparation).toHaveBeenCalledWith(7, { expected_version: 3, manual_questions: [{ ...first, answer: "Updated yes" }, second] });
    await vi.waitFor(() => expect(node?.textContent).toContain("Updated yes"));
    const removeButtons = Array.from(node!.querySelectorAll("button")).filter((item) => item.textContent === "Remove");
    await act(async () => { removeButtons[1]?.click(); });
    await vi.waitFor(() => expect(apiMocks.patchPreparation).toHaveBeenLastCalledWith(7, { expected_version: 4, manual_questions: [editedFirst] }));
  });

  it("preserves edit input and refetches without retrying a 409 manual-question update", async () => {
    const item = { id: "00000000-0000-0000-0000-000000000001", question: "Portfolio?", answer: "Old", acknowledged: true };
    apiMocks.getWorkspace.mockResolvedValue(reviewableWorkspace({ preparation: preparation({ manual_questions: [item] }) })); apiMocks.patchPreparation.mockRejectedValueOnce(new apiMocks.ApiError(409)); apiMocks.listDrafts.mockResolvedValue([]); apiMocks.getByJobId.mockRejectedValue(new apiMocks.ApiError(404));
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    await act(async () => { root?.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ApplicationWorkspace applicationId={7} /></QueryClientProvider>); });
    await vi.waitFor(() => expect(button(node!, "Edit")).toBeTruthy());
    await act(async () => { button(node!, "Edit")?.click(); }); await fill(node!, `manual-answer-${item.id}`, "Updated");
    await act(async () => { button(node!, "Save changes")?.click(); });
    await vi.waitFor(() => expect(apiMocks.patchPreparation).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(apiMocks.getWorkspace).toHaveBeenCalledTimes(2));
    expect((node!.querySelector(`#manual-answer-${item.id}`) as HTMLTextAreaElement).value).toBe("Updated");
    expect(apiMocks.patchPreparation).toHaveBeenCalledWith(7, { expected_version: 3, manual_questions: [{ ...item, answer: "Updated" }] });
  });
});
