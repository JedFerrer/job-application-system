import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { displaySections, legacyDisplaySections, TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";

const apiMocks = vi.hoisted(() => ({
  tailorResume: vi.fn(),
  previewCoverage: vi.fn(),
  createDraft: vi.fn(),
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

vi.mock("@/lib/api", () => ({
  resumeApi: apiMocks,
}));

vi.mock("@/lib/cover-letter-export", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/cover-letter-export")>()),
  ...exportMocks,
}));

vi.mock("@/components/ui/dialog", () => {
  const Dialog = ({ children }: { children: ReactNode }) => <>{children}</>;
  const DialogContent = ({ children, className, "data-testid": testId }: { children: ReactNode; className?: string; "data-testid"?: string }) => (
    <div role="dialog" className={className} data-testid={testId}>{children}</div>
  );
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
    },
  ],
  education: [],
  certifications: [],
  matched_skills: ["python"],
  other_skills: [],
  missing_keywords: [],
};

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
    expect(container.querySelectorAll('[data-testid="canonical-summary"] p')).toHaveLength(1);
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
    apiMocks.tailorResume.mockResolvedValue(tailoredResume);
    apiMocks.createDraft.mockResolvedValue({
      id: 1,
      job_id: 101,
      source_profile_id: 8,
      job_title: "Backend Engineer",
      job_company: "Acme",
      content: tailoredResume,
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
      content: tailoredResume,
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
    apiMocks.tailorResume.mockResolvedValue(tailoredResume);
    apiMocks.createDraft.mockResolvedValue({ content: tailoredResume });
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
          projects: [expect.objectContaining({ name: "Analytical Engine" })],
          links: expect.objectContaining({ linkedin: "https://linkedin.com/in/ada" }),
          languages: [expect.objectContaining({ language: "Spanish" })],
          licenses: [expect.objectContaining({ name: "Registered Engineer" })],
          work_history: [expect.objectContaining({ tailored_highlights: ["Built APIs"] })],
        }),
        "Ada_Lovelace_Resume",
      );
    }
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Use suggestion"))?.click());
    expect(container.textContent).not.toContain("Use all suggestions");
  });

  it("shows source bullets when the mapping is unsafe", async () => {
    apiMocks.tailorResume.mockResolvedValue({
      ...tailoredResume,
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
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save resume"))?.click());
    expect(apiMocks.createDraft.mock.calls[0]?.[0].content.work_history[0].tailored_highlights).toEqual(["Built APIs"]);
    expect(apiMocks.createDraft.mock.calls[0]?.[0].content.work_history[0]).not.toHaveProperty("tailored_highlight_source_indices");
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
});
