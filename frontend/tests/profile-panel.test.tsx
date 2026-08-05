import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProfilePanel } from "@/components/profile/profile-panel";

const apiMocks = vi.hoisted(() => ({
  getProfile: vi.fn(),
  listImportDrafts: vi.fn(),
  updateProfile: vi.fn(),
  upload: vi.fn(),
}));
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/api", () => ({
  resumeApi: apiMocks,
  ApiError: class ApiError extends Error {},
}));
vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("@/context/resume-task-context", () => ({
  useResumeTask: () => ({ setTask: vi.fn(), isRunning: false, progressMsg: null }),
}));

const profile = {
  profile_id: 8,
  resume_id: 3,
  matching_profile_changed: false,
  profile: {
    full_name: "Ada Lovelace",
    email: "ada@example.com",
    phone: null,
    headline: "Backend Engineer",
    years_experience: 5,
    skills: ["python"],
    education: [],
    certifications: [],
    languages: [
      { language: "Spanish", proficiency: "Professional" },
      { language: "English", proficiency: null },
    ],
    licenses: [{ name: "Registered Engineer", issuer: "State Board" }],
    links: {
      linkedin: "https://linkedin.com/in/ada",
      github: null,
      portfolio: null,
      website: null,
    },
    projects: [{
      name: "Analytical Engine",
      description: "A computing project",
      technologies: ["Python"],
      achievements: ["Reduced errors"],
      url: "https://example.com/project",
    }],
    work_history: [],
    preferred_roles: [],
    seniority: "senior",
  },
};

describe("ProfilePanel projects and professional links", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
    apiMocks.getProfile.mockResolvedValue(profile);
    apiMocks.listImportDrafts.mockResolvedValue([]);
    apiMocks.updateProfile.mockReset();
    apiMocks.updateProfile.mockResolvedValue({ ...profile, matching_profile_changed: false });
    apiMocks.upload.mockReset();
    toastMocks.success.mockReset();
    toastMocks.error.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  const render = async (expected = "Analytical Engine") => {
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><ProfilePanel /></QueryClientProvider>);
    });
    await vi.waitFor(() => expect(container.textContent).toContain(expected));
  };

  const setInput = async (selector: string, value: string) => {
    const input = container.querySelector(selector) as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    await act(async () => {
      setter?.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };

  const setTextArea = async (selector: string, value: string) => {
    const textarea = container.querySelector(selector) as HTMLTextAreaElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    await act(async () => {
      setter?.call(textarea, value);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };

  it("displays links and projects and supports editing them", async () => {
    await render();
    const link = container.querySelector('a[href="https://linkedin.com/in/ada"]');
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(container.textContent).toContain("Reduced errors");

    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Edit profile"))?.click());
    const github = container.querySelector("#pf-link-github") as HTMLInputElement;
    await setInput("#pf-link-github", " https://github.com/ada ");
    await act(async () => (container.querySelector('button[aria-label="Remove project 1"]') as HTMLButtonElement | null)?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Add project"))?.click());
    expect(container.querySelector("#pf-project-0-name")).not.toBeNull();
    await setInput("#pf-project-0-name", "New project");
    await setTextArea("#pf-project-0-description", "A new description");
    await setInput("#pf-project-0-url", "https://example.com/new-project");
    await setTextArea("#pf-project-0-technologies", "TypeScript\nReact");
    await setTextArea("#pf-project-0-achievements", "Shipped feature\nImproved UX");
    await act(async () => (Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save all changes")) as HTMLButtonElement | undefined)?.click());
    await vi.waitFor(() => expect(apiMocks.updateProfile).toHaveBeenCalled());
    const saved = apiMocks.updateProfile.mock.calls.at(-1)?.[0];
    expect(saved.links.github).toBe("https://github.com/ada");
    expect(saved.projects[0].name).toBe("New project");
    expect(saved.projects[0].technologies).toEqual(["TypeScript", "React"]);
    expect(saved.projects[0].achievements).toEqual(["Shipped feature", "Improved UX"]);
    expect(toastMocks.success).toHaveBeenCalledWith("Profile updated");
  });

  it("places education in the left-column order before certifications", async () => {
    apiMocks.getProfile.mockResolvedValue({
      ...profile,
      profile: {
        ...profile.profile,
        education: [{ degree: "Computer Science", institution: "Polytechnic Institute", year: "2014" }],
        certifications: ["Hardware Servicing"],
      },
    });
    await render();

    const educationHeading = Array.from(container.querySelectorAll("div")).find(
      (element) => element.textContent?.trim() === "Education",
    );
    const certificationsHeading = Array.from(container.querySelectorAll("div")).find(
      (element) => element.textContent?.trim() === "Certifications",
    );
    expect(educationHeading).toBeDefined();
    expect(certificationsHeading).toBeDefined();
    expect(
      (educationHeading as Node).compareDocumentPosition(certificationsHeading as Node) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("uses a full-width degree field above institution and year", async () => {
    apiMocks.getProfile.mockResolvedValue({
      ...profile,
      profile: {
        ...profile.profile,
        education: [{ degree: "Computer Science", institution: "Polytechnic Institute", year: "2014" }],
      },
    });
    await render();
    await act(async () =>
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Edit profile"))
        ?.click(),
    );

    const degreeInput = Array.from(container.querySelectorAll("input")).find(
      (input) => input.placeholder === "e.g. B.S. Computer Science",
    );
    const educationGrid = degreeInput?.closest(".grid");
    expect(degreeInput?.parentElement?.className).toContain("sm:col-span-2");
    expect(educationGrid?.className).toContain("sm:grid-cols-2");
  });

  it("displays and edits languages with optional proficiency", async () => {
    await render("Spanish");
    expect(container.textContent).toContain("Professional");
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Edit profile"))?.click());
    await setInput("#pf-language-proficiency-0", " Fluent ");
    await act(async () => (container.querySelector('button[aria-label="Remove English"]') as HTMLButtonElement).click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save all changes"))?.click());
    await vi.waitFor(() => expect(apiMocks.updateProfile).toHaveBeenCalled());
    const saved = apiMocks.updateProfile.mock.calls.at(-1)?.[0];
    expect(saved.languages).toEqual([{ language: "Spanish", proficiency: "Fluent" }]);
  });

  it("displays and edits licenses with an optional issuer", async () => {
    await render("Registered Engineer");
    expect(container.textContent).toContain("State Board");
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Edit profile"))?.click());
    await setInput("#pf-license-issuer-0", " Engineering Board ");
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Add credential"))?.click());
    await setInput("#pf-license-1", "Safety Registration");
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save all changes"))?.click());
    await vi.waitFor(() => expect(apiMocks.updateProfile).toHaveBeenCalled());
    const saved = apiMocks.updateProfile.mock.calls.at(-1)?.[0];
    expect(saved.licenses).toEqual([
      { name: "Registered Engineer", issuer: "Engineering Board" },
      { name: "Safety Registration", issuer: null },
    ]);
  });

  it("shows clear empty states for missing links and projects", async () => {
    apiMocks.getProfile.mockResolvedValue({
      ...profile,
      profile: {
        ...profile.profile,
        links: { linkedin: null, github: null, portfolio: null, website: null },
        projects: [],
        languages: [],
        licenses: [],
      },
    });
    await render("No projects added.");
    expect(container.textContent).toContain("No professional links added.");
    expect(container.textContent).toContain("No languages added.");
    expect(container.textContent).toContain("No licenses or registrations added.");
  });

  it("clears a populated professional link to null while preserving the full payload", async () => {
    await render();
    await act(async () =>
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Edit profile"))
        ?.click(),
    );
    await setInput("#pf-link-linkedin", "   ");
    await act(async () =>
      (Array.from(container.querySelectorAll("button")).find((button) =>
        button.textContent?.includes("Save all changes"),
      ) as HTMLButtonElement | undefined)?.click(),
    );

    await vi.waitFor(() => expect(apiMocks.updateProfile).toHaveBeenCalled());
    const saved = apiMocks.updateProfile.mock.calls.at(-1)?.[0];
    expect(saved.links.linkedin).toBeNull();
    expect(saved.links.github).toBeNull();
    expect(saved.projects).toEqual(profile.profile.projects);
    expect(toastMocks.success).toHaveBeenCalledWith("Profile updated");
  });

  it("blocks invalid URLs with accessible errors and preserves cancel", async () => {
    await render();
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Edit profile"))?.click());
    const github = container.querySelector("#pf-link-github") as HTMLInputElement;
    await setInput("#pf-link-github", "mailto:ada@example.com");
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save all changes"))?.click());
    expect(apiMocks.updateProfile).not.toHaveBeenCalled();
    const error = container.querySelector("#link-github-error");
    expect(error?.textContent).toContain("full HTTP or HTTPS URL");
    expect(github.getAttribute("aria-invalid")).toBe("true");
    expect(github.getAttribute("aria-describedby")).toBe("link-github-error");

    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Cancel")?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Edit profile"))?.click());
    expect((container.querySelector("#pf-link-github") as HTMLInputElement).value).toBe("");
    expect((container.querySelector("#pf-link-linkedin") as HTMLInputElement).value).toBe("https://linkedin.com/in/ada");
  });

  it("blocks an invalid project URL with project-specific ARIA wiring", async () => {
    await render();
    await act(async () =>
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Edit profile"))
        ?.click(),
    );
    await setInput("#pf-project-0-url", "ftp://example.com");
    await act(async () =>
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Save all changes"))
        ?.click(),
    );

    expect(apiMocks.updateProfile).not.toHaveBeenCalled();
    const projectUrl = container.querySelector("#pf-project-0-url") as HTMLInputElement;
    const error = container.querySelector("#project-0-url-error");
    expect(error?.textContent).toContain("full HTTP or HTTPS URL");
    expect(projectUrl.getAttribute("aria-invalid")).toBe("true");
    expect(projectUrl.getAttribute("aria-describedby")).toBe("project-0-url-error");
  });

  it("retains edit state and unsaved values when saving fails", async () => {
    apiMocks.updateProfile.mockRejectedValue(new Error("network failure"));
    await render();
    await act(async () =>
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Edit profile"))
        ?.click(),
    );
    await setInput("#pf-link-github", "https://github.com/ada-updated");
    await act(async () =>
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Save all changes"))
        ?.click(),
    );

    await vi.waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Update failed"));
    expect(container.textContent).toContain("Save all changes");
    expect((container.querySelector("#pf-link-github") as HTMLInputElement).value).toBe(
      "https://github.com/ada-updated",
    );
  });

  it("keeps matching-refresh messaging from the backend response", async () => {
    apiMocks.updateProfile.mockResolvedValue({ ...profile, matching_profile_changed: true });
    await render();
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Edit profile"))?.click());
    await act(async () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Save all changes"))?.click());
    await vi.waitFor(() => expect(toastMocks.success).toHaveBeenCalledWith("Profile updated. Match rankings will update shortly."));
  });
});
