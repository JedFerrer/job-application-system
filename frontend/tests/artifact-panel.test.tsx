import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { ArtifactPanel } from "@/components/applications/artifact-panel";
import type { ApplicationPackage, ArtifactDetail, ArtifactSummary } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  buildResumePdf: vi.fn(), buildResumeDocx: vi.fn(), buildCoverLetterPdf: vi.fn(), buildCoverLetterDocx: vi.fn(),
  uploadArtifact: vi.fn(), downloadArtifact: vi.fn(), getArtifact: vi.fn(), retryArtifactValidation: vi.fn(), deleteArtifact: vi.fn(),
  ApiError: class ApiError extends Error { constructor(public status: number) { super("Request failed"); } },
}));
vi.mock("@/lib/api", () => ({ ApiError: mocks.ApiError, applicationsApi: {
  uploadArtifact: mocks.uploadArtifact, downloadArtifact: mocks.downloadArtifact, getArtifact: mocks.getArtifact,
  retryArtifactValidation: mocks.retryArtifactValidation, deleteArtifact: mocks.deleteArtifact,
} }));
vi.mock("@/lib/cover-letter-export", () => ({
  buildResumePdf: mocks.buildResumePdf, buildResumeDocx: mocks.buildResumeDocx,
  buildCoverLetterPdf: mocks.buildCoverLetterPdf, buildCoverLetterDocx: mocks.buildCoverLetterDocx,
}));

const summary = (overrides: Partial<ArtifactSummary> = {}): ArtifactSummary => ({
  id: 81, package_id: 12, document_type: "resume", format: "pdf", filename: "saved.pdf", byte_length: 8,
  sha256: "a".repeat(64), validation_state: "validated", failure_category: null, created_at: "2026-09-01T00:00:00Z", ...overrides,
});
const detail = (overrides: Partial<ArtifactDetail> = {}): ArtifactDetail => ({
  ...summary(), application_id: 7, content_type: "application/pdf", exporter_version: "resume-pdf-v1",
  expected_text_sha256: "b".repeat(64), validation_policy_version: "v1", task_id: null, retry_after: null,
  validation_report: { policy_version: "v1", format: "pdf", state: "validated", page_count: 1, failure_category: null,
    checks: [{ name: "blank_pages", status: "pass", category: null }] }, ...overrides,
});
const item = (overrides: Partial<ApplicationPackage> = {}): ApplicationPackage => ({
  id: 12, application_id: 7, revision: 2, content_sha256: "c".repeat(64), schema_version: "application-package-v1",
  created_at: "2026-09-01T00:00:00Z", artifacts: [], content: { resume: { content: {
    job_id: 41, professional_summary: "Frozen summary", full_name: "Ada", email: null, phone: null, headline: null,
    work_history: [], education: [], certifications: [], matched_skills: [], other_skills: [], missing_keywords: [],
  } }, cover_letter: { mode: "selected", content: "Frozen letter" } }, ...overrides,
});

describe("ArtifactPanel", () => {
  let node: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let client: QueryClient;
  let downloadClick: ReturnType<typeof vi.spyOn>;
  const render = async (pkg: ApplicationPackage, current = true) => {
    await act(async () => root.render(<QueryClientProvider client={client}><ArtifactPanel key={pkg.id} applicationId={7} package={pkg} current={current} /></QueryClientProvider>));
  };
  const click = async (label: string) => {
    const button = Array.from(node.querySelectorAll("button")).find((candidate) => candidate.textContent === label);
    expect(button, label).toBeTruthy();
    await act(async () => button!.click());
  };
  beforeEach(() => {
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:saved"), revokeObjectURL: vi.fn() });
    downloadClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    mocks.getArtifact.mockResolvedValue(detail());
    mocks.downloadArtifact.mockResolvedValue({ blob: new Blob(["stored"]), filename: "saved.pdf" });
  });
  afterEach(() => { vi.useRealTimers(); act(() => root.unmount()); node.remove(); client.clear(); downloadClick.mockRestore(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

  it("builds from frozen content, uploads the same Blob, then downloads stored bytes", async () => {
    const blob = new Blob(["built"]);
    mocks.buildResumePdf.mockReturnValue({ blob, filename: "built.pdf", exporterVersion: "resume-pdf-v1" });
    mocks.uploadArtifact.mockResolvedValue({ artifact: detail({ validation_state: "queued" }), existing: false });
    await render(item());
    await click("Generate Resume PDF");
    expect(mocks.buildResumePdf.mock.calls[0]?.[0].professional_summary).toBe("Frozen summary");
    expect(mocks.uploadArtifact).toHaveBeenCalledWith(7, 12, blob, "built.pdf", "resume", "pdf", "resume-pdf-v1");
    expect(mocks.downloadArtifact).toHaveBeenCalledWith(7, 81, "saved.pdf");
    expect(downloadClick).toHaveBeenCalledTimes(1);
  });

  it("offers four independent generation actions for a selected letter", async () => {
    const built = { blob: new Blob(["built"]), filename: "built.docx", exporterVersion: "v1" };
    mocks.buildResumeDocx.mockResolvedValue(built);
    mocks.buildCoverLetterPdf.mockReturnValue(built);
    mocks.buildCoverLetterDocx.mockResolvedValue(built);
    mocks.uploadArtifact.mockResolvedValue({ artifact: detail(), existing: false });
    await render(item());
    await click("Generate Resume DOCX");
    await click("Generate Cover Letter PDF");
    await click("Generate Cover Letter DOCX");
    expect(mocks.buildResumeDocx.mock.calls[0]?.[0].professional_summary).toBe("Frozen summary");
    expect(mocks.buildCoverLetterPdf).toHaveBeenCalledWith("Frozen letter", "cover_letter_package_12");
    expect(mocks.buildCoverLetterDocx).toHaveBeenCalledWith("Frozen letter", "cover_letter_package_12");
    expect(mocks.uploadArtifact.mock.calls.map((call) => [call[4], call[5]])).toEqual([
      ["resume", "docx"], ["cover_letter", "pdf"], ["cover_letter", "docx"],
    ]);
  });

  it("finishes an in-flight export for the captured package after selection changes", async () => {
    let resolveBuild!: (value: { blob: Blob; filename: string; exporterVersion: string }) => void;
    mocks.buildResumeDocx.mockReturnValue(new Promise((resolve) => { resolveBuild = resolve; }));
    mocks.uploadArtifact.mockResolvedValue({ artifact: detail(), existing: false });
    await render(item());
    await click("Generate Resume DOCX");
    const replacement = item({ id: 13, content_sha256: "d".repeat(64), artifacts: [] });
    await render(replacement);
    const blob = new Blob(["package 12"]);
    await act(async () => resolveBuild({ blob, filename: "old.docx", exporterVersion: "v1" }));
    await vi.waitFor(() => expect(mocks.uploadArtifact).toHaveBeenCalledWith(7, 12, blob, "old.docx", "resume", "docx", "v1"));
    expect(node.textContent).not.toContain("File saved");
    expect(node.textContent).toContain("No exported files saved for this package");
  });

  it("keeps saved state when stored download fails and identifies a duplicate", async () => {
    mocks.buildResumePdf.mockReturnValue({ blob: new Blob(["built"]), filename: "built.pdf", exporterVersion: "v1" });
    mocks.uploadArtifact.mockResolvedValue({ artifact: detail(), existing: true });
    mocks.downloadArtifact.mockRejectedValue(new Error("offline"));
    await render(item({ artifacts: [summary()] }));
    await click("Generate Resume PDF");
    expect(node.textContent).toContain("File saved, but download failed");
    expect(node.textContent).toContain("saved.pdf");
    expect(mocks.retryArtifactValidation).not.toHaveBeenCalled();
    mocks.downloadArtifact.mockResolvedValue({ blob: new Blob(["stored"]), filename: "saved.pdf" });
    await click("Download");
    expect(downloadClick).toHaveBeenCalledTimes(1);
  });

  it("identifies an existing exact file without restarting validation", async () => {
    mocks.buildResumePdf.mockReturnValue({ blob: new Blob(["built"]), filename: "built.pdf", exporterVersion: "v1" });
    mocks.uploadArtifact.mockResolvedValue({ artifact: detail({ validation_state: "warning" }), existing: true });
    await render(item());
    await click("Generate Resume PDF");
    expect(node.textContent).toContain("This exact file is already saved");
    expect(mocks.retryArtifactValidation).not.toHaveBeenCalled();
    expect(mocks.downloadArtifact).toHaveBeenCalledWith(7, 81, "saved.pdf");
  });

  it.each([[413, "size or application storage limit"], [415, "format is unsupported"], [422, "could not be accepted or read"], [503, "temporarily unavailable"]])("explains upload status %i", async (status, copy) => {
    mocks.buildResumePdf.mockReturnValue({ blob: new Blob(["built"]), filename: "built.pdf", exporterVersion: "v1" });
    mocks.uploadArtifact.mockRejectedValue(new mocks.ApiError(status));
    await render(item());
    await click("Generate Resume PDF");
    expect(node.textContent).toContain(copy);
    expect(mocks.downloadArtifact).not.toHaveBeenCalled();
  });

  it("renders historical saved files without generation and permits download", async () => {
    await render(item({ artifacts: [summary({ validation_state: "failed" })] }), false);
    expect(node.textContent).toContain("saved.pdf");
    expect(node.textContent).not.toContain("Generate Resume PDF");
    await click("Download");
    expect(mocks.downloadArtifact).toHaveBeenCalledWith(7, 81, "saved.pdf");
  });

  it("recovers persisted rows and active detail checks with a fresh query cache", async () => {
    mocks.getArtifact.mockResolvedValue(detail({ validation_state: "queued" }));
    await render(item({ artifacts: [summary({ validation_state: "queued" }), summary({ id: 82, validation_state: "warning" })] }));
    await vi.waitFor(() => expect(mocks.getArtifact).toHaveBeenCalledWith(7, 81));
    expect(mocks.getArtifact).not.toHaveBeenCalledWith(7, 82);
    act(() => root.unmount()); client.clear();
    root = createRoot(node); client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mocks.getArtifact.mockClear();
    await render(item({ artifacts: [summary({ validation_state: "queued" }), summary({ id: 82, validation_state: "warning" })] }));
    expect(node.textContent).toContain("Queued for checking");
    expect(node.textContent).toContain("Checked with warnings");
    await vi.waitFor(() => expect(mocks.getArtifact).toHaveBeenCalledWith(7, 81));
    expect(mocks.getArtifact).not.toHaveBeenCalledWith(7, 82);
  });

  it("hides letter generation for a not-needed package", async () => {
    await render(item({ content: { ...item().content, cover_letter: { mode: "not_needed" } } }));
    expect(node.textContent).toContain("Generate Resume PDF");
    expect(node.textContent).not.toContain("Generate Cover Letter");
  });

  it("shows the persisted PDF report", async () => {
    await render(item({ artifacts: [summary()] }));
    await click("View report");
    await vi.waitFor(() => expect(node.textContent).toContain("Blank pages: pass"));
    expect(node.textContent).toContain("Page count: 1");
  });

  it.each([
    ["validated", "Content and structure checked", true],
    ["warning", "Content and structure checked", true],
    ["queued", "Content and structure checks are waiting to run", false],
    ["validating", "Content and structure checks are in progress", false],
    ["failed", "Validation failed before all checks could be confirmed", false],
    ["unavailable", "Validation is unavailable, so content and structure checks could not be confirmed", false],
  ] as const)("uses truthful DOCX copy for %s", async (state, expected, completed) => {
    const docxReport = detail({
      format: "docx", validation_state: state,
      validation_report: { policy_version: "v1", format: "docx", state, page_count: null, failure_category: null, checks: [] },
    });
    mocks.getArtifact.mockResolvedValue(docxReport);
    await render(item({ artifacts: [summary({ format: "docx", validation_state: state })] }));
    await click("View report");
    await vi.waitFor(() => expect(node.textContent).toContain(expected));
    expect(node.textContent?.includes("Content and structure checked")).toBe(completed);
    expect(node.textContent).toContain("Page layout, pagination, and clipping");
    expect(node.textContent).toContain("not checked");
  });

  it("keeps returned checks visible when DOCX validation fails partway through", async () => {
    mocks.getArtifact.mockResolvedValue(detail({
      format: "docx", validation_state: "failed",
      validation_report: { policy_version: "v1", format: "docx", state: "failed", page_count: null,
        failure_category: "parser_timeout", checks: [{ name: "content_match", status: "pass", category: null }] },
    }));
    await render(item({ artifacts: [summary({ format: "docx", validation_state: "failed" })] }));
    await click("View report");
    await vi.waitFor(() => expect(node.textContent).toContain("Content: pass"));
    expect(node.textContent).toContain("Validation failed before all checks could be confirmed");
    expect(node.textContent).not.toContain("Content and structure checked");
    expect(node.textContent).toContain("Page layout, pagination, and clipping were not checked");
  });

  it("retries failed validation on the same artifact and handles a 409", async () => {
    mocks.getArtifact.mockResolvedValue(detail({ validation_state: "failed" }));
    mocks.retryArtifactValidation.mockRejectedValue(new mocks.ApiError(409));
    await render(item({ artifacts: [summary({ validation_state: "failed" })] }));
    await click("Retry validation");
    expect(mocks.retryArtifactValidation).toHaveBeenCalledWith(7, 81);
    expect(node.textContent).toContain("Validation is still in progress");
  });

  it("allows an unavailable file to retry validation without creating an artifact", async () => {
    mocks.getArtifact.mockResolvedValue(detail({ validation_state: "unavailable" }));
    mocks.retryArtifactValidation.mockResolvedValue({ artifact: detail({ validation_state: "queued" }), existing: true });
    await render(item({ artifacts: [summary({ validation_state: "unavailable" })] }));
    await click("Retry validation");
    expect(mocks.retryArtifactValidation).toHaveBeenCalledWith(7, 81);
    expect(mocks.uploadArtifact).not.toHaveBeenCalled();
    expect(node.textContent).toContain("Validation queued for this saved file");
  });

  it("uses retry_after for active files and never retries validated files", async () => {
    mocks.getArtifact.mockResolvedValue(detail({ validation_state: "queued", retry_after: "2099-01-01T00:00:00Z" }));
    await render(item({ artifacts: [summary({ validation_state: "queued" })] }));
    await vi.waitFor(() => expect(mocks.getArtifact).toHaveBeenCalled());
    expect(Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Retry validation")?.disabled).toBe(true);
    mocks.getArtifact.mockResolvedValue(detail({ validation_state: "queued", retry_after: "2000-01-01T00:00:00Z" }));
    await act(async () => { await client.invalidateQueries({ queryKey: ["application-artifact", 7, 81] }); });
    await vi.waitFor(() => expect(Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Retry validation")?.disabled).toBe(false));
    await render(item({ artifacts: [summary({ validation_state: "validated" })] }));
    expect(Array.from(node.querySelectorAll("button")).some((button) => button.textContent === "Retry validation")).toBe(false);
  });

  it.each(["queued", "validating"] as const)("polls %s detail and stops after terminal result", async (initial) => {
    mocks.getArtifact.mockResolvedValueOnce(detail({ validation_state: initial }))
      .mockResolvedValue(detail({ validation_state: "validated" }));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await render(item({ artifacts: [summary({ validation_state: initial })] }));
    await vi.waitFor(() => expect(mocks.getArtifact).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(mocks.getArtifact).toHaveBeenCalledTimes(2);
    expect(node.textContent).toContain("Content checks passed");
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(mocks.getArtifact).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  it.each(["validated", "warning", "failed", "unavailable"] as const)("does not poll terminal %s detail", async (state) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await render(item({ artifacts: [summary({ validation_state: state })] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(mocks.getArtifact).not.toHaveBeenCalled();
  });

  it("keeps a file visible and refreshes state when delete returns 409", async () => {
    mocks.deleteArtifact.mockRejectedValue(new mocks.ApiError(409));
    await render(item({ artifacts: [summary({ validation_state: "failed" })] }));
    await click("View report");
    await vi.waitFor(() => expect(mocks.getArtifact).toHaveBeenCalledTimes(1));
    await click("Delete exported file");
    await vi.waitFor(() => expect(mocks.getArtifact).toHaveBeenCalledTimes(2));
    expect(node.textContent).toContain("saved.pdf");
    expect(node.textContent).toContain("File state refreshed");
  });

  it("removes a terminal row only after successful deletion and the persisted package refresh", async () => {
    let resolveDelete!: () => void;
    mocks.deleteArtifact.mockReturnValue(new Promise<void>((resolve) => { resolveDelete = resolve; }));
    const initialPackage = item({ artifacts: [summary()] });
    const refreshedPackage = item({ artifacts: [] });
    const fetchPackage = vi.fn().mockResolvedValue(refreshedPackage);
    client.setQueryData(["application-package", 7, 12], initialPackage);
    function PersistedPanel() {
      const { data } = useQuery<ApplicationPackage>({
        queryKey: ["application-package", 7, 12], queryFn: fetchPackage, staleTime: Infinity,
      });
      return data ? <ArtifactPanel applicationId={7} package={data} current /> : null;
    }
    await act(async () => root.render(<QueryClientProvider client={client}><PersistedPanel /></QueryClientProvider>));
    expect(node.textContent).toContain("saved.pdf");
    expect(fetchPackage).not.toHaveBeenCalled();
    await click("Delete exported file");
    expect(node.textContent).toContain("saved.pdf");
    await act(async () => resolveDelete());
    expect(mocks.deleteArtifact).toHaveBeenCalledWith(7, 81);
    expect(window.confirm).toHaveBeenCalled();
    await vi.waitFor(() => expect(fetchPackage).toHaveBeenCalledTimes(1));
    expect(client.getQueryData<ApplicationPackage>(["application-package", 7, 12])?.artifacts).toEqual([]);
    await vi.waitFor(() => expect(node.textContent).not.toContain("saved.pdf"));
    expect(node.textContent).toContain("Exported files");
    expect(node.textContent).toContain("Generate new file from this frozen package");
    expect(node.textContent).toContain("No exported files saved for this package");
  });
});
