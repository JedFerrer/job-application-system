import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createContext, useContext, useRef, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { displaySections, legacyDisplaySections, TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { createReviewState, reviewedResume, sourceBackedSnapshotIsSaveable, sourceSkillOrder, summaryReview } from "@/lib/tailoring-review";
import type { TailoredResume } from "@/lib/types";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

const apiMocks = vi.hoisted(() => ({
  tailorResume: vi.fn(),
  previewCoverage: vi.fn(),
  createDraft: vi.fn(),
  validateExport: vi.fn(async (content: unknown) => content),
}));
const exportMocks = vi.hoisted(() => ({
  buildResumeHeaderItems: vi.fn((data: { canonical_sections?: Array<{ section: string; items: Array<{ source_id: string; text: string; canonical_order: number }> }> }) => {
    const seen = new Set<string>();
    return (data.canonical_sections ?? [])
      .filter((section) => section.section === "contact" || section.section === "links")
      .flatMap((section) => section.items)
      .sort((a, b) => a.canonical_order - b.canonical_order)
      .filter((item) => !seen.has(item.source_id) && seen.add(item.source_id))
      .map((item) => item.text);
  }),
  downloadResumePdf: vi.fn(),
  downloadResumeDocx: vi.fn(),
}));
const lifecycle = vi.hoisted(() => ({ dialogClose: undefined as undefined | ((event: { preventDefault: () => void }) => void), alertClose: undefined as undefined | ((event: { preventDefault: () => void }) => void) }));

vi.mock("@/lib/api", () => ({
  resumeApi: apiMocks,
}));

vi.mock("@/lib/cover-letter-export", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/cover-letter-export")>()),
  ...exportMocks,
}));

vi.mock("@/components/ui/dialog", () => {
  const Dialog = ({ children, open, onOpenChange }: { children: ReactNode; open: boolean; onOpenChange: (open: boolean) => void }) => open ? <><button onClick={() => onOpenChange(false)}>Close dialog</button>{children}</> : null;
  const DialogContent = ({ children, className, "data-testid": testId, onCloseAutoFocus, onFocusCapture }: { children: ReactNode; className?: string; "data-testid"?: string; onCloseAutoFocus?: (event: { preventDefault: () => void }) => void; onFocusCapture?: (event: React.FocusEvent) => void }) => {
    lifecycle.dialogClose = onCloseAutoFocus;
    return (
    <div role="dialog" className={className} data-testid={testId} onFocusCapture={onFocusCapture}>{children}</div>
    );
  };
  const DialogHeader = ({ children, className }: { children: ReactNode; className?: string }) => <div className={className}>{children}</div>;
  const DialogTitle = ({ children }: { children: ReactNode }) => <h2>{children}</h2>;
  const DialogDescription = ({ children }: { children: ReactNode }) => <p>{children}</p>;

  return {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
  };
});

const AlertContext = createContext<((open: boolean) => void) | null>(null);
vi.mock("@/components/ui/alert-dialog", () => ({
  AlertDialog: ({ open, children, onOpenChange }: { open: boolean; children: ReactNode; onOpenChange: (open: boolean) => void }) => open ? <AlertContext.Provider value={onOpenChange}><div>{children}</div></AlertContext.Provider> : null,
  AlertDialogContent: ({ children, onCloseAutoFocus }: { children: ReactNode; onCloseAutoFocus?: (event: { preventDefault: () => void }) => void }) => { lifecycle.alertClose = onCloseAutoFocus; return <div>{children}</div>; },
  AlertDialogHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AlertDialogTitle: ({ children }: { children: ReactNode }) => <h3>{children}</h3>,
  AlertDialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  AlertDialogFooter: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AlertDialogAction: ({ children, disabled, onClick }: { children: ReactNode; disabled?: boolean; onClick?: () => void }) => { const setOpen = useContext(AlertContext); return <button disabled={disabled} onClick={() => { onClick?.(); setOpen?.(false); queueMicrotask(() => lifecycle.alertClose?.({ preventDefault: () => undefined })); if (children === "Discard") queueMicrotask(() => lifecycle.dialogClose?.({ preventDefault: () => undefined })); }}>{children}</button>; },
  AlertDialogCancel: ({ children }: { children: ReactNode }) => { const setOpen = useContext(AlertContext); return <button onClick={() => { setOpen?.(false); lifecycle.alertClose?.({ preventDefault: () => undefined }); }}>{children}</button>; },
}));

const tailoredResume = {
  job_id: 101,
  professional_summary: "Backend Engineer with supported experience",
  full_name: "Ada Lovelace",
  email: "ada@example.com",
  phone: null,
  headline: "Backend Engineer",
  skills: ["Python", "FastAPI"],
  links: { linkedin: "https://linkedin.com/in/ada", github: null, portfolio: null, website: null },
  projects: [{ name: "Analytical Engine", description: "A computing project", technologies: ["Python"], achievements: ["Reduced errors"], url: "https://example.com/project" }],
  languages: [{ language: "Spanish", proficiency: "Fluent" }],
  licenses: [{ name: "Registered Engineer", issuer: "State Board" }],
  work_history: [
    {
      company: "Acme",
      title: "Engineer",
      start: "2020-01",
      end: "2022-01",
      original_highlights: ["Built APIs"],
      tailored_highlights: ["Built Python APIs"],
      tailored_highlight_source_indices: [0],
      role_context: ["Owned API delivery across the platform.", "Partnered with product."],
      responsibilities_heading: "Selected responsibilities",
    },
  ],
  education: [],
  certifications: [],
  matched_skills: ["python"],
  other_skills: [],
  missing_keywords: [],
};

const supportedSourceBackedDraft = (
  version: "lossless-complete-v2" | "full-context-v1" = "full-context-v1",
  sourceSchemaVersion: "source-resume-v4" | "source-resume-v5" = "source-resume-v5",
): TailoredResume => ({
  ...tailoredResume,
  full_name: null,
  email: null,
  headline: null,
  links: { linkedin: null, github: null, portfolio: null, website: null },
  projects: [],
  languages: [],
  licenses: [],
  tailoring_snapshot_version: version,
  source_resume_id: 10,
  source_resume_content_id: 20,
  source_schema_version: sourceSchemaVersion,
  source_revision_sha256: "revision",
  canonical_source_summary: "Backend Engineer with supported experience",
  canonical_sections: [{
    section: "summary",
    canonical_order: 0,
    items: [{
      source_id: "00000000-0000-0000-0000-000000000010",
      text: "Backend Engineer with supported experience",
      canonical_order: 0,
    }],
  }],
  work_history: [{
    company: "Acme",
    title: "Engineer",
    start: "2020-01",
    end: "2022-01",
    source_role_id: "00000000-0000-0000-0000-000000000099",
    source_header: "Acme",
    role_context: ["Owned API delivery across the platform.", "Partnered with product."],
    responsibilities_heading: "Selected responsibilities",
    original_highlights: ["Built APIs"],
    tailored_highlights: ["Built Python APIs"],
    tailored_highlight_source_indices: [0],
    selection_decisions: [{
      source_id: "00000000-0000-0000-0000-000000000001",
      source_index: 0,
      canonical_order: 0,
      original_text: "Built APIs",
      suggested_text: "Built Python APIs",
      final_text: "Built Python APIs",
      recommended_included: true,
      final_included: true,
      recommendation_rank: 0,
      output_rank: 0,
      evidence: [{ evidence_id: "role:0", excerpt: "Built APIs", section: "work_history", relevance_label: "Direct evidence", source_id: "00000000-0000-0000-0000-000000000001", role_index: 0 }],
    }],
  }],
});

describe("TailorResumeDialog", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
          refetchOnWindowFocus: false,
          refetchOnReconnect: false,
        },
      },
    });
    apiMocks.tailorResume.mockReset();
    apiMocks.previewCoverage.mockReset();
    apiMocks.createDraft.mockReset();
    apiMocks.validateExport.mockReset();
    apiMocks.validateExport.mockImplementation(async (content: unknown) => content);
    exportMocks.downloadResumePdf.mockReset();
    exportMocks.downloadResumeDocx.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  const renderedText = () => container.textContent ?? "";
  const renderGeneratedDraft = async (draft: TailoredResume) => {
    apiMocks.tailorResume.mockResolvedValue(draft);
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(renderedText()).toContain("Save resume"));
  };
  const saveResumeButton = () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"));

  it("handles a failed PDF download and allows a successful retry", async () => {
    exportMocks.downloadResumePdf.mockRejectedValueOnce(new Error("internal chunk URL"))
      .mockResolvedValueOnce(undefined);
    await renderGeneratedDraft(tailoredResume as TailoredResume);
    const pdf = () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "PDF")!;
    await act(async () => { pdf().click(); await Promise.resolve(); });
    expect(toastMocks.error).toHaveBeenCalledWith("Could not create the PDF. Please try again.");
    expect(toastMocks.error).not.toHaveBeenCalledWith(expect.stringContaining("internal chunk URL"));
    expect(pdf().disabled).toBe(false);
    await act(async () => { pdf().click(); await Promise.resolve(); });
    expect(exportMocks.downloadResumePdf).toHaveBeenCalledTimes(2);
  });
  const clickButton = async (label: string) => {
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent === label)?.click();
      await Promise.resolve();
    });
  };

  it("clears dirty state after Save even when the persisted echo is normalized", async () => {
    function Harness() {
      const [open, setOpen] = useState(true);
      return <TailorResumeDialog jobId={101} open={open} onOpenChange={setOpen} initialData={supportedSourceBackedDraft()} />;
    }
    apiMocks.createDraft.mockResolvedValue({ id: 9, content: { ...supportedSourceBackedDraft(), professional_summary: "Normalized server echo" } });
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><Harness /></QueryClientProvider>); });
    await vi.waitFor(() => expect(renderedText()).toContain("Keep original"));
    await clickButton("Keep original");
    await clickButton("Save resume");
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledTimes(1));
    await clickButton("Close dialog");
    expect(renderedText()).not.toContain("Save or discard unsaved edits?");
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it("tracks a new review edit as dirty after Save", async () => {
    apiMocks.createDraft.mockResolvedValue({ id: 9, content: { ...supportedSourceBackedDraft(), professional_summary: "Normalized server echo" } });
    await renderGeneratedDraft(supportedSourceBackedDraft());
    await clickButton("Keep original");
    await clickButton("Save resume");
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledTimes(1));
    await clickButton("Use suggestion");
    await clickButton("Close dialog");
    expect(renderedText()).toContain("Save or discard unsaved edits?");
  });

  it("keeps edits dirty when Save fails", async () => {
    apiMocks.createDraft.mockRejectedValue(new Error("save failed"));
    await renderGeneratedDraft(supportedSourceBackedDraft());
    await clickButton("Keep original");
    await clickButton("Save resume");
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledTimes(1));
    await clickButton("Close dialog");
    expect(renderedText()).toContain("Save or discard unsaved edits?");
  });

  it("keeps an unsaved review edit dirty", async () => {
    await renderGeneratedDraft(supportedSourceBackedDraft());
    await clickButton("Keep original");
    await clickButton("Close dialog");
    expect(renderedText()).toContain("Save or discard unsaved edits?");
  });

  it("keeps an edit made while Save is in flight dirty after it succeeds", async () => {
    let resolveSave: ((value: { id: number; content: TailoredResume }) => void) | undefined;
    apiMocks.createDraft.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
    await renderGeneratedDraft(supportedSourceBackedDraft());
    await clickButton("Keep original");
    await clickButton("Save resume");
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledTimes(1));
    await clickButton("Use suggestion");
    await act(async () => {
      resolveSave?.({ id: 9, content: { ...supportedSourceBackedDraft(), professional_summary: "Normalized server echo" } });
      await Promise.resolve();
    });
    await clickButton("Close dialog");
    expect(renderedText()).toContain("Save or discard unsaved edits?");
  });

  it("restores resume-local focus on Cancel and the opener on Discard", async () => {
    function Harness() {
      const [open, setOpen] = useState(true);
      const openerRef = useRef<HTMLButtonElement>(null);
      return <><button ref={openerRef}>Resume workspace opener</button><TailorResumeDialog jobId={101} open={open} onOpenChange={setOpen} initialData={supportedSourceBackedDraft()} returnFocusRef={openerRef} /></>;
    }
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><Harness key="success" /></QueryClientProvider>); });
    await vi.waitFor(() => expect(renderedText()).toContain("Keep original"));
    const editorControl = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Keep original")!;
    editorControl.focus();
    await act(async () => { editorControl.click(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Use suggestion"));
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click(); });
    expect(renderedText()).toContain("Save or discard unsaved edits?");
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Cancel")?.click(); await Promise.resolve(); });
    expect(document.activeElement?.textContent).toContain("Save resume");
    await vi.waitFor(() => expect(document.activeElement?.textContent).toContain("Save resume"));
    expect(document.activeElement).not.toBe(document.body);
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click(); });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Discard")?.click(); await Promise.resolve(); });
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement?.textContent).toBe("Resume workspace opener");
  });

  it("returns to the opener after a successful guarded Save", async () => {
    function Harness() {
      const [open, setOpen] = useState(true);
      const openerRef = useRef<HTMLButtonElement>(null);
      return <><button ref={openerRef}>Resume save opener</button><TailorResumeDialog jobId={101} open={open} onOpenChange={setOpen} initialData={supportedSourceBackedDraft()} returnFocusRef={openerRef} /></>;
    }
    apiMocks.createDraft.mockResolvedValueOnce({ id: 9, content: tailoredResume });
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><Harness key="success" /></QueryClientProvider>); });
    await vi.waitFor(() => expect(renderedText()).toContain("Keep original"));
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Keep original")?.click(); });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click(); });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save")?.click(); });
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledWith({
      job_id: 101,
      content: expect.objectContaining({
        source_resume_id: 10,
        source_resume_content_id: 20,
        source_revision_sha256: "revision",
        source_schema_version: "source-resume-v5",
        tailoring_snapshot_version: "full-context-v1",
        work_history: [expect.objectContaining({
          source_role_id: "00000000-0000-0000-0000-000000000099",
          tailored_highlight_source_indices: [0],
          selection_decisions: [expect.objectContaining({
            final_text: "Built APIs",
            final_included: true,
            output_rank: 0,
            evidence: [expect.objectContaining({ evidence_id: "role:0" })],
          })],
        })],
      }),
    }));
    await act(async () => { lifecycle.dialogClose?.({ preventDefault: () => undefined }); });
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement?.textContent).toBe("Resume save opener");

  });

  it("keeps legacy display items separate from canonical provenance", () => {
    const legacyItems = legacyDisplaySections(tailoredResume).flatMap((section) => section.items);
    expect(legacyItems.length).toBeGreaterThan(0);
    expect(legacyItems.every((item) => !item.source_id)).toBe(true);
    expect(legacyItems.every((item) => item.display_key?.startsWith("legacy-"))).toBe(true);

    const canonical = {
      ...tailoredResume,
      canonical_sections: [{
        section: "skills" as const,
        items: [{ source_id: "00000000-0000-0000-0000-000000000001", text: "Python", canonical_order: 0 }],
      }],
    };
    expect(displaySections(canonical)).toBe(canonical.canonical_sections);
    expect(displaySections(canonical)[0]?.items[0]?.source_id).toBe("00000000-0000-0000-0000-000000000001");
  });

  it("lets reviewers restore the source summary and skill order", () => {
    const fullContext = {
      ...tailoredResume,
      tailoring_snapshot_version: "full-context-v1" as const,
      professional_summary: "Tailored summary for the job",
      canonical_source_summary: "Original develop-ment summary",
      summary_evidence_ids: ["summary:0"],
      skills: ["FastAPI", "Python"],
      skill_order_source_ids: ["skill:1", "skill:0"],
      canonical_sections: [
        { section: "summary" as const, heading: "Summary", canonical_order: 0, items: [{ source_id: "summary-0", text: "Original develop-", canonical_order: 0 }, { source_id: "summary-1", text: "ment summary", canonical_order: 1 }] },
        { section: "skills" as const, heading: "Skills", canonical_order: 1, items: [{ source_id: "skill-0", text: "Python", canonical_order: 1 }, { source_id: "skill-1", text: "FastAPI", canonical_order: 2 }] },
      ],
    };
    expect(summaryReview(fullContext)).toEqual({ original: "Original develop-ment summary", suggestion: "Tailored summary for the job", changed: true });
    expect(sourceSkillOrder(fullContext)).toEqual({ texts: ["Python", "FastAPI"], ids: ["skill:0", "skill:1"] });

    const state = createReviewState(fullContext);
    state.summaryRejected = true;
    state.skillsSourceOrder = true;
    const reviewed = reviewedResume(state);
    expect(reviewed.professional_summary).toBe("Original develop-ment summary");
    expect(reviewed.summary_evidence_ids).toEqual([]);
    expect(reviewed.skills).toEqual(["Python", "FastAPI"]);
    expect(reviewed.skill_order_source_ids).toEqual(["skill:0", "skill:1"]);

    state.summaryRejected = false;
    state.skillsSourceOrder = false;
    const restored = reviewedResume(state);
    expect(restored.professional_summary).toBe("Tailored summary for the job");
    expect(restored.summary_evidence_ids).toEqual(["summary:0"]);
    expect(restored.skills).toEqual(["FastAPI", "Python"]);
    expect(restored.skill_order_source_ids).toEqual(["skill:1", "skill:0"]);
  });

  it("exposes reversible summary and skill controls in the review dialog", async () => {
    const fullContext = {
      ...tailoredResume,
      full_name: null,
      email: null,
      headline: null,
      links: { linkedin: null, github: null, portfolio: null, website: null },
      projects: [],
      languages: [],
      licenses: [],
      tailoring_snapshot_version: "full-context-v1" as const,
      source_resume_id: 10,
      source_resume_content_id: 20,
      source_schema_version: "source-resume-v5" as const,
      source_revision_sha256: "revision",
      professional_summary: "Tailored summary for the job",
      canonical_source_summary: "Original develop-ment summary",
      summary_evidence_ids: ["summary:0"],
      skills: ["FastAPI", "Python"],
      skill_order_source_ids: ["skill:1", "skill:0"],
      canonical_sections: [
        { section: "summary" as const, heading: "Summary", canonical_order: 0, items: [{ source_id: "summary-0", text: "Original develop-", canonical_order: 0 }, { source_id: "summary-1", text: "ment summary", canonical_order: 1 }] },
        { section: "skills" as const, heading: "Skills", canonical_order: 1, items: [{ source_id: "skill-0", text: "Python", canonical_order: 1 }, { source_id: "skill-1", text: "FastAPI", canonical_order: 2 }] },
      ],
      work_history: [{
        ...tailoredResume.work_history[0],
        source_role_id: "00000000-0000-0000-0000-000000000099",
        selection_decisions: [{ source_id: "00000000-0000-0000-0000-000000000001", source_index: 0, canonical_order: 0, original_text: "Built APIs", suggested_text: "Built Python APIs", final_text: "Built Python APIs", recommended_included: true, final_included: true, recommendation_rank: 0, output_rank: 0 }],
      }],
    };
    apiMocks.tailorResume.mockResolvedValue(fullContext);
    apiMocks.createDraft.mockResolvedValue({ content: fullContext });

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Keep original"));
    expect(renderedText()).toContain("Restore source skill order");

    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Keep original")?.click(); await Promise.resolve(); });
    expect(renderedText()).toContain("Use suggestion");
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Restore source skill order")?.click(); await Promise.resolve(); });
    expect(renderedText()).toContain("Use suggested order");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Regenerate draft"))?.click(); await Promise.resolve(); });
    expect(confirm).toHaveBeenCalledWith("Regenerate and discard your unsaved review choices?");
    expect(apiMocks.tailorResume).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.click(); await Promise.resolve(); });
    expect(apiMocks.createDraft).toHaveBeenCalledWith(expect.objectContaining({ content: expect.objectContaining({ professional_summary: "Original develop-ment summary", skills: ["Python", "FastAPI"], summary_evidence_ids: [] }) }));
  });

  it("waits for the user to start generation and labels the result as a review-required draft", async () => {
    apiMocks.tailorResume.mockImplementation(async () => tailoredResume);

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog
            jobId={101}
            jobTitle="Backend Engineer"
            open
            onOpenChange={vi.fn()}
          />
        </QueryClientProvider>,
      );
    });

    expect(apiMocks.tailorResume).not.toHaveBeenCalled();
    expect(renderedText()).toContain("Generate tailored resume");

    const generateButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Generate tailored resume"),
    );
    expect(generateButton).toBeDefined();
    await act(async () => {
      generateButton?.click();
      await Promise.resolve();
    });

    await vi.waitFor(() => expect(renderedText()).toContain("Acme"));
    queryClient.setQueryData(["resume-draft", 1], { content: { job_id: 101 } });

    expect(apiMocks.tailorResume).toHaveBeenCalledWith(101);
    expect(renderedText()).toContain("Generation is not saved until you choose Save resume.");
    expect(renderedText()).toContain("Review a tailored draft for \"Backend Engineer\"");
    expect(renderedText()).not.toContain("Your experience rewritten");
    expect(renderedText()).not.toContain("No skills or experience were invented.");
    expect(renderedText()).toContain("Acme");
    expect(renderedText()).toContain("Built Python APIs");
    expect(renderedText()).toContain("Owned API delivery across the platform. Partnered with product.");
    expect(renderedText()).toContain("Selected responsibilities");
    expect(renderedText()).toContain("Analytical Engine");
    expect(renderedText()).toContain("Registered Engineer");
    expect(renderedText()).toContain("Spanish");
    expect(renderedText()).not.toContain("https://linkedin.com/in/ada");
    expect(renderedText()).toContain("Copy all");
    expect(renderedText()).toContain("PDF");
    expect(renderedText()).toContain("DOCX");
    expect(renderedText()).toContain("Regenerate draft");
  });

  it("shows the loading state while tailoring is pending", async () => {
    apiMocks.tailorResume.mockReturnValue(new Promise(() => undefined));

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog jobId={102} open onOpenChange={vi.fn()} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
    });

    expect(apiMocks.tailorResume).not.toHaveBeenCalled();
    const generateButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Generate tailored resume"),
    );
    expect(generateButton).toBeDefined();
    await act(async () => {
      generateButton?.click();
      await Promise.resolve();
    });

    await vi.waitFor(() => expect(renderedText()).toContain("Generating a reviewable draft..."));
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("does not turn match context into candidate skills when the current snapshot is empty", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    apiMocks.tailorResume.mockResolvedValue({
      ...tailoredResume,
      skills: [],
      matched_skills: ["kubernetes"],
      other_skills: ["mongodb"],
    });

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).not.toContain("Matched job requirements"));
    expect(Array.from(container.querySelectorAll("h4")).map((heading) => heading.textContent)).not.toContain("Skills");
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Copy all"))?.click(); await Promise.resolve(); });
    expect(writeText).not.toHaveBeenCalledWith(expect.stringContaining("kubernetes"));
  });

  it("keeps unclassified source text collapsed and excludes it from copy", async () => {
    const marker = "PASSTHROUGH_MARKER: literal source text";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    apiMocks.tailorResume.mockResolvedValue({
      ...tailoredResume,
      canonical_sections: [{
        section: "unclassified" as const,
        canonical_order: 9,
        items: [{ source_id: "00000000-0000-0000-0000-000000000001", text: marker, canonical_order: 9 }],
      }],
    });

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Unclassified source text (1 items)"));
    const diagnostics = Array.from(container.querySelectorAll("details")).find((detail) => detail.textContent?.includes("Unclassified source text"));
    expect(diagnostics).toBeDefined();
    expect(diagnostics?.open).toBe(false);
    await act(async () => { (diagnostics?.querySelector("summary") as HTMLElement | null)?.click(); });
    expect(renderedText()).toContain(marker);

    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Copy all"))?.click(); await Promise.resolve(); });
    expect(writeText).toHaveBeenCalled();
    expect(writeText.mock.calls[0]?.[0]).not.toContain(marker);
  });

  it("renders named sections with their source heading and keeps long source skills out of badges", async () => {
    const longSkillSource = "Long source skill prose that should stay out of badge presentation.";
    apiMocks.tailorResume.mockResolvedValue({
      ...tailoredResume,
      canonical_source_summary: "Backend engineer with API design and automation experience.",
      canonical_sections: [
        { section: "contact" as const, canonical_order: 0, items: [
          { source_id: "name", text: "Ada Lovelace", canonical_order: 0 },
          { source_id: "address", text: "12 Analytical Engine Lane", canonical_order: 1 },
          { source_id: "email", text: "ada@example.com", canonical_order: 3 },
        ] },
        { section: "links" as const, canonical_order: 2, items: [
          { source_id: "github", text: "github.com/ada", canonical_order: 2 },
        ] },
        { section: "summary" as const, heading: "PROFESSIONAL SUMMARY", canonical_order: 4, items: [
          { source_id: "summary-1", text: "Backend engineer with API design and", canonical_order: 4 },
          { source_id: "summary-2", text: "automation experience.", canonical_order: 5 },
        ] },
        { section: "projects" as const, heading: "SELECTED PROJECTS", canonical_order: 6, items: [
          { source_id: "project-1", text: "Analytical Engine", canonical_order: 6 },
        ] },
        { section: "skills" as const, heading: "TECHNICAL STACK", canonical_order: 0, items: [{ source_id: "skills-1", text: longSkillSource, canonical_order: 0 }] },
        { section: "named" as const, heading: "Awards & Achievements", canonical_order: 1, items: [{ source_id: "named-1", text: "Engineering Excellence", canonical_order: 1 }] },
      ],
    });

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });

    await vi.waitFor(() => expect(renderedText()).toContain("Awards & Achievements"));
    expect(renderedText()).toContain("Resume header");
    expect(renderedText()).toContain("12 Analytical Engine Lane");
    expect(renderedText()).toContain("github.com/ada");
    expect(renderedText()).toContain("Backend engineer with API design and");
    expect(renderedText()).toContain("automation experience.");
    expect(container.querySelectorAll('[data-testid="canonical-summary"] p')).toHaveLength(2);
    expect(renderedText()).toContain("Original:");
    expect(renderedText()).toContain("Suggestion:");
    expect(renderedText()).toContain("PROFESSIONAL SUMMARY");
    expect(renderedText()).toContain("SELECTED PROJECTS");
    expect(renderedText()).toContain("TECHNICAL STACK");
    expect(renderedText()).toContain("Engineering Excellence");
    expect(Array.from(container.querySelectorAll("div.inline-flex")).map((badge) => badge.textContent)).not.toContain(longSkillSource);
  });

  it("separates candidate fit from resume coverage and refreshes after review", async () => {
    const analyzed = {
      ...tailoredResume,
      requirement_classifications: [
        { requirement: "python", status: "verified", priority: "unclassified", evidence_categories: ["profile_skill"], resume_coverage_status: "covered", resume_evidence_categories: ["skills"] },
        { requirement: "docker", status: "verified", priority: "unclassified", evidence_categories: ["project"], resume_coverage_status: "not_represented", resume_evidence_categories: [] },
        { requirement: "kubernetes", status: "missing", priority: "unclassified", evidence_categories: [], resume_coverage_status: "not_applicable", resume_evidence_categories: [] },
      ],
    };
    apiMocks.tailorResume.mockResolvedValue(analyzed);
    apiMocks.previewCoverage.mockResolvedValue({
      ...analyzed,
      requirement_classifications: analyzed.requirement_classifications,
    });

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Candidate Fit and Resume Coverage"));
    expect(renderedText()).toContain("Verified and shown on this resume");
    expect(renderedText()).toContain("Verified, not shown on this resume");
    expect(renderedText()).toContain("Missing requirements");
    expect(container.querySelector('[data-testid="tailor-resume-scroll-region"]')?.className).toContain("overflow-y-auto");
    expect(container.querySelector('[data-testid="tailor-resume-actions"]')?.className).toContain("shrink-0");
    expect(container.querySelector('[data-testid="tailor-resume-actions"]')?.className).not.toContain("px-");
    expect(container.querySelector(".text-amber-800.dark\\:text-amber-200")).not.toBeNull();

    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Keep original"))?.click(); await Promise.resolve(); });
    expect(apiMocks.previewCoverage).toHaveBeenCalledWith(expect.objectContaining({ job_id: 101 }));
  });

  it("keeps the legacy matched/other fallback when skills is absent", async () => {
    const { skills: _skills, ...legacyResume } = tailoredResume;
    apiMocks.tailorResume.mockResolvedValue(legacyResume);

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Additional resume sections"));
    expect(Array.from(container.querySelectorAll("h4")).map((heading) => heading.textContent)).toContain("projects");
  });

  it("keeps the legacy matched/other fallback when skills is null", async () => {
    apiMocks.tailorResume.mockResolvedValue({ ...tailoredResume, skills: null });

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Additional resume sections"));
    expect(Array.from(container.querySelectorAll("h4")).map((heading) => heading.textContent)).toContain("projects");
  });

  it("saves the displayed tailored snapshot with its job id", async () => {
    const sourceBacked = supportedSourceBackedDraft();
    apiMocks.tailorResume.mockResolvedValue(sourceBacked);
    apiMocks.createDraft.mockResolvedValue({
      id: 1,
      job_id: 101,
      source_profile_id: 8,
      job_title: "Backend Engineer",
      job_company: "Acme",
      content: sourceBacked,
      created_at: "2026-07-14T00:00:00Z",
      updated_at: "2026-07-14T00:00:00Z",
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog
            jobId={101}
            jobTitle="Backend Engineer"
            open
            onOpenChange={vi.fn()}
          />
        </QueryClientProvider>,
      );
    });
    expect(apiMocks.tailorResume).not.toHaveBeenCalled();
    const generateButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Generate tailored resume"),
    );
    expect(generateButton).toBeDefined();
    await act(async () => {
      generateButton?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(renderedText()).toContain("Acme"));
    queryClient.setQueryData(["resume-draft", 1], { content: { job_id: 101 } });

    const saveButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Save resume"),
    );
    expect(saveButton).toBeDefined();
    await act(async () => {
      saveButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(apiMocks.createDraft).toHaveBeenCalledWith({
      job_id: 101,
      content: sourceBacked,
    });
    expect(queryClient.getQueryData(["resume-draft", 1])).toBeUndefined();
  });

  it("shows the existing failure state when tailoring fails", async () => {
    apiMocks.tailorResume.mockImplementation(async () => {
      throw new Error("failed");
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog jobId={103} open onOpenChange={vi.fn()} />
        </QueryClientProvider>,
      );
    });

    expect(apiMocks.tailorResume).not.toHaveBeenCalled();
    const generateButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Generate tailored resume"),
    );
    expect(generateButton).toBeDefined();
    await act(async () => {
      generateButton?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() =>
      expect(renderedText()).toContain("Could not generate tailored resume."),
    );

    expect(apiMocks.tailorResume).toHaveBeenCalledWith(103);
  });

  it("keeps the reviewed draft visible when regeneration fails", async () => {
    apiMocks.tailorResume.mockResolvedValueOnce(tailoredResume).mockRejectedValueOnce(new Error("failed"));

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(renderedText()).toContain("Built Python APIs"));

    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Regenerate draft"))?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(renderedText()).toContain("Could not regenerate this draft."));
    expect(renderedText()).toContain("Built Python APIs");
  });

  it("uses the reviewed original for save and every export path", async () => {
    const sourceBacked = supportedSourceBackedDraft();
    apiMocks.tailorResume.mockResolvedValue(sourceBacked);
    apiMocks.createDraft.mockResolvedValue({ content: sourceBacked });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(renderedText()).toContain("Keep original"));
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Keep original"))?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.click());
    expect(apiMocks.createDraft.mock.calls[0]?.[0].content.work_history[0].tailored_highlights).toEqual(["Built APIs"]);
    const copyButton = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Copy all"));
    await act(async () => { copyButton?.click(); await Promise.resolve(); });
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Built APIs"));
    expect(writeText.mock.calls[0]?.[0]).not.toContain("Built Python APIs");
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "PDF")?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "DOCX")?.click());
    for (const exportMock of [exportMocks.downloadResumePdf, exportMocks.downloadResumeDocx]) {
      expect(exportMock).toHaveBeenCalledWith(
        expect.objectContaining({
          skills: ["Python", "FastAPI"],
          projects: [],
          links: expect.objectContaining({ linkedin: null }),
          languages: [],
          licenses: [],
          work_history: [expect.objectContaining({ tailored_highlights: ["Built APIs"] })],
        }),
        "Tailored_Resume",
      );
    }
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Use suggestion"))?.click());
    expect(container.textContent).not.toContain("Use all suggestions");
  });

  it("shows source bullets when the mapping is unsafe", async () => {
    apiMocks.tailorResume.mockResolvedValue({
      ...tailoredResume,
      tailoring_snapshot_version: "full-context-v1" as const,
      source_resume_id: 10,
      source_resume_content_id: 20,
      source_schema_version: "source-resume-v5" as const,
      source_revision_sha256: "revision",
      work_history: [{ ...tailoredResume.work_history[0], tailored_highlight_source_indices: [1] }],
    });
    apiMocks.createDraft.mockResolvedValue({ content: tailoredResume });
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Comparison unavailable; showing source bullets safely."));
    expect(renderedText()).toContain("Built APIs");
    expect(renderedText()).not.toContain("Built Python APIs");
    expect(container.querySelector('button[aria-label*="Keep original"]')).toBeNull();
    expect(renderedText()).toContain("This draft no longer has a valid source mapping. Regenerate the draft before saving.");
    expect(Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.disabled).toBe(true);
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click());
    expect(Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save")?.disabled).toBe(true);
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Discard")?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it.each([
    ["null snapshot version", { ...tailoredResume, tailoring_snapshot_version: null }],
    ["omitted snapshot version", { ...tailoredResume }],
    ["legacy full-resume-v1 snapshot", { ...tailoredResume, tailoring_snapshot_version: "full-resume-v1" as const }],
    ["unknown snapshot version", { ...tailoredResume, tailoring_snapshot_version: "future-version-v99" as unknown as TailoredResume["tailoring_snapshot_version"] }],
  ])("blocks Save in the rendered dialog for a %s", async (_label, draft) => {
    await renderGeneratedDraft(draft);
    expect(renderedText()).toContain("Regenerate the draft before saving.");
    expect(saveResumeButton()?.disabled).toBe(true);
    await act(async () => saveResumeButton()?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it("blocks source-resume-v3 in the rendered dialog and its close confirmation", async () => {
    await renderGeneratedDraft({ ...supportedSourceBackedDraft(), source_schema_version: "source-resume-v3" });
    expect(renderedText()).toContain("Regenerate the draft before saving.");
    expect(saveResumeButton()?.disabled).toBe(true);
    await act(async () => saveResumeButton()?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click());
    expect(Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save")?.disabled).toBe(true);
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it("fails closed when a zero-evidence draft omits canonical_source_summary", async () => {
    const explicitNullSummary = {
      ...supportedSourceBackedDraft(),
      professional_summary: null,
      canonical_source_summary: null,
      summary_evidence_ids: [],
      summary_evidence: [],
    };
    expect(sourceBackedSnapshotIsSaveable(explicitNullSummary)).toBe(true);

    const { canonical_source_summary: _canonicalSourceSummary, ...missingCanonicalSummary } = explicitNullSummary;
    expect(Object.hasOwn(missingCanonicalSummary, "canonical_source_summary")).toBe(false);

    await renderGeneratedDraft(missingCanonicalSummary);
    expect(renderedText()).toContain("Regenerate the draft before saving.");
    expect(saveResumeButton()?.disabled).toBe(true);
    await act(async () => saveResumeButton()?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();

    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click());
    expect(Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save")?.disabled).toBe(true);
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it("saves an explicitly-null zero-evidence canonical summary through the rendered dialog", async () => {
    const explicitNullSummary = {
      ...supportedSourceBackedDraft(),
      professional_summary: null,
      canonical_source_summary: null,
      summary_evidence_ids: [],
      summary_evidence: [],
    };
    expect(Object.hasOwn(explicitNullSummary, "canonical_source_summary")).toBe(true);
    expect(explicitNullSummary.canonical_source_summary).toBeNull();
    expect(sourceBackedSnapshotIsSaveable(explicitNullSummary)).toBe(true);

    const reviewed = reviewedResume(createReviewState(explicitNullSummary));
    expect(Object.hasOwn(reviewed, "canonical_source_summary")).toBe(true);
    expect(reviewed.canonical_source_summary).toBeNull();
    expect(reviewed.professional_summary).toBeNull();
    expect(reviewed.summary_evidence_ids).toEqual([]);

    apiMocks.createDraft.mockResolvedValue({ id: 11, content: reviewed });
    await renderGeneratedDraft(explicitNullSummary);
    expect(renderedText()).not.toContain("Regenerate the draft before saving.");
    expect(saveResumeButton()?.disabled).toBe(false);
    await act(async () => saveResumeButton()?.click());
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledWith({
      job_id: 101,
      content: expect.objectContaining({
        canonical_source_summary: null,
        professional_summary: null,
        summary_evidence_ids: [],
        summary_evidence: [],
        tailoring_snapshot_version: "full-context-v1",
        source_schema_version: "source-resume-v5",
        source_resume_id: 10,
        source_resume_content_id: 20,
        source_revision_sha256: "revision",
        work_history: [expect.objectContaining({
          source_role_id: "00000000-0000-0000-0000-000000000099",
          tailored_highlight_source_indices: [0],
          selection_decisions: [expect.objectContaining({
            source_id: "00000000-0000-0000-0000-000000000001",
            final_included: true,
            final_text: "Built Python APIs",
            output_rank: 0,
            evidence: [expect.objectContaining({ evidence_id: "role:0" })],
          })],
        })],
      }),
    }));
  });

  it.each([
    ["unknown", "source-resume-v99" as unknown as TailoredResume["source_schema_version"]],
    ["empty", "" as unknown as TailoredResume["source_schema_version"]],
    ["null", null],
    ["missing", undefined],
  ])("blocks a %s source schema in the rendered dialog", async (_label, sourceSchemaVersion) => {
    const draft = { ...supportedSourceBackedDraft(), source_schema_version: sourceSchemaVersion };
    if (sourceSchemaVersion === undefined) delete draft.source_schema_version;
    await renderGeneratedDraft(draft);
    expect(renderedText()).toContain("Regenerate the draft before saving.");
    expect(saveResumeButton()?.disabled).toBe(true);
    await act(async () => saveResumeButton()?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it("blocks an empty tailoring snapshot version in the rendered dialog", async () => {
    await renderGeneratedDraft({ ...supportedSourceBackedDraft(), tailoring_snapshot_version: "" as unknown as TailoredResume["tailoring_snapshot_version"] });
    expect(renderedText()).toContain("Regenerate the draft before saving.");
    expect(saveResumeButton()?.disabled).toBe(true);
    await act(async () => saveResumeButton()?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it.each([
    ["lossless-complete-v2", "source-resume-v4"],
    ["full-context-v1", "source-resume-v5"],
  ] as const)("saves a valid %s draft with %s provenance", async (snapshotVersion, sourceSchemaVersion) => {
    const draft = supportedSourceBackedDraft(snapshotVersion, sourceSchemaVersion);
    apiMocks.createDraft.mockResolvedValue({ id: 10, content: draft });
    await renderGeneratedDraft(draft);
    expect(saveResumeButton()?.disabled).toBe(false);
    await act(async () => saveResumeButton()?.click());
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledWith({
      job_id: 101,
      content: expect.objectContaining({
        tailoring_snapshot_version: snapshotVersion,
        source_resume_id: 10,
        source_resume_content_id: 20,
        source_revision_sha256: "revision",
        source_schema_version: sourceSchemaVersion,
        work_history: [expect.objectContaining({
          source_role_id: "00000000-0000-0000-0000-000000000099",
          tailored_highlight_source_indices: [0],
          tailored_highlights: ["Built Python APIs"],
          selection_decisions: [expect.objectContaining({
            source_id: "00000000-0000-0000-0000-000000000001",
            source_index: 0,
            final_included: true,
            final_text: "Built Python APIs",
            output_rank: 0,
            evidence: [expect.objectContaining({ evidence_id: "role:0" })],
          })],
        })],
      }),
    }));
  });

  it("disables close-confirmation Save for an unsupported snapshot", async () => {
    await renderGeneratedDraft({ ...tailoredResume });
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click());
    expect(Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save")?.disabled).toBe(true);
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Discard")?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it("keeps a valid source-backed reviewed draft saveable with its provenance", async () => {
    const sourceBacked = supportedSourceBackedDraft();
    const reviewed = reviewedResume(createReviewState(sourceBacked));
    expect(sourceBackedSnapshotIsSaveable(reviewed)).toBe(true);
    expect(reviewed.work_history[0]?.tailored_highlight_source_indices).toEqual([0]);
    expect(reviewed.work_history[0]?.selection_decisions).toEqual(sourceBacked.work_history[0]?.selection_decisions);
    apiMocks.tailorResume.mockResolvedValue(sourceBacked);
    apiMocks.createDraft.mockResolvedValue({ id: 9, content: sourceBacked });
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(renderedText()).toContain("Save resume"));
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.click();
    });
    await vi.waitFor(() => expect(apiMocks.createDraft).toHaveBeenCalledWith({ job_id: 101, content: expect.objectContaining({ source_resume_id: 10, source_resume_content_id: 20, source_revision_sha256: "revision", work_history: [expect.objectContaining({ tailored_highlight_source_indices: [0], selection_decisions: sourceBacked.work_history[0]?.selection_decisions })] }) }));
  });

  it("blocks the live summary-mismatch shape before it reaches Save", async () => {
    const invalid = {
      ...supportedSourceBackedDraft(),
      // This mirrors the live v5 shape: display sections contain wrapped text,
      // but the backend's canonical summary is null because no primary summary
      // source item exists.
      canonical_source_summary: null,
      professional_summary: "Synthetic wrapped display line.",
      summary_evidence_ids: [],
      summary_evidence: [],
      canonical_sections: [{
        section: "summary" as const,
        canonical_order: 0,
        items: [{
          source_id: "00000000-0000-0000-0000-000000000010",
          text: "Synthetic wrapped display line.",
          canonical_order: 0,
        }],
      }],
    };

    expect(sourceBackedSnapshotIsSaveable(invalid)).toBe(false);
    await renderGeneratedDraft(invalid);
    expect(saveResumeButton()?.disabled).toBe(true);
    await act(async () => saveResumeButton()?.click());
    expect(apiMocks.createDraft).not.toHaveBeenCalled();
  });

  it("keeps an evidence-backed summary saveable when the API contract permits it", () => {
    const evidenceBacked = {
      ...supportedSourceBackedDraft(),
      professional_summary: "Synthetic supported tailored summary.",
      summary_evidence_ids: ["summary:0"],
      summary_evidence: [{
        evidence_id: "summary:0",
        excerpt: "Synthetic source summary.",
        section: "summary",
        relevance_label: "Direct evidence",
      }],
    };

    expect(sourceBackedSnapshotIsSaveable(evidenceBacked)).toBe(true);
  });

  it("does not submit legacy full-resume snapshots that the source validator cannot persist", () => {
    expect(sourceBackedSnapshotIsSaveable({ ...tailoredResume, tailoring_snapshot_version: "full-resume-v1" })).toBe(false);
  });

  it("shows moved-higher for reorder-only bullets without review controls", async () => {
    apiMocks.tailorResume.mockResolvedValue({
      ...tailoredResume,
      work_history: [{
        ...tailoredResume.work_history[0],
        original_highlights: ["First bullet", "Second bullet", "Third bullet"],
        tailored_highlights: ["Third bullet", "First bullet", "Second bullet"],
        tailored_highlight_source_indices: [2, 0, 1],
      }],
    });
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click(); await Promise.resolve(); });
    await vi.waitFor(() => expect(renderedText()).toContain("Moved higher"));
    expect(renderedText()).not.toContain("Original:");
    expect(renderedText()).not.toContain("Suggested:");
    expect(container.querySelector('button[aria-label*="Keep original"]')).toBeNull();
    expect(container.querySelector('button[aria-label*="Use suggestion"]')).toBeNull();
    expect(renderedText()).not.toContain("Use all suggestions");
  });

  it("keeps every new source bullet included and separates recommendation from wording", async () => {
    const fullResume = {
      ...tailoredResume,
      tailoring_snapshot_version: "full-resume-v1" as const,
      work_history: [{
        ...tailoredResume.work_history[0],
        original_highlights: ["Recommended source", "Unrelated source"],
        tailored_highlights: ["Recommended suggestion", "Unrelated source"],
        tailored_highlight_source_indices: [0, 1],
        selection_decisions: [
          {
            source_id: "00000000-0000-0000-0000-000000000001",
            source_index: 0,
            canonical_order: 0,
            original_text: "Recommended source",
            suggested_text: "Recommended suggestion",
            final_text: "Recommended suggestion",
            recommended_included: true,
            final_included: true,
            recommendation_rank: 0,
            output_rank: 0,
          },
          {
            source_id: "00000000-0000-0000-0000-000000000002",
            source_index: 1,
            canonical_order: 1,
            original_text: "Unrelated source",
            suggested_text: "Unrelated source",
            final_text: "Unrelated source",
            recommended_included: false,
            final_included: true,
            recommendation_rank: null,
            output_rank: 1,
          },
        ],
      }],
    };
    apiMocks.tailorResume.mockResolvedValue(fullResume);
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(renderedText()).toContain("Recommended for this job"));
    expect(renderedText()).toContain("Unrelated source");
    expect(renderedText()).toContain("Restore AI wording");
    expect(renderedText()).toContain("Include all experience");
    expect(renderedText()).not.toContain("Restore recommended selection");

    const unrelated = container.querySelector('[data-testid="review-item-0:1"]');
    await act(async () => unrelated?.querySelector('button[aria-label="Omit bullet"]')?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(renderedText()).toContain("Not shown");
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Include all experience"))?.click());
    expect(renderedText()).not.toContain("Not shown");
  });

  it("renders source-backed role narrative once and keeps continuation fragments out of preview", async () => {
    const narrative = "Owned event delivery for payment processing across the platform.";
    const heading = "Responsibilities";
    const bullet = "Designed an event pipeline that processed payments across three regions without interruption.";
    const fragment = "payments across three regions without";
    apiMocks.tailorResume.mockResolvedValue({
      ...tailoredResume,
      tailoring_snapshot_version: "full-context-v1" as const,
      work_history: [{
        ...tailoredResume.work_history[0],
        original_highlights: [bullet, "Coordinated the migration window and reduced failed retries."],
        tailored_highlights: [bullet, "Coordinated the migration window and reduced failed retries."],
        tailored_highlight_source_indices: [0, 1],
        role_context: [narrative],
        responsibilities_heading: heading,
        selection_decisions: [
          {
            source_id: "00000000-0000-0000-0000-000000000001",
            source_index: 0,
            canonical_order: 0,
            original_text: bullet,
            suggested_text: bullet,
            final_text: bullet,
            recommended_included: true,
            final_included: true,
            output_rank: 0,
          },
          {
            source_id: "00000000-0000-0000-0000-000000000002",
            source_index: 1,
            canonical_order: 1,
            original_text: "Coordinated the migration window and reduced failed retries.",
            suggested_text: "Coordinated the migration window and reduced failed retries.",
            final_text: "Coordinated the migration window and reduced failed retries.",
            recommended_included: true,
            final_included: true,
            output_rank: 1,
          },
        ],
      }],
      canonical_sections: [{
        section: "unclassified" as const,
        canonical_order: 9,
        items: [{ source_id: "00000000-0000-0000-0000-000000000099", text: fragment, canonical_order: 9 }],
      }],
    });

    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} /></QueryClientProvider>);
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Generate tailored resume"))?.click();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(container.querySelector('[data-testid="role-context-0"]')?.textContent).toBe(narrative));
    expect(container.querySelectorAll('[data-testid="role-context-0"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="responsibilities-heading-0"]')?.textContent).toBe(heading);
    expect(container.querySelectorAll('[data-testid="responsibilities-heading-0"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="role-context-0"]')?.textContent).not.toContain(fragment);
    expect(renderedText()).toContain(bullet);
    const diagnostics = Array.from(container.querySelectorAll("details")).find((detail) => detail.textContent?.includes("Unclassified source text"));
    expect(diagnostics).toBeDefined();
    expect(diagnostics?.open).toBe(false);
  });

  it("blocks copy and export when a saved draft requires regeneration", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const blocked = {
      ...tailoredResume,
      tailoring_snapshot_version: "full-context-v1" as const,
      requires_regeneration: true,
    };

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} initialData={blocked} readOnly />
        </QueryClientProvider>,
      );
    });

    await vi.waitFor(() => expect(renderedText()).toContain("This resume must be re-generated after the source resume changes."));
    const copyButton = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Copy all"));
    const pdfButton = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "PDF");
    const docxButton = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "DOCX");
    expect((copyButton as HTMLButtonElement | undefined)?.disabled).toBe(true);
    expect((pdfButton as HTMLButtonElement | undefined)?.disabled).toBe(true);
    expect((docxButton as HTMLButtonElement | undefined)?.disabled).toBe(true);
    await act(async () => { copyButton?.click(); await Promise.resolve(); });
    await act(async () => { pdfButton?.click(); await Promise.resolve(); });
    await act(async () => { docxButton?.click(); await Promise.resolve(); });
    expect(writeText).not.toHaveBeenCalled();
    expect(exportMocks.downloadResumePdf).not.toHaveBeenCalled();
    expect(exportMocks.downloadResumeDocx).not.toHaveBeenCalled();
    expect(apiMocks.validateExport).not.toHaveBeenCalled();
  });

  it("shows Original kept when a read-only saved draft restored the source bullet", async () => {
    const saved = {
      ...supportedSourceBackedDraft(),
      work_history: [{
        ...tailoredResume.work_history[0]!,
        tailored_highlights: ["Built APIs"],
        selection_decisions: [{
          source_id: "00000000-0000-0000-0000-000000000001",
          source_index: 0,
          canonical_order: 0,
          original_text: "Built APIs",
          suggested_text: "Built Python APIs",
          final_text: "Built APIs",
          recommended_included: true,
          final_included: true,
          recommendation_rank: 0,
          output_rank: 0,
        }],
      }],
    };

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} initialData={saved} readOnly />
        </QueryClientProvider>,
      );
    });

    await vi.waitFor(() => expect(renderedText()).toContain("Original kept"));
    expect(renderedText()).toContain("Built APIs");
    expect(renderedText()).not.toMatch(/Use suggestion/);
  });

  it("preserves persisted output order on a read-only saved draft", async () => {
    const first = "Persisted first bullet";
    const second = "Persisted recommended bullet";
    const saved = {
      ...tailoredResume,
      work_history: [{
        ...tailoredResume.work_history[0]!,
        original_highlights: [first, second],
        tailored_highlights: [first, second],
        tailored_highlight_source_indices: [0, 1],
        selection_decisions: [
          {
            source_id: "00000000-0000-0000-0000-000000000001",
            source_index: 0,
            canonical_order: 0,
            original_text: first,
            suggested_text: first,
            final_text: first,
            recommended_included: false,
            final_included: true,
            recommendation_rank: null,
            output_rank: 0,
          },
          {
            source_id: "00000000-0000-0000-0000-000000000002",
            source_index: 1,
            canonical_order: 1,
            original_text: second,
            suggested_text: second,
            final_text: second,
            recommended_included: true,
            final_included: true,
            recommendation_rank: 0,
            output_rank: 1,
          },
        ],
      }],
    };
    expect(reviewedResume(createReviewState(saved)).work_history[0]!.tailored_highlights).toEqual([second, first]);

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} initialData={saved} readOnly />
        </QueryClientProvider>,
      );
    });

    await vi.waitFor(() => expect(renderedText()).toContain(first));
    expect(renderedText().indexOf(first)).toBeLessThan(renderedText().indexOf(second));
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Copy all"))?.click();
      await Promise.resolve();
    });
    expect(writeText).toHaveBeenCalled();
    const copied = String(writeText.mock.calls[0]?.[0] ?? "");
    expect(copied.indexOf(first)).toBeLessThan(copied.indexOf(second));
  });

  it("preserves saved original wording on editable reopen until the reviewer changes it", async () => {
    const saved = {
      ...supportedSourceBackedDraft(),
      work_history: [{
        ...tailoredResume.work_history[0]!,
        original_highlights: ["Built APIs", "Shipped dashboards"],
        tailored_highlights: ["Built APIs", "Shipped dashboards"],
        tailored_highlight_source_indices: [0, 1],
        selection_decisions: [
          {
            source_id: "00000000-0000-0000-0000-000000000001",
            source_index: 0,
            canonical_order: 0,
            original_text: "Built APIs",
            suggested_text: "Built Python APIs",
            final_text: "Built APIs",
            recommended_included: true,
            final_included: true,
            recommendation_rank: 0,
            output_rank: 0,
          },
          {
            source_id: "00000000-0000-0000-0000-000000000002",
            source_index: 1,
            canonical_order: 1,
            original_text: "Shipped dashboards",
            suggested_text: "Shipped dashboards",
            final_text: "Shipped dashboards",
            recommended_included: true,
            final_included: true,
            recommendation_rank: 1,
            output_rank: 1,
          },
        ],
      }],
    };
    apiMocks.createDraft.mockResolvedValue({ content: saved });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <TailorResumeDialog jobId={101} open onOpenChange={vi.fn()} initialData={saved} />
        </QueryClientProvider>,
      );
    });

    await vi.waitFor(() => expect(renderedText()).toContain("Original kept"));
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.click();
      await Promise.resolve();
    });
    expect(apiMocks.createDraft.mock.calls[0]?.[0].content.work_history[0].tailored_highlights).toEqual([
      "Built APIs",
      "Shipped dashboards",
    ]);

    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Use suggestion"))?.click();
      await Promise.resolve();
    });
    expect(renderedText()).not.toContain("Original kept");
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.click();
      await Promise.resolve();
    });
    expect(apiMocks.createDraft.mock.calls[1]?.[0].content.work_history[0].tailored_highlights).toEqual([
      "Built Python APIs",
      "Shipped dashboards",
    ]);
  });
});
