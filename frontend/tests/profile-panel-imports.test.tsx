import { act, createContext, useContext, type ButtonHTMLAttributes, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProfilePanel } from "@/components/profile/profile-panel";

const apiMocks = vi.hoisted(() => ({
  getProfile: vi.fn(),
  listImportDrafts: vi.fn(),
  getImportDraft: vi.fn(),
  approveImport: vi.fn(),
  rejectImport: vi.fn(),
  updateProfile: vi.fn(),
  upload: vi.fn(),
}));
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/api", () => ({ resumeApi: apiMocks, ApiError: class ApiError extends Error {} }));
vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("@/context/resume-task-context", () => ({
  useResumeTask: () => ({ setTask: vi.fn(), isRunning: false, progressMsg: null }),
}));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open?: boolean; children: ReactNode }) => open ? <div role="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}));
const AlertDialogState = createContext<((open: boolean) => void) | null>(null);
vi.mock("@/components/ui/alert-dialog", () => {
  function AlertDialog({ open, onOpenChange, children }: { open?: boolean; onOpenChange?: (open: boolean) => void; children: ReactNode }) {
    return open ? <AlertDialogState.Provider value={onOpenChange ?? null}><div role="alertdialog">{children}</div></AlertDialogState.Provider> : null;
  }
  function AlertDialogCancel({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
    const onOpenChange = useContext(AlertDialogState);
    return <button {...props} onClick={(event) => { props.onClick?.(event); onOpenChange?.(false); }}>{children}</button>;
  }
  return {
    AlertDialog,
    AlertDialogAction: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button>,
    AlertDialogCancel,
    AlertDialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    AlertDialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
    AlertDialogFooter: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    AlertDialogHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    AlertDialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
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
    links: { linkedin: null, github: null, portfolio: null, website: null },
    projects: [],
    work_history: [],
    preferred_roles: [],
    seniority: "senior",
  },
};
const draft = { resume_id: 11, filename: "replacement.pdf", created_at: "2026-07-30T00:00:00Z", lifecycle_status: "draft" as const };
const detail = { ...draft, is_active: false, profile: { ...profile.profile, full_name: "Draft Ada", links: { linkedin: "https://linkedin.com/in/draft", github: null, portfolio: null, website: null }, projects: [{ name: "Project X", description: null, technologies: ["Python"], achievements: [], url: null }] } };

describe("ProfilePanel imported resume drafts", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
    apiMocks.getProfile.mockResolvedValue(profile);
    apiMocks.listImportDrafts.mockResolvedValue([draft]);
    apiMocks.getImportDraft.mockReset();
    apiMocks.getImportDraft.mockResolvedValue(detail);
    apiMocks.approveImport.mockReset();
    apiMocks.rejectImport.mockReset();
    apiMocks.updateProfile.mockResolvedValue(profile);
    toastMocks.success.mockReset();
    toastMocks.error.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  const render = async () => {
    await act(async () => root.render(<QueryClientProvider client={queryClient}><ProfilePanel /></QueryClientProvider>));
    await vi.waitFor(() => expect(container.textContent).toContain("Imported resume drafts"));
  };

  const openReview = async () => {
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Review draft"))?.click());
    await vi.waitFor(() => expect(container.textContent).toContain("Draft Ada"));
  };

  const clickButton = async (text: string) => {
    await act(async () => (Array.from(container.querySelectorAll("button")).find((button) => button.textContent === text) as HTMLButtonElement | undefined)?.click());
  };

  const clickAlertButton = async (text: string) => {
    await act(async () => (Array.from(container.querySelectorAll('[role="alertdialog"] button')).find((button) => button.textContent === text) as HTMLButtonElement | undefined)?.click());
  };

  it("shows loading and error states", async () => {
    let resolveList!: (value: typeof draft[]) => void;
    apiMocks.listImportDrafts.mockReturnValue(new Promise((resolve) => { resolveList = resolve; }));
    await act(async () => root.render(<QueryClientProvider client={queryClient}><ProfilePanel /></QueryClientProvider>));
    await vi.waitFor(() => expect(container.textContent).toContain("Imported resume drafts"));
    expect(container.textContent).toContain("Loading imported resume drafts");
    resolveList([]);
    await vi.waitFor(() => expect(container.textContent).toContain("No imported resume drafts awaiting review."));

    apiMocks.listImportDrafts.mockRejectedValue(new Error("network failure"));
    queryClient.removeQueries({ queryKey: ["resume-imports"] });
    await act(async () => root.render(<QueryClientProvider client={queryClient}><ProfilePanel /></QueryClientProvider>));
    await vi.waitFor(() => expect(container.textContent).toContain("Could not load imported resume drafts."));
  });

  it("loads metadata without fetching parsed detail until opened", async () => {
    await render();
    expect(container.textContent).toContain("replacement.pdf");
    expect(container.textContent).not.toContain("Draft Ada");
    expect(apiMocks.getImportDraft).not.toHaveBeenCalled();
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Review draft"))?.click());
    await vi.waitFor(() => expect(apiMocks.getImportDraft).toHaveBeenCalledWith(11));
    await vi.waitFor(() => expect(container.textContent).toContain("Draft Ada"));
    expect(container.textContent).toContain("Draft Ada");
    expect(container.textContent).toContain("Project X");
    expect(container.querySelector('a[href="https://linkedin.com/in/draft"]')).not.toBeNull();
  });

  it("shows an empty state", async () => {
    apiMocks.listImportDrafts.mockResolvedValue([]);
    await render();
    expect(container.textContent).toContain("No imported resume drafts awaiting review.");
  });

  it("requires approval confirmation and invalidates profile and imports", async () => {
    apiMocks.getImportDraft.mockResolvedValue(detail);
    apiMocks.approveImport.mockResolvedValue(profile);
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const removeSpy = vi.spyOn(queryClient, "removeQueries");
    await render();
    await openReview();
    await clickButton("Approve draft");
    expect(apiMocks.approveImport).not.toHaveBeenCalled();
    await clickAlertButton("Approve draft");
    await vi.waitFor(() => expect(apiMocks.approveImport).toHaveBeenCalledWith(11));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["profile"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["resume-imports"] });
    expect(removeSpy).toHaveBeenCalledWith({ queryKey: ["resume-import", 11] });
    expect(toastMocks.success).toHaveBeenCalledWith("Profile updated. Match rankings will update shortly.");
  });

  it("rejects without invalidating the active profile", async () => {
    apiMocks.getImportDraft.mockResolvedValue(detail);
    apiMocks.rejectImport.mockResolvedValue(undefined);
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const removeSpy = vi.spyOn(queryClient, "removeQueries");
    await render();
    await openReview();
    await clickButton("Reject draft");
    expect(apiMocks.rejectImport).not.toHaveBeenCalled();
    await clickAlertButton("Reject draft");
    await vi.waitFor(() => expect(apiMocks.rejectImport).toHaveBeenCalledWith(11));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["resume-imports"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["profile"] });
    expect(removeSpy).toHaveBeenCalledWith({ queryKey: ["resume-import", 11] });
    expect(toastMocks.success).toHaveBeenCalledWith("Imported resume draft rejected.");
  });

  it("cancels approve and reject confirmations without sending requests", async () => {
    await render();
    await openReview();
    await clickButton("Approve draft");
    await clickAlertButton("Cancel");
    expect(apiMocks.approveImport).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alertdialog"]')).toBeNull();
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();

    await clickButton("Reject draft");
    await clickAlertButton("Cancel");
    expect(apiMocks.rejectImport).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alertdialog"]')).toBeNull();
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("closes review without approving or rejecting", async () => {
    await render();
    await openReview();
    await clickButton("Cancel");
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(apiMocks.approveImport).not.toHaveBeenCalled();
    expect(apiMocks.rejectImport).not.toHaveBeenCalled();
  });

  it("surfaces approve and reject failures without closing review", async () => {
    apiMocks.approveImport.mockRejectedValueOnce(new Error("approve failed"));
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    await render();
    await openReview();
    await clickButton("Approve draft");
    await clickAlertButton("Approve draft");
    await vi.waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Something went wrong"));
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();

    apiMocks.rejectImport.mockRejectedValueOnce(new Error("reject failed"));
    await clickButton("Reject draft");
    await clickAlertButton("Reject draft");
    await vi.waitFor(() => expect(toastMocks.error).toHaveBeenCalledTimes(2));
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    expect(
      invalidateSpy.mock.calls.some(([filters]) =>
        Array.isArray(filters?.queryKey) &&
        filters.queryKey.length === 1 &&
        filters.queryKey[0] === "profile",
      ),
    ).toBe(false);
  });

  it("prevents duplicate approve and reject submissions while pending", async () => {
    let resolveApprove!: (value: typeof profile) => void;
    apiMocks.approveImport.mockReturnValue(new Promise((resolve) => { resolveApprove = resolve; }));
    await render();
    await openReview();
    await clickButton("Approve draft");
    await clickAlertButton("Approve draft");
    await vi.waitFor(() => expect(apiMocks.approveImport).toHaveBeenCalledTimes(1));
    await act(async () => { for (const button of Array.from(container.querySelectorAll("button")).filter((item) => item.textContent?.includes("Approve draft"))) (button as HTMLButtonElement).click(); });
    expect(apiMocks.approveImport).toHaveBeenCalledTimes(1);
    resolveApprove(profile);
    await vi.waitFor(() => expect(container.querySelector('[role="dialog"]')).toBeNull());

    let resolveReject!: (value: undefined) => void;
    apiMocks.listImportDrafts.mockResolvedValue([draft]);
    apiMocks.getImportDraft.mockResolvedValue(detail);
    apiMocks.rejectImport.mockReturnValue(new Promise((resolve) => { resolveReject = resolve; }));
    await render();
    await openReview();
    await clickButton("Reject draft");
    await clickAlertButton("Reject draft");
    await vi.waitFor(() => expect(apiMocks.rejectImport).toHaveBeenCalledTimes(1));
    await act(async () => { for (const button of Array.from(container.querySelectorAll("button")).filter((item) => item.textContent?.includes("Reject draft"))) (button as HTMLButtonElement).click(); });
    expect(apiMocks.rejectImport).toHaveBeenCalledTimes(1);
    resolveReject(undefined);
  });
});
