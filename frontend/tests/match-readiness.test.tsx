import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobCard, type JobCardData, type JobCardMatch } from "@/components/jobs/job-card";
import { JobDetailView } from "@/components/jobs/job-detail-view";

const apiMocks = vi.hoisted(() => {
  const jobsApi = {
    get: vi.fn(),
    archive: vi.fn(),
  };
  const applicationsApi = {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  };
  const matchesApi = {
    explain: vi.fn(),
    skills: vi.fn(),
    rank: vi.fn(),
    list: vi.fn(),
  };
  const listAllApplications = vi.fn();

  class ApiError extends Error {
    status: number;
    detail: unknown;

    constructor(message: string, status: number, detail: unknown) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.detail = detail;
    }
  }

  return { jobsApi, applicationsApi, matchesApi, listAllApplications, ApiError };
});

const routerMocks = vi.hoisted(() => {
  const back = vi.fn();
  const push = vi.fn();
  return { back, push };
});

const toastMocks = vi.hoisted(() => {
  const success = vi.fn();
  const error = vi.fn();
  return { success, error };
});

vi.mock("@/lib/api", () => apiMocks);

vi.mock("next/navigation", () => ({
  useRouter: () => routerMocks,
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastMocks.success,
    error: toastMocks.error,
  },
}));

vi.mock("@/components/cover/cover-letter-dialog", () => ({
  CoverLetterDialog: () => null,
}));

vi.mock("@/components/interview/interview-prep-dialog", () => ({
  InterviewPrepDialog: () => null,
}));

vi.mock("@/components/resume/tailor-resume-dialog", () => ({
  TailorResumeDialog: () => null,
}));

describe("JobCard readiness", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("renders a compact readiness badge when readiness is present", async () => {
    const job: JobCardData = {
      jobId: 1,
      title: "Backend Engineer",
      company: "Acme",
      location: "Remote",
      isRemote: true,
      jobType: "fulltime",
      shiftType: null,
      salaryMin: null,
      salaryMax: null,
      salaryCurrency: null,
      source: "test",
      sourceUrl: "https://example.com/job/1",
      description: "Build APIs",
      postedAt: null,
      importedAt: null,
      companyLogo: null,
    };
    const match: JobCardMatch = {
      score: 87,
      confidence: "strong",
      matchedSkills: ["python", "fastapi"],
      readiness: {
        decision: "apply",
        reasons: ["Strong match score", "2 matched skills found"],
        caveats: [],
        deterministic_blockers: [],
        deterministic_warnings: [],
        decision_basis: "score",
      },
    };

    await act(async () => {
      root.render(
        <JobCard
          job={job}
          match={match}
          hideExternalLink
          showConfidenceLabel={false}
        />,
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Ready to apply");
    expect(container.textContent).toContain("Based on match score");
    expect(container.textContent).toContain("Suggested: Apply");
    expect(container.textContent).not.toContain("Stretch fit");
  });

  it("summarizes blocker and warning readiness states in one line", async () => {
    const job: JobCardData = {
      jobId: 3,
      title: "Data Engineer",
      company: "Gamma",
      location: "Remote",
      isRemote: true,
      jobType: "fulltime",
      shiftType: null,
      salaryMin: null,
      salaryMax: null,
      salaryCurrency: null,
      source: "test",
      sourceUrl: "https://example.com/job/3",
      description: "Build data systems",
      postedAt: null,
      importedAt: null,
      companyLogo: null,
    };

    const renderMatch = async (readiness: NonNullable<JobCardMatch["readiness"]>) => {
      await act(async () => {
        root.render(
          <JobCard
            job={job}
            match={{ score: 90, confidence: "strong", matchedSkills: [], readiness }}
            hideExternalLink
            showConfidenceLabel={false}
          />,
        );
        await Promise.resolve();
      });
    };

    await renderMatch({
      decision: "skip",
      reasons: ["A rule applies"],
      caveats: [],
      deterministic_blockers: ["Blocked keyword: contract"],
      deterministic_warnings: [],
      decision_basis: "blocker",
    });
    expect(container.textContent).toContain("Blocked by your rule");
    expect(container.textContent).toContain("Suggested: Skip");

    await renderMatch({
      decision: "save",
      reasons: ["Review needed"],
      caveats: [],
      deterministic_blockers: [],
      deterministic_warnings: ["Remote status is unclear"],
      decision_basis: "score",
    });
    expect(container.textContent).toContain("Needs review");
    expect(container.textContent).toContain("Suggested: Review");
  });

  it("keeps JobCard usable when readiness is absent", async () => {
    const job: JobCardData = {
      jobId: 2,
      title: "Platform Engineer",
      company: "Beta",
      location: "Remote",
      isRemote: true,
      jobType: "fulltime",
      shiftType: null,
      salaryMin: null,
      salaryMax: null,
      salaryCurrency: null,
      source: "test",
      sourceUrl: "https://example.com/job/2",
      description: "Build services",
      postedAt: null,
      importedAt: null,
      companyLogo: null,
    };

    await act(async () => {
      root.render(<JobCard job={job} hideExternalLink />);
      await Promise.resolve();
    });

    expect(container.textContent).not.toContain("Ready to apply");
  });
});

describe("JobDetailView readiness", () => {
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
        },
      },
    });

    routerMocks.back.mockReset();
    routerMocks.push.mockReset();
    toastMocks.success.mockReset();
    toastMocks.error.mockReset();
    apiMocks.jobsApi.get.mockReset();
    apiMocks.jobsApi.archive.mockReset();
    apiMocks.applicationsApi.list.mockReset();
    apiMocks.applicationsApi.create.mockReset();
    apiMocks.applicationsApi.update.mockReset();
    apiMocks.matchesApi.explain.mockReset();
    apiMocks.matchesApi.skills.mockReset();
    apiMocks.matchesApi.rank.mockReset();
    apiMocks.matchesApi.list.mockReset();
    apiMocks.listAllApplications.mockReset();

    apiMocks.jobsApi.get.mockResolvedValue({
      id: 501,
      title: "Backend Engineer",
      company: "Acme",
      location: "Remote",
      is_remote: true,
      job_type: "fulltime",
      description: "Build APIs",
      salary_min: null,
      salary_max: null,
      salary_currency: null,
      source: "test",
      source_url: "https://example.com/job/501",
      posted_at: null,
      fetched_at: "2026-07-14T00:00:00Z",
      shift_type: null,
      company_logo: null,
      visibility: "public",
      ingest_source: "scraper",
      created_by_user_id: null,
      imported_at: null,
      match_score: null,
      match_reason: null,
    });
    apiMocks.listAllApplications.mockResolvedValue([]);
    apiMocks.matchesApi.skills.mockResolvedValue({
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python", "fastapi"],
      missing_keywords: ["kubernetes"],
      readiness: {
        decision: "apply",
        reasons: ["Strong match score", "2 matched skills found"],
        caveats: ["Detailed skill gaps are not available yet"],
        deterministic_blockers: ["Blocked keyword: contract"],
        deterministic_warnings: ["Salary data needs review"],
        decision_basis: "blocker",
      },
    });
    queryClient.setQueryData(["match-skills", 501], {
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python", "fastapi"],
      missing_keywords: ["kubernetes"],
      readiness: {
        decision: "apply",
        reasons: ["Strong match score", "2 matched skills found"],
        caveats: ["Detailed skill gaps are not available yet"],
        deterministic_blockers: ["Blocked keyword: contract"],
        deterministic_warnings: ["Salary data needs review"],
        decision_basis: "blocker",
      },
    });

    queryClient.setQueryData(
      ["job", 501],
      {
        id: 501,
        title: "Backend Engineer",
        company: "Acme",
        location: "Remote",
        is_remote: true,
        job_type: "fulltime",
        description: "Build APIs",
        salary_min: null,
        salary_max: null,
        salary_currency: null,
        source: "test",
        source_url: "https://example.com/job/501",
        posted_at: null,
        fetched_at: "2026-07-14T00:00:00Z",
        shift_type: null,
        company_logo: null,
        visibility: "public",
        ingest_source: "scraper",
        created_by_user_id: null,
        imported_at: null,
        match_score: null,
        match_reason: null,
      },
    );

    queryClient.setQueryData(
      ["matches", "seed"],
      {
        pages: [
          {
            items: [
              {
                job_id: 501,
                title: "Backend Engineer",
                company: "Acme",
                location: "Remote",
                is_remote: true,
                job_type: "fulltime",
                description: "Build APIs",
                shift_type: null,
                salary_min: null,
                salary_max: null,
                salary_currency: null,
                source: "test",
                source_url: "https://example.com/job/501",
                company_logo: null,
                vector_score: 87,
                confidence: "strong",
                llm_score: null,
                matched_skills: ["python", "fastapi"],
                readiness: {
                  decision: "apply",
                  reasons: [
                    "Strong match score",
                    "2 matched skills found",
                    "Known gaps: kubernetes, graphql",
                  ],
                  caveats: ["Detailed skill gaps are not available yet"],
                  deterministic_blockers: ["Blocked keyword: contract"],
                  deterministic_warnings: ["Salary data needs review"],
                  decision_basis: "blocker",
                },
                posted_at: null,
              },
            ],
            total: 1,
            limit: 20,
            offset: 0,
            next_cursor: null,
            has_more: false,
          },
        ],
        pageParams: [null],
      },
    );
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    queryClient.clear();
    container.remove();
  });

  it("renders readiness details in the Match Score card", async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("Ready to apply");
    expect(container.textContent).toContain("Suggested next action");
    expect(container.textContent).toContain("Suggested: Skip");
    expect(container.textContent).toContain("Based on your rules");
    expect(container.textContent).not.toContain("Go to action");
    expect(container.textContent).not.toContain("View action");
    expect(container.textContent).toContain("Strong match score");
    expect(container.textContent).toContain("Blocked by your rule");
    expect(container.textContent).toContain("Blocked keyword: contract");
    expect(container.textContent).toContain("Needs review");
    expect(container.textContent).toContain("Salary data needs review");
    expect(container.textContent).toContain("2 matched skills found");
    expect(container.textContent).toContain("Missingkubernetes");
    expect(container.textContent).toContain(
      "Detailed skill gaps are not available yet",
    );
    expect(container.textContent).toContain("Get Detailed AI Assessment");
    expect(container.textContent).not.toContain("Known gaps: kubernetes, graphql");
    expect(container.textContent).not.toContain("Stretch fit");
    expect(
      [...container.querySelectorAll("button")].filter(
        (button) => button.textContent?.includes("Mark as Applied"),
      ),
    ).toHaveLength(1);
    expect(
      [...container.querySelectorAll("button")].filter(
        (button) => button.textContent?.includes("Save Job"),
      ),
    ).toHaveLength(1);
  });

  it("associates Suggested Apply with the existing control and preserves its mutation flow", async () => {
    const readiness = {
      decision: "apply" as const,
      reasons: ["Strong match score"],
      caveats: [],
      deterministic_blockers: [],
      deterministic_warnings: [],
      decision_basis: "score" as const,
    };
    apiMocks.matchesApi.skills.mockResolvedValue({
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python"],
      missing_keywords: [],
      readiness,
    });
    queryClient.setQueryData(["match-skills", 501], {
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python"],
      missing_keywords: [],
      readiness,
    });
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const navigation = [...container.querySelectorAll("button")].find(
      (button) => button.getAttribute("aria-label") === "Go to apply action",
    );
    const applyControl = document.getElementById("crm-mark-applied-action");
    expect(navigation).toBeDefined();
    expect(applyControl?.getAttribute("aria-describedby")).toBe(
      "suggested-apply-action-description",
    );
    expect(container.textContent).toContain("Mark as Applied updates your application status.");
    await act(async () => {
      navigation?.click();
    });

    expect(document.activeElement?.id).toBe("crm-mark-applied-action");
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(apiMocks.applicationsApi.create).not.toHaveBeenCalled();
    expect(apiMocks.applicationsApi.update).not.toHaveBeenCalled();

    apiMocks.applicationsApi.create.mockResolvedValue({ id: 700 });
    apiMocks.applicationsApi.update.mockResolvedValue({ id: 700, status: "applied" });
    await act(async () => {
      applyControl?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.applicationsApi.create).toHaveBeenCalledOnce();
    expect(apiMocks.applicationsApi.update).toHaveBeenCalledOnce();
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
  });

  it("does not associate Suggested Apply with an already-applied job", async () => {
    const readiness = {
      decision: "apply" as const,
      reasons: ["Strong match score"],
      caveats: [],
      deterministic_blockers: [],
      deterministic_warnings: [],
      decision_basis: "score" as const,
    };
    apiMocks.listAllApplications.mockResolvedValue([
      { id: 701, job_id: 501, status: "applied" },
    ]);
    queryClient.setQueryData(["applications", "list"], [
      { id: 701, job_id: 501, status: "applied" },
    ]);
    apiMocks.matchesApi.skills.mockResolvedValue({
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python"],
      missing_keywords: [],
      readiness,
    });
    queryClient.setQueryData(["match-skills", 501], {
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python"],
      missing_keywords: [],
      readiness,
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const applyControl = document.getElementById("crm-mark-applied-action");
    expect(applyControl?.textContent).toBe("Applied");
    expect(applyControl?.getAttribute("aria-describedby")).toBeNull();
    expect(container.textContent).not.toContain("Suggested next action: Apply.");
    expect(container.textContent).not.toContain("Go to action");
  });

  it("navigates Review to readiness explanation without a mutation", async () => {
    const readiness = {
      decision: "save" as const,
      reasons: ["Review needed"],
      caveats: [],
      deterministic_blockers: [],
      deterministic_warnings: ["Remote status is unclear"],
      decision_basis: "score" as const,
    };
    apiMocks.matchesApi.skills.mockResolvedValue({
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python"],
      missing_keywords: [],
      readiness,
    });
    queryClient.setQueryData(["match-skills", 501], {
      job_id: 501,
      vector_score: 87,
      matched_skills: ["python"],
      missing_keywords: [],
      readiness,
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const navigation = [...container.querySelectorAll("button")].find(
      (button) => button.getAttribute("aria-label") === "View readiness explanation",
    );
    expect(navigation).toBeDefined();
    await act(async () => {
      navigation?.click();
    });

    expect(document.activeElement?.id).toBe("readiness-explanation");
    expect(apiMocks.applicationsApi.create).not.toHaveBeenCalled();
    expect(apiMocks.applicationsApi.update).not.toHaveBeenCalled();
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
  });

  it("opens Archive job confirmation from More actions without a mutation", async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const moreActions = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("More actions"),
    );
    await act(async () => {
      moreActions?.dispatchEvent(
        new MouseEvent("pointerdown", { bubbles: true, button: 0 }),
      );
      await Promise.resolve();
    });

    const archiveMenuItems = [...document.querySelectorAll('[role="menuitem"]')].filter(
      (item) => item.textContent?.includes("Archive job"),
    );
    expect(archiveMenuItems).toHaveLength(1);
    await act(async () => {
      archiveMenuItems[0]?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(apiMocks.jobsApi.archive).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Archive this job?");
    expect(document.body.textContent).toContain(
      "This removes the job from your lists for you. It does not delete the job.",
    );
    expect(container.textContent).toContain("Suggested: Skip");
    expect([...container.querySelectorAll("button")].filter(
      (button) => button.textContent?.includes("Mark as Applied"),
    )).toHaveLength(1);
    expect([...container.querySelectorAll("button")].filter(
      (button) => button.textContent?.includes("Save Job"),
    )).toHaveLength(1);
    const cancel = [...document.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Keep job",
    );
    await act(async () => {
      cancel?.click();
      await Promise.resolve();
    });
    expect(apiMocks.jobsApi.archive).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("Archive this job?");
  });

  it("archives once after confirmation and invalidates only job-list caches", async () => {
    apiMocks.jobsApi.archive.mockResolvedValue(undefined);
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const moreActions = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("More actions"),
    );
    await act(async () => {
      moreActions?.dispatchEvent(
        new MouseEvent("pointerdown", { bubbles: true, button: 0 }),
      );
      await Promise.resolve();
      [...document.querySelectorAll('[role="menuitem"]')]
        .find((item) => item.textContent?.includes("Archive job"))
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const confirm = [...document.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Archive job",
    );
    await act(async () => {
      confirm?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.jobsApi.archive).toHaveBeenCalledTimes(1);
    expect(apiMocks.jobsApi.archive).toHaveBeenCalledWith(501);
    expect(routerMocks.push).toHaveBeenCalledWith("/dashboard/jobs");
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["job", 501] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["matches"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["jobs"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["imported-jobs"] });
    expect(invalidateQueries).not.toHaveBeenCalledWith({ queryKey: ["applications"] });
  });

  it("keeps the confirmation open after archive failure and preserves application copy", async () => {
    apiMocks.listAllApplications.mockResolvedValue([{ id: 702, job_id: 501, status: "saved" }]);
    queryClient.setQueryData(["applications", "list"], [
      { id: 702, job_id: 501, status: "saved" },
    ]);
    apiMocks.jobsApi.archive.mockRejectedValue(new apiMocks.ApiError("Archive failed", 500, null));
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const moreActions = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("More actions"),
    );
    await act(async () => {
      moreActions?.dispatchEvent(
        new MouseEvent("pointerdown", { bubbles: true, button: 0 }),
      );
      await Promise.resolve();
      [...document.querySelectorAll('[role="menuitem"]')]
        .find((item) => item.textContent?.includes("Archive job"))
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(document.body.textContent).toContain(
      "Your saved application will remain available in Applications.",
    );
    const confirm = [...document.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Archive job",
    );
    await act(async () => {
      confirm?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.jobsApi.archive).toHaveBeenCalledTimes(1);
    expect(routerMocks.push).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Archive this job?");
    expect(toastMocks.error).toHaveBeenCalledWith("Archive failed");
  });
});
