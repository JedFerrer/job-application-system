import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ButtonHTMLAttributes, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ResumePanel } from "@/components/resume/resume-panel";

const apiMocks = vi.hoisted(() => ({
  getProfile: vi.fn(),
  listDrafts: vi.fn(),
  getDraft: vi.fn(),
  deleteDraft: vi.fn(),
  tailorResume: vi.fn(),
  upload: vi.fn(),
}));
const exportMocks = vi.hoisted(() => ({
  downloadResumePdf: vi.fn(),
  downloadResumeDocx: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ resumeApi: apiMocks }));
vi.mock("@/lib/cover-letter-export", () => exportMocks);
vi.mock("@/context/resume-task-context", () => ({
  useResumeTask: () => ({ setTask: vi.fn(), isRunning: false, progressMsg: null }),
}));
vi.mock("@/components/ui/alert-dialog", () => {
  const Root = ({ open, children }: { open?: boolean; children: ReactNode }) =>
    open ? <div role="alertdialog">{children}</div> : null;
  const Box = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  const Action = ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  );
  return {
    AlertDialog: Root,
    AlertDialogAction: Action,
    AlertDialogCancel: Action,
    AlertDialogContent: Box,
    AlertDialogDescription: Box,
    AlertDialogFooter: Box,
    AlertDialogHeader: Box,
    AlertDialogTitle: Box,
  };
});
vi.mock("@/components/ui/dialog", () => {
  const Box = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  return {
    Dialog: Box,
    DialogContent: ({ children }: { children: ReactNode }) => <div role="dialog">{children}</div>,
    DialogDescription: Box,
    DialogHeader: Box,
    DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
  };
});

const profile = {
  profile_id: 8,
  resume_id: 3,
  profile: {
    full_name: "Ada Lovelace",
    email: "ada@example.com",
    phone: null,
    headline: "Backend Engineer",
    years_experience: 5,
    skills: ["python"],
    education: [],
    certifications: [],
    links: { linkedin: "https://linkedin.com/in/ada", github: null, portfolio: null, website: null },
    projects: [{ name: "Analytical Engine", description: "A computing project", technologies: ["Python"], achievements: ["Reduced errors"], url: "https://example.com/project" }],
    work_history: [],
    preferred_roles: [],
    seniority: "senior",
  },
};
const content = {
  job_id: 101,
  professional_summary: "Backend Engineer",
  full_name: "Ada Lovelace",
  email: "ada@example.com",
  phone: null,
  headline: "Backend Engineer",
  work_history: [],
  education: [],
  certifications: [],
  matched_skills: ["python"],
  other_skills: [],
  missing_keywords: [],
};
const draft = {
  id: 1,
  job_id: 101,
  source_profile_id: 8,
  job_title: "Backend Engineer",
  job_company: "Acme",
  created_at: "2026-07-14T00:00:00Z",
  updated_at: "2026-07-14T00:00:00Z",
};
const detail = { ...draft, content };

describe("ResumePanel saved tailored drafts", () => {
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
    apiMocks.getProfile.mockResolvedValue(profile);
    apiMocks.listDrafts.mockResolvedValue([draft]);
    apiMocks.getDraft.mockResolvedValue(detail);
    apiMocks.deleteDraft.mockResolvedValue(undefined);
    apiMocks.tailorResume.mockReset();
    exportMocks.downloadResumePdf.mockReset();
    exportMocks.downloadResumeDocx.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  const render = async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ResumePanel />
        </QueryClientProvider>,
      );
    });
  };

  it("shows the loading state", async () => {
    let resolveList!: (value: unknown[]) => void;
    apiMocks.listDrafts.mockReturnValue(new Promise((resolve) => { resolveList = resolve; }));
    await render();
    expect(container.textContent).toContain("Loading saved resumes");
    await act(async () => resolveList([]));
    await vi.waitFor(() => expect(container.textContent).toContain("No tailored resumes yet"));
  });

  it("shows the empty state", async () => {
    apiMocks.listDrafts.mockResolvedValue([]);
    await render();
    await vi.waitFor(() => expect(container.textContent).toContain("No tailored resumes yet"));
    expect(container.querySelector('a[href="/matches"]')?.textContent).toContain("Browse job matches");
  });

  it("shows the error state", async () => {
    apiMocks.listDrafts.mockRejectedValue(new Error("failed"));
    await render();
    await vi.waitFor(() => expect(container.textContent).toContain("Could not load saved resumes."));
  });

  it("renders metadata and opens the saved snapshot without tailoring", async () => {
    await render();
    await vi.waitFor(() => expect(container.textContent).toContain("Acme"));
    expect(apiMocks.getProfile).not.toHaveBeenCalled();
    expect(apiMocks.upload).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("Upload résumé");
    expect(container.textContent).not.toContain("Analytical Engine");
    expect(container.textContent).toContain("Jul 14, 2026");
    const open = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("Open"));
    await act(async () => open?.click());
    await vi.waitFor(() => expect(container.textContent).toContain("Saved AI-generated draft. Review every claim before use."));
    expect(apiMocks.getDraft).toHaveBeenCalledWith(1);
    expect(apiMocks.tailorResume).not.toHaveBeenCalled();
  });

  it("uses the saved snapshot for copy and exports", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    await render();
    await vi.waitFor(() => expect(container.textContent).toContain("Acme"));
    await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("Open"))?.click());
    await vi.waitFor(() => expect(container.textContent).toContain("Copy all"));
    await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("Copy all"))?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "PDF")?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "DOCX")?.click());
    expect(navigator.clipboard.writeText).toHaveBeenCalled();
    expect(exportMocks.downloadResumePdf).toHaveBeenCalledWith(content, "Ada_Lovelace_Resume");
    expect(exportMocks.downloadResumeDocx).toHaveBeenCalledWith(content, "Ada_Lovelace_Resume");
  });

  it("deletes the selected draft, refreshes the list, and closes its viewer", async () => {
    apiMocks.listDrafts.mockReset();
    apiMocks.listDrafts.mockResolvedValueOnce([draft]).mockResolvedValueOnce([]);
    await render();
    await vi.waitFor(() => expect(container.textContent).toContain("Acme"));
    await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("Open"))?.click());
    await vi.waitFor(() => expect(container.textContent).toContain("Saved AI-generated draft"));
    const deleteButton = container.querySelector(
      'button[aria-label="Delete saved draft for Backend Engineer"]',
    ) as HTMLButtonElement | null;
    await act(async () => deleteButton?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Delete")?.click());
    await vi.waitFor(() => expect(apiMocks.deleteDraft).toHaveBeenCalledWith(1));
    expect(apiMocks.listDrafts.mock.calls.length).toBeGreaterThan(1);
    await vi.waitFor(() =>
      expect(container.textContent).not.toContain(
        "Saved AI-generated draft. Review every claim before use.",
      ),
    );
  });
});
