import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { createReviewState, reviewedResume } from "@/lib/tailoring-review";
import { QA_WRAP_NARRATIVE, qaLegacyResume, qaWrapResume } from "./fixtures/tailored-resume-qa";

const apiMocks = vi.hoisted(() => ({
  tailorResume: vi.fn(),
  previewCoverage: vi.fn(),
  createDraft: vi.fn(),
  validateExport: vi.fn(),
}));
const exportMocks = vi.hoisted(() => ({
  downloadResumePdf: vi.fn(),
  downloadResumeDocx: vi.fn(),
}));
const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ resumeApi: apiMocks }));
vi.mock("@/lib/cover-letter-export", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/cover-letter-export")>()),
  ...exportMocks,
}));
vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("@/components/ui/dialog", () => {
  const Dialog = ({ children }: { children: ReactNode }) => <>{children}</>;
  const Box = ({ children, className, "data-testid": testId }: { children: ReactNode; className?: string; "data-testid"?: string }) => (
    <div role="dialog" className={className} data-testid={testId}>{children}</div>
  );
  return {
    Dialog,
    DialogContent: Box,
    DialogHeader: Box,
    DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
    DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  };
});

describe("Phase 4 QA export gates", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    apiMocks.tailorResume.mockReset();
    apiMocks.previewCoverage.mockReset();
    apiMocks.createDraft.mockReset();
    apiMocks.validateExport.mockReset();
    exportMocks.downloadResumePdf.mockReset();
    exportMocks.downloadResumeDocx.mockReset();
    toastMocks.success.mockReset();
    toastMocks.error.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  async function renderDialog(resume: ReturnType<typeof qaWrapResume>, readOnly = true) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog
            jobId={resume.job_id}
            open
            onOpenChange={vi.fn()}
            initialData={resume}
            readOnly={readOnly}
          />
        </QueryClientProvider>,
      );
    });
  }

  async function clickLabel(label: string) {
    const button = Array.from(container.querySelectorAll("button")).find((item) => item.textContent?.includes(label));
    await act(async () => { button?.click(); await Promise.resolve(); });
    return button;
  }

  it("rehydrates Keep Original on a read-only saved draft", async () => {
    const source = qaWrapResume();
    const state = createReviewState(source);
    state.rejected.add("0:0");
    const saved = reviewedResume(state);
    await renderDialog(saved);
    await vi.waitFor(() => expect(container.textContent).toContain("Original kept"));
    expect(container.textContent).toContain(source.work_history[0]!.selection_decisions![0]!.original_text);
    expect(container.textContent).not.toMatch(/Use suggestion/);
  });

  it("preserves persisted output_rank on a read-only saved draft", async () => {
    const source = qaWrapResume();
    const decisions = source.work_history[0]!.selection_decisions!;
    const kept = {
      ...decisions[0]!,
      final_text: decisions[0]!.original_text,
      output_rank: 1,
    };
    const omitted = { ...decisions[1]!, final_included: false, output_rank: null };
    const first = { ...decisions[2]!, output_rank: 0 };
    const saved = {
      ...source,
      work_history: [{
        ...source.work_history[0]!,
        tailored_highlights: [first.final_text, kept.final_text],
        tailored_highlight_source_indices: [2, 0],
        selection_decisions: [kept, omitted, first],
      }, source.work_history[1]!],
    };
    expect(reviewedResume(createReviewState(saved)).work_history[0]!.tailored_highlights).toEqual([
      kept.final_text,
      first.final_text,
    ]);

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    apiMocks.validateExport.mockImplementation(async (content: unknown) => content);
    await renderDialog(saved);
    await vi.waitFor(() => expect(container.textContent).toContain("Original kept"));
    expect(container.textContent!.indexOf(first.final_text)).toBeLessThan(container.textContent!.indexOf(kept.final_text));

    await clickLabel("Copy all");
    expect(writeText).toHaveBeenCalled();
    const copied = String(writeText.mock.calls[0]?.[0] ?? "");
    expect(copied.indexOf(first.final_text)).toBeLessThan(copied.indexOf(kept.final_text));
    expect(exportMocks.downloadResumePdf).not.toHaveBeenCalled();
  });

  it("keeps an unsafe source-backed draft readable and blocks copy/PDF/DOCX when export validation fails", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    apiMocks.validateExport.mockRejectedValue(new Error("invalid snapshot"));
    await renderDialog(qaWrapResume());
    await vi.waitFor(() => expect(container.textContent).toContain(QA_WRAP_NARRATIVE));
    expect(container.textContent).toContain("Acme Platform - Senior Engineer");
    expect(container.textContent).toContain("Beacon Systems - Software Engineer");

    await clickLabel("Copy all");
    await clickLabel("PDF");
    await clickLabel("DOCX");

    expect(apiMocks.validateExport).toHaveBeenCalled();
    expect(writeText).not.toHaveBeenCalled();
    expect(exportMocks.downloadResumePdf).not.toHaveBeenCalled();
    expect(exportMocks.downloadResumeDocx).not.toHaveBeenCalled();
    expect(toastMocks.error).toHaveBeenCalledWith(
      "This resume must be re-generated after the source resume changes.",
    );
  });

  it("blocks full-resume-v1 canonical drafts client-side without calling backend validation", async () => {
    apiMocks.validateExport.mockResolvedValue(qaWrapResume());
    await renderDialog(qaWrapResume({ tailoring_snapshot_version: "full-resume-v1" }));
    await clickLabel("PDF");
    expect(apiMocks.validateExport).not.toHaveBeenCalled();
    expect(exportMocks.downloadResumePdf).not.toHaveBeenCalled();
    expect(toastMocks.error).toHaveBeenCalledWith(
      "This saved resume must be re-generated before export.",
    );
  });

  it("blocks a requires_regeneration draft without calling backend export validation", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await renderDialog(qaWrapResume({ requires_regeneration: true }));
    await vi.waitFor(() =>
      expect(container.textContent).toContain("This resume must be re-generated after the source resume changes."),
    );
    expect(container.textContent).toContain(QA_WRAP_NARRATIVE);
    await clickLabel("Copy all");
    await clickLabel("PDF");
    await clickLabel("DOCX");
    expect(apiMocks.validateExport).not.toHaveBeenCalled();
    expect(writeText).not.toHaveBeenCalled();
    expect(exportMocks.downloadResumePdf).not.toHaveBeenCalled();
    expect(exportMocks.downloadResumeDocx).not.toHaveBeenCalled();
  });

  it("exports a legacy non-full-context draft without backend source validation", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const legacy = qaLegacyResume();
    await renderDialog(legacy);
    await vi.waitFor(() => expect(container.textContent).toContain(QA_WRAP_NARRATIVE));
    await clickLabel("Copy all");
    await clickLabel("PDF");
    await clickLabel("DOCX");
    expect(apiMocks.validateExport).not.toHaveBeenCalled();
    expect(writeText).toHaveBeenCalled();
    expect(exportMocks.downloadResumePdf).toHaveBeenCalledWith(legacy, "Jordan_Example_Resume");
    expect(exportMocks.downloadResumeDocx).toHaveBeenCalledWith(legacy, "Jordan_Example_Resume");
  });
});
