import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ applicationsApi: { createDocumentReview: vi.fn(), getDocumentReview: vi.fn(), getTaskStatus: vi.fn(), acknowledgeDocumentReview: vi.fn() } }));
vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/button", () => ({ Button: (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props} /> }));
import { DocumentReviewPanel } from "@/components/applications/document-review-panel";

const packageItem = { id: 2, application_id: 1, revision: 1, content: {}, content_sha256: "a".repeat(64), schema_version: "application-package-v1", created_at: "2026-01-01" };
const detail = { id: "r", application_id: 1, package_id: 2, package_content_sha256: "a".repeat(64), review_policy_version: "application-document-review-v2", state: "completed" as const, outcome: "attention_needed" as const, findings: [{ id: "f", document: "resume" as const, category: "repetition", severity: "suggestion" as const, excerpt: "Repeated text", locator: "/resume", rationale: "Repeated", evidence: [{ role: "target" as const, document: "resume" as const, locator: "/resume", excerpt: "Repeated text" }], proposed_action: "Revise" }], failure_code: null, acknowledgments: [], created_at: "2026-01-01", completed_at: "2026-01-01" };
const summary = (overrides: Record<string, unknown> = {}) => ({ id: "r", task_id: "task", package_id: 2, package_content_sha256: "a".repeat(64), review_policy_version: "application-document-review-v1", state: "queued" as const, outcome: null, failure_code: null, ...overrides });
let node: HTMLDivElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;
afterEach(() => { root?.unmount(); node?.remove(); root = null; node = null; vi.clearAllMocks(); });

async function render(initialReview: ReturnType<typeof summary> | null, item = packageItem) {
  if (!node) { node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); }
  await act(async () => { root?.render(<DocumentReviewPanel applicationId={1} package={item} initialReview={initialReview} />); });
}

describe("DocumentReviewPanel", () => {
  it("shows not reviewed only when server lifecycle summary is null", async () => {
    await render(null);
    expect(node?.textContent).toContain("Not reviewed");
  });

  it("hydrates queued state and resumes polling from the persisted task id", async () => {
    api.applicationsApi.getTaskStatus.mockResolvedValue({ status: "running", message: "Reviewing" });
    await render(summary());
    await vi.waitFor(() => expect(api.applicationsApi.getTaskStatus).toHaveBeenCalledWith("task"));
    expect(node?.textContent).toContain("Review queued");
    expect(node?.textContent).not.toContain("Run review again");
    expect((node?.querySelector("button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("refetches the persisted report after a resumed terminal task", async () => {
    api.applicationsApi.getTaskStatus.mockResolvedValue({ status: "done", message: "Done", review_id: "r" });
    api.applicationsApi.getDocumentReview.mockResolvedValue(detail);
    await render(summary({ state: "running" }));
    await vi.waitFor(() => expect(api.applicationsApi.getDocumentReview).toHaveBeenCalledWith(1, 2, "r"));
    expect(node?.textContent).toContain("Repeated");
  });

  it("renders persisted failed state without polling or falling back to not reviewed", async () => {
    api.applicationsApi.getDocumentReview.mockResolvedValue({ ...detail, state: "failed", outcome: "insufficient_evidence", findings: [], failure_code: "unavailable" });
    await render(summary({ state: "failed", task_id: null, outcome: "insufficient_evidence", failure_code: "unavailable" }));
    await vi.waitFor(() => expect(node?.textContent).toContain("Review unavailable"));
    expect(api.applicationsApi.getTaskStatus).not.toHaveBeenCalled();
    expect(node?.textContent).not.toContain("Not reviewed");
  });

  it("loads an initially completed summary without polling its old task", async () => {
    api.applicationsApi.getDocumentReview.mockResolvedValue(detail);
    await render(summary({ state: "completed", outcome: "attention_needed", task_id: "task" }));
    await vi.waitFor(() => expect(node?.textContent).toContain("Repeated"));
    expect(api.applicationsApi.getTaskStatus).not.toHaveBeenCalled();
  });

  it("starts a completed review again with rerun enabled", async () => {
    api.applicationsApi.getDocumentReview.mockResolvedValue(detail);
    api.applicationsApi.createDocumentReview.mockResolvedValue({ task_id: "fresh-task", review_id: "fresh", state: "queued" });
    await render(summary({ state: "completed", outcome: "attention_needed", task_id: null }));
    await vi.waitFor(() => expect(node?.textContent).toContain("Run review again"));
    await act(async () => { (Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Run review again") as HTMLButtonElement).click(); });
    await vi.waitFor(() => expect(api.applicationsApi.createDocumentReview).toHaveBeenCalledWith(1, 2, expect.any(String), true));
  });

  it("retries a failed review as a fresh non-rerun request", async () => {
    api.applicationsApi.getDocumentReview.mockResolvedValue({ ...detail, state: "failed", outcome: "insufficient_evidence", findings: [], failure_code: "unavailable" });
    api.applicationsApi.createDocumentReview.mockResolvedValue({ task_id: "retry-task", review_id: "retry", state: "queued" });
    await render(summary({ state: "failed", task_id: null, outcome: "insufficient_evidence", failure_code: "unavailable" }));
    await vi.waitFor(() => expect(node?.textContent).toContain("Review documents"));
    await act(async () => { (Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Review documents") as HTMLButtonElement).click(); });
    expect(api.applicationsApi.createDocumentReview).toHaveBeenCalledWith(1, 2, expect.any(String), false);
  });

  it("keeps a persisted acknowledgment selected after reload", async () => {
    api.applicationsApi.getDocumentReview.mockResolvedValue({ ...detail, acknowledgments: [{ finding_id: "f", disposition: "will_revise" }] });
    await render(summary({ state: "completed", outcome: "attention_needed", task_id: null }));
    await vi.waitFor(() => expect((Array.from(node!.querySelectorAll("button")).find((button) => button.textContent === "Will revise") as HTMLButtonElement).getAttribute("aria-pressed")).toBe("true"));
  });

  it("shows bounded status-unavailable when an active persisted task cannot be recovered", async () => {
    api.applicationsApi.getTaskStatus.mockRejectedValue(new Error("expired"));
    api.applicationsApi.getDocumentReview.mockResolvedValue({ ...detail, state: "running", outcome: null, findings: [], failure_code: null });
    await render(summary({ state: "running" }));
    await vi.waitFor(() => expect(node?.textContent).toContain("Review status unavailable"));
  });

  it("does not let an old completed response replace a newly selected package", async () => {
    let resolveDetail: ((value: typeof detail) => void) | undefined;
    api.applicationsApi.getDocumentReview.mockImplementation(() => new Promise((resolve) => { resolveDetail = resolve; }));
    await render(summary({ state: "completed", outcome: "attention_needed", task_id: null }));
    await vi.waitFor(() => expect(api.applicationsApi.getDocumentReview).toHaveBeenCalled());
    await render(null, { ...packageItem, id: 3, content_sha256: "b".repeat(64) });
    await act(async () => { resolveDetail?.(detail); });
    expect(node?.textContent).toContain("Not reviewed");
    expect(node?.textContent).not.toContain("Repeated");
  });

  it("cleans up active polling when unmounted", async () => {
    const clearInterval = vi.spyOn(window, "clearInterval");
    api.applicationsApi.getTaskStatus.mockResolvedValue({ status: "running", message: "Reviewing" });
    await render(summary());
    root?.unmount();
    expect(clearInterval).toHaveBeenCalled();
  });
});
