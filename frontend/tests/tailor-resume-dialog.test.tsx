import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";

const apiMocks = vi.hoisted(() => ({
  tailorResume: vi.fn(),
  createDraft: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  resumeApi: apiMocks,
}));

vi.mock("@/lib/cover-letter-export", () => ({
  downloadResumePdf: vi.fn(),
  downloadResumeDocx: vi.fn(),
}));

vi.mock("@/components/ui/dialog", () => {
  const Dialog = ({ children }: { children: ReactNode }) => <>{children}</>;
  const DialogContent = ({ children }: { children: ReactNode }) => (
    <div role="dialog">{children}</div>
  );
  const DialogHeader = ({ children }: { children: ReactNode }) => <div>{children}</div>;
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
  work_history: [
    {
      company: "Acme",
      title: "Engineer",
      start: "2020-01",
      end: "2022-01",
      original_highlights: ["Built APIs"],
      tailored_highlights: ["Built Python APIs"],
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
    apiMocks.createDraft.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  const renderedText = () => container.textContent ?? "";

  it("labels generated content as a review-required draft", async () => {
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

    await vi.waitFor(() =>
      expect(renderedText()).toContain("Acme"),
    );

    expect(apiMocks.tailorResume).toHaveBeenCalledWith(101);
    expect(renderedText()).toContain(
      "AI-generated draft based on your profile. Review every claim before copying or downloading.",
    );
    expect(renderedText()).not.toContain("No skills or experience were invented.");
    expect(renderedText()).toContain("Acme");
    expect(renderedText()).toContain("Built Python APIs");
    expect(renderedText()).toContain("Copy all");
    expect(renderedText()).toContain("PDF");
    expect(renderedText()).toContain("DOCX");
    expect(renderedText()).toContain("Regenerate");
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

    expect(renderedText()).toContain("Tailored resume");
    expect(container.querySelector("svg")).not.toBeNull();
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
    await vi.waitFor(() => expect(renderedText()).toContain("Acme"));

    const saveButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Save draft"),
    );
    expect(saveButton).toBeDefined();
    await act(async () => {
      saveButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(apiMocks.createDraft).toHaveBeenCalledWith({
      job_id: 101,
      content: tailoredResume,
    });
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

    await vi.waitFor(() =>
      expect(renderedText()).toContain(
        "Could not load tailored resume.",
      ),
    );

    expect(apiMocks.tailorResume).toHaveBeenCalledWith(103);
  });
});
