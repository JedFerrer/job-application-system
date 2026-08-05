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
    summary: vi.fn(),
    skills: vi.fn(),
    analyze: vi.fn(),
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

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

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

  it("separates score and readiness when readiness is present", async () => {
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
      scoreLabel: "Relevance",
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
          compactMatchLayout
          hideExternalLink
          showConfidenceLabel={false}
          onCardClick={() => undefined}
        />,
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Ready to apply");
    expect(container.textContent).not.toContain("Suggested: Apply");
    expect(container.textContent).not.toContain("Stretch fit");
    expect(container.querySelector('[data-testid="match-readiness-summary"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="match-readiness-summary"]')?.textContent).toContain(
      "Ready to apply",
    );
    expect(container.textContent).not.toContain("Match score");
    expect(container.textContent).toContain("Relevance");
    expect(container.querySelector('[data-testid="match-why-recommended"]')?.textContent).toBe(
      "Why recommended: Python, FastAPI",
    );
    expect(container.querySelector('[data-testid="match-readiness-summary"]')?.parentElement?.className).toContain(
      "border",
    );
    expect(container.firstElementChild?.className).toContain("hover:border-primary/50");
    expect(container.querySelector("p.line-clamp-2")).not.toBeNull();
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
            compactMatchLayout
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
    expect(container.textContent).not.toContain("Suggested: Skip");
    expect(container.textContent).not.toContain("Likely skip");

    await renderMatch({
      decision: "save",
      reasons: ["Review needed"],
      caveats: [],
      deterministic_blockers: [],
      deterministic_warnings: ["Remote status is unclear"],
      decision_basis: "score",
    });
    expect(container.textContent).toContain("Needs review");
    expect(container.textContent).not.toContain("Suggested: Review");
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

  const makeSummary = (
    jobId: number,
    marker: string,
    vectorScore = 87,
  ) => ({
    job_id: jobId,
    vector_score: vectorScore,
    confidence: "moderate",
    llm_score: null,
    matched_skills: [`${marker}-matched`],
    missing_keywords: [`${marker}-missing`],
    readiness: {
      decision: "skip" as const,
      reasons: [`${marker} reason`],
      caveats: [],
      deterministic_blockers: [`${marker} blocker`],
      deterministic_warnings: [],
      decision_basis: "blocker" as const,
    },
  });

  const renderJob = async (jobId: number) => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={jobId} />
        </QueryClientProvider>,
      );
      for (let index = 0; index < 4; index += 1) {
        await Promise.resolve();
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    });
  };

  const waitForCondition = async (condition: () => boolean) => {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (condition()) return;
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5));
      });
    }
    expect(condition()).toBe(true);
  };

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
    apiMocks.matchesApi.summary.mockReset();
    apiMocks.matchesApi.skills.mockReset();
    apiMocks.matchesApi.analyze.mockReset();
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
    apiMocks.matchesApi.summary.mockResolvedValue({
      job_id: 501,
      vector_score: 87,
      confidence: "moderate",
      llm_score: null,
      explanation: null,
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

    expect(container.textContent).not.toContain("Ready to apply");
    expect(container.textContent).not.toContain("Suggested next action");
    expect(container.textContent).toContain("Application recommendation");
    expect(container.textContent).toContain("Based on your preferences and readiness rules");
    expect(container.textContent).toContain("Skip");
    expect(container.textContent).not.toContain("Go to action");
    expect(container.textContent).not.toContain("View action");
    expect(container.textContent).not.toContain("Strong match score");
    expect(container.textContent).toContain("Why we recommend skipping");
    expect(container.textContent).toContain("Blocked keyword: contract");
    expect(container.textContent).toContain("What to review");
    expect(container.textContent).toContain("Salary data needs review");
    expect(container.textContent).toContain("Matched skills (2)PythonFastAPI");
    expect(container.textContent).toContain("Skill gaps (1)Kubernetes");
    expect(container.textContent).not.toContain("matched skills found");
    expect(container.textContent).not.toContain("Moderate match score");
    expect(container.textContent).toContain(
      "Detailed skill gaps are not available yet",
    );
    expect(container.textContent).toContain("Get AI fit review");
    expect(container.textContent).not.toContain("Known gaps: kubernetes, graphql");
    expect(container.textContent).not.toContain("Stretch fit");
    const scoreSummary = container.querySelector('[data-testid="match-score-summary"]');
    const readinessExplanation = container.querySelector("#readiness-explanation");
    const detailedAssessment = container.querySelector('[data-testid="match-detailed-assessment"]');
    const recommendationBadge = [...(readinessExplanation?.querySelectorAll("[class]") ?? [])].find(
      (node) => node.textContent === "Skip" && node.className.includes("rounded-full"),
    );
    expect(scoreSummary?.textContent).not.toContain("Skip");
    expect(recommendationBadge?.className).toContain("text-red-700");
    expect(scoreSummary?.parentElement?.className).toContain("flex-col");
    expect(readinessExplanation?.parentElement).toBe(scoreSummary?.parentElement);
    expect(detailedAssessment?.parentElement).toBe(scoreSummary?.parentElement);
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

  it("shows the More jobs card only when the job includes an employer summary", async () => {
    const jobWithEmployerSummary = {
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
      employer_summary: {
        company: "Acme Corp",
        company_logo: null,
        job_count: 4,
      },
    };
    apiMocks.jobsApi.get.mockResolvedValue(jobWithEmployerSummary);
    queryClient.setQueryData(["job", 501], jobWithEmployerSummary);

    await renderJob(501);

    expect(container.textContent).toContain("More jobs");
    expect(container.textContent).toContain("4 jobs in your catalog");
    const companyLink = [...container.querySelectorAll("a")].find(
      (link) => link.textContent?.includes("Acme Corp"),
    );
    expect(companyLink?.getAttribute("href")).toBe("/dashboard/jobs?company_exact=Acme%20Corp");
    expect(container.textContent).toContain("AC");
  });

  it("does not show the More jobs card without an employer summary", async () => {
    await renderJob(501);

    expect(container.textContent).not.toContain("More jobs");
  });

  it("renders the employer logo when the summary supplies one", async () => {
    const jobWithEmployerLogo = {
      ...(queryClient.getQueryData<Record<string, unknown>>(["job", 501]) ?? {}),
      employer_summary: {
        company: "Acme Corp",
        company_logo: "https://cdn.example.com/acme-logo.png",
        job_count: 4,
      },
    };
    apiMocks.jobsApi.get.mockResolvedValue(jobWithEmployerLogo);
    queryClient.setQueryData(["job", 501], jobWithEmployerLogo);

    await renderJob(501);

    const logo = container.querySelector('img[src="https://cdn.example.com/acme-logo.png"]');
    expect(logo).not.toBeNull();
  });

  it("hydrates persisted Quick Match data from the summary API on a fresh load", async () => {
    queryClient.removeQueries({ queryKey: ["matches"] });
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });
    queryClient.removeQueries({ queryKey: ["match-skills", 501] });
    apiMocks.matchesApi.summary.mockResolvedValue(makeSummary(501, "fresh-api"));

    await renderJob(501);
    await waitForCondition(() => container.textContent?.includes("fresh-api blocker") ?? false);

    expect(apiMocks.matchesApi.summary).toHaveBeenCalledOnce();
    expect(apiMocks.matchesApi.summary).toHaveBeenCalledWith(501);
    expect(container.textContent).toContain("fresh-api blocker");
    expect(container.textContent).toContain("Matched skills (1)Fresh Api Matched");
    expect(container.textContent).toContain("Skill gaps (1)Fresh Api Missing");
  });

  it("keeps profile relevance separate from a saved AI fit review", async () => {
    apiMocks.matchesApi.summary.mockResolvedValue({
      ...makeSummary(501, "ai-review", 87),
      llm_score: 96,
      llm_confidence: 0.9,
      explanation: "Your backend experience and Python skills align well with this role.",
    });

    await renderJob(501);
    await waitForCondition(
      () => container.querySelector('[data-testid="ai-fit-review"]') !== null,
    );

    expect(container.textContent).toContain("Profile relevance");
    expect(container.querySelector('[data-testid="match-score-summary"]')?.textContent).toContain("87%");
    expect(container.querySelector('[data-testid="ai-fit-review"]')?.textContent).toContain("AI fit review");
    expect(container.querySelector('[data-testid="ai-fit-review"]')?.textContent).toContain("Strong skills fit");
    expect(container.querySelector('[data-testid="ai-fit-review"]')?.textContent).toContain("Skills and experience only");
    expect(container.querySelector('[data-testid="ai-fit-review"]')?.textContent).toContain(
      "Your backend experience and Python skills align well with this role.",
    );
    expect(container.querySelector('[data-testid="ai-fit-review"]')?.textContent).toContain("Matched skills (1)");
    expect(container.querySelector('[data-testid="ai-fit-review"]')?.textContent).toContain("Skill gaps (1)");
    expect(container.textContent).not.toContain("Detailed Match");
    expect(container.textContent).not.toContain("Quick:");
    expect(container.querySelector('[data-testid="match-detailed-assessment"]')).toBeNull();
    expect(container.textContent).not.toContain("Includes a structured skills-gap review");
    expect(container.querySelector('[role="tablist"]')).toBeNull();
    expect(container.querySelector('[id="job-detail-tab-skills"]')).toBeNull();
    expect(container.querySelector('[id="job-detail-tab-notes"]')).toBeNull();
  });

  it("keeps cached match data visible while the persisted summary is pending", async () => {
    const pendingSummary = deferred<ReturnType<typeof makeSummary> | null>();
    apiMocks.matchesApi.summary.mockReturnValue(pendingSummary.promise);
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });

    await renderJob(501);

    expect(apiMocks.matchesApi.summary).toHaveBeenCalledWith(501);
    expect(container.textContent).toContain("Blocked keyword: contract");
    expect(container.textContent).not.toContain("No saved match analysis for this job yet.");

    await act(async () => {
      pendingSummary.resolve(makeSummary(501, "pending-complete"));
      await pendingSummary.promise;
      await Promise.resolve();
    });
  });

  it("replaces cached match and skill data with a successful persisted summary", async () => {
    const pendingSummary = deferred<ReturnType<typeof makeSummary> | null>();
    apiMocks.matchesApi.summary.mockReturnValue(pendingSummary.promise);
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });

    await renderJob(501);
    expect(container.textContent).toContain("Blocked keyword: contract");

    await act(async () => {
      pendingSummary.resolve(makeSummary(501, "persisted", 64));
      await pendingSummary.promise;
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("persisted blocker");
    expect(container.textContent).toContain("Matched skills (1)Persisted Matched");
    expect(container.textContent).toContain("Skill gaps (1)Persisted Missing");
    expect(container.textContent).not.toContain("Blocked keyword: contract");
    expect(container.textContent).not.toContain("Missingkubernetes");
  });

  it("treats a successful null summary as authoritative and clears cached match data", async () => {
    const pendingSummary = deferred<ReturnType<typeof makeSummary> | null>();
    apiMocks.matchesApi.summary.mockReturnValue(pendingSummary.promise);
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });

    await renderJob(501);
    expect(container.textContent).toContain("Blocked keyword: contract");

    await act(async () => {
      pendingSummary.resolve(null);
      await pendingSummary.promise;
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("No saved match analysis for this job yet.");
    expect(container.textContent).toContain(
      "Run Match Analysis to compare it with your active profile.",
    );
    expect(container.textContent).not.toContain("Blocked keyword: contract");
  });

  it("keeps cached data provisionally when summary refresh fails and offers retry", async () => {
    apiMocks.matchesApi.summary.mockRejectedValue(new Error("summary unavailable"));
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });

    await renderJob(501);
    await waitForCondition(
      () => container.textContent?.includes("Could not refresh saved match analysis.") ?? false,
    );

    expect(container.textContent).toContain("Blocked keyword: contract");
    expect(container.textContent).toContain("Could not refresh saved match analysis.");
    expect(container.textContent).toContain("Retry");
    expect(container.textContent).not.toContain("No saved match analysis for this job yet.");
  });

  it("shows an error without claiming no saved match when no cache is available", async () => {
    queryClient.removeQueries({ queryKey: ["matches"] });
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });
    queryClient.removeQueries({ queryKey: ["match-skills", 501] });
    apiMocks.matchesApi.summary.mockRejectedValue(new Error("summary unavailable"));

    await renderJob(501);
    await waitForCondition(
      () => container.textContent?.includes("Could not load your saved match analysis.") ?? false,
    );

    expect(container.textContent).toContain("Could not load your saved match analysis.");
    expect(container.textContent).toContain("Retry loading match");
    expect(container.textContent).not.toContain("No saved match analysis for this job yet.");
  });

  it("surfaces a summary failure immediately despite the app retry default", async () => {
    queryClient.setDefaultOptions({
      queries: {
        retry: 1,
        retryDelay: 1_000,
      },
    });
    queryClient.removeQueries({ queryKey: ["matches"] });
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });
    queryClient.removeQueries({ queryKey: ["match-skills", 501] });
    apiMocks.matchesApi.summary.mockRejectedValue(new Error("summary unavailable"));

    await renderJob(501);
    await waitForCondition(
      () => container.textContent?.includes("Could not load your saved match analysis.") ?? false,
    );

    expect(apiMocks.matchesApi.summary).toHaveBeenCalledOnce();
    expect(container.textContent).toContain("Retry loading match");
    expect(container.textContent).not.toContain("Loading saved match analysis…");

    const retry = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Retry loading match",
    );
    await act(async () => {
      retry?.click();
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(apiMocks.matchesApi.summary).toHaveBeenCalledTimes(2);
  });

  it("never lets a late Job A explanation override Job B", async () => {
    const pendingExplanation = deferred<{
      job_id: number;
      vector_score: number;
      confidence: string;
      llm_score: number;
      llm_confidence: number;
      llm_available: boolean;
      llm_error: null;
      explanation: string;
      matched_skills: string[];
      missing_keywords: string[];
      readiness: ReturnType<typeof makeSummary>["readiness"];
    }>();
    apiMocks.matchesApi.explain.mockReturnValue(pendingExplanation.promise);
    apiMocks.matchesApi.summary.mockImplementation((requestedJobId: number) =>
      Promise.resolve(makeSummary(requestedJobId, `job-${requestedJobId}`)),
    );

    await renderJob(501);
    const assessmentButton = [...container.querySelectorAll("button")].find(
        (button) => button.textContent?.includes("Get AI fit review"),
    );
    await act(async () => {
      assessmentButton?.click();
      await Promise.resolve();
    });

    const jobA = queryClient.getQueryData<Record<string, unknown>>(["job", 501]);
    queryClient.setQueryData(["job", 502], {
      ...jobA,
      id: 502,
      title: "Job B",
      source_url: "https://example.com/job/502",
    });
    await renderJob(502);
    await waitForCondition(() => container.textContent?.includes("job-502 blocker") ?? false);
    expect(container.textContent).toContain("job-502 blocker");

    await act(async () => {
      pendingExplanation.resolve({
        job_id: 501,
        vector_score: 99,
        confidence: "strong",
        llm_score: 99,
        llm_confidence: 0.99,
        llm_available: true,
        llm_error: null,
        explanation: "JOB A EXPLANATION MUST NOT LEAK",
        matched_skills: ["job-a-only-skill"],
        missing_keywords: ["job-a-only-gap"],
        readiness: makeSummary(501, "job-a").readiness,
      });
      await pendingExplanation.promise;
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("job-502 blocker");
    expect(container.textContent).not.toContain("JOB A EXPLANATION MUST NOT LEAK");
    expect(container.textContent).not.toContain("job-a-only-skill");
    expect(container.textContent).not.toContain("job-a-only-gap");
  });

  it("analyzes and displays the current job without batch ranking", async () => {
    queryClient.removeQueries({ queryKey: ["matches"] });
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });
    queryClient.removeQueries({ queryKey: ["match-skills", 501] });
    apiMocks.matchesApi.summary
      .mockResolvedValueOnce(null);
    apiMocks.matchesApi.analyze.mockResolvedValue(makeSummary(501, "after-analyze"));
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

    await renderJob(501);
    await waitForCondition(
      () => container.textContent?.includes("No saved match analysis for this job yet.") ?? false,
    );
    expect(container.textContent).toContain("No saved match analysis for this job yet.");

    const analyzeButton = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Analyze Match",
    );
    await act(async () => {
      analyzeButton?.click();
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
      await Promise.resolve();
    });
    await waitForCondition(
      () => container.textContent?.includes("after-analyze blocker") ?? false,
    );

    expect(apiMocks.matchesApi.analyze).toHaveBeenCalledWith(501);
    expect(apiMocks.matchesApi.rank).not.toHaveBeenCalled();
    expect(apiMocks.matchesApi.summary).toHaveBeenCalledOnce();
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["matches"] });
    expect(container.textContent).toContain("after-analyze blocker");
  });

  it("retries a preparing analysis once before showing the saved summary", async () => {
    queryClient.removeQueries({ queryKey: ["match-summary", 501] });
    apiMocks.matchesApi.summary.mockResolvedValueOnce(null);
    apiMocks.matchesApi.analyze
      .mockRejectedValueOnce(new apiMocks.ApiError("Preparing this job", 409, null))
      .mockResolvedValueOnce(makeSummary(501, "prepared-match"));

    await renderJob(501);
    await waitForCondition(
      () => container.textContent?.includes("No saved match analysis for this job yet.") ?? false,
    );
    const analyzeButton = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Analyze Match",
    );
    analyzeButton?.click();

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1_050));
    });
    await waitForCondition(() => apiMocks.matchesApi.analyze.mock.calls.length === 2);
    await waitForCondition(
      () => container.textContent?.includes("prepared-match blocker") ?? false,
    );

    expect(apiMocks.matchesApi.analyze).toHaveBeenCalledTimes(2);
    expect(apiMocks.matchesApi.rank).not.toHaveBeenCalled();
  });

  it("keeps the existing Apply control singular and preserves its mutation flow", async () => {
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
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const applyControl = document.getElementById("crm-mark-applied-action");
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    const navigation = [...container.querySelectorAll("button")].find(
      (button) => button.getAttribute("aria-label") === "Go to apply action",
    );
    expect(applyControl).toBeDefined();
    expect(navigation).toBeDefined();
    expect(applyControl?.getAttribute("aria-describedby")).toBeNull();
    expect(container.textContent).not.toContain("Suggested next action");
    expect(container.textContent).toContain("Application recommendation");
    expect(container.textContent).toContain("Apply");
    expect([...container.querySelectorAll("button")].filter(
      (button) => button.textContent?.includes("Mark as Applied"),
    )).toHaveLength(1);
    await act(async () => {
      navigation?.click();
    });
    expect(document.activeElement?.id).toBe("crm-mark-applied-action");
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(applyControl?.className).toContain("ring-2");
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

  it("shows warning details without presenting a second suggested action", async () => {
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
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <JobDetailView jobId={501} />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("What to review");
    expect(container.textContent).toContain("Remote status is unclear");
    expect(container.textContent).not.toContain("Review details");
    expect(container.textContent).not.toContain("Suggested next action");
    expect(container.textContent).toContain("Application recommendation");
    expect(container.textContent).toContain("Review");
    expect([...container.querySelectorAll("button")].some(
      (button) => button.getAttribute("aria-label") === "Review recommendation details",
    )).toBe(false);
    expect(apiMocks.applicationsApi.create).not.toHaveBeenCalled();
    expect(apiMocks.applicationsApi.update).not.toHaveBeenCalled();
  });

  it("archives immediately from More actions", async () => {
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

    expect(apiMocks.jobsApi.archive).toHaveBeenCalledWith(501);
    expect(document.body.textContent).not.toContain("Archive this job?");
    expect(container.textContent).toContain("Application recommendation");
    expect(container.textContent).toContain("Skip");
    expect([...container.querySelectorAll("button")].filter(
      (button) => button.textContent?.includes("Mark as Applied"),
    )).toHaveLength(1);
    expect([...container.querySelectorAll("button")].filter(
      (button) => button.textContent?.includes("Save Job"),
    )).toHaveLength(1);
  });

  it("archives once and invalidates only job-list caches", async () => {
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

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(apiMocks.jobsApi.archive).toHaveBeenCalledTimes(1);
    expect(apiMocks.jobsApi.archive).toHaveBeenCalledWith(501);
    expect(routerMocks.push).toHaveBeenCalledWith("/dashboard/jobs");
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["job", 501] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["matches"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["jobs"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["imported-jobs"] });
    expect(invalidateQueries).not.toHaveBeenCalledWith({ queryKey: ["applications"] });
  });

  it("reports archive failure and preserves application copy", async () => {
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

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(apiMocks.jobsApi.archive).toHaveBeenCalledTimes(1);
    expect(routerMocks.push).not.toHaveBeenCalled();
    expect(toastMocks.error).toHaveBeenCalledWith("Archive failed");
  });
});
