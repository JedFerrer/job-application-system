import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobCard, type JobCardData, type JobCardMatch } from "@/components/jobs/job-card";
import { JobDetailView } from "@/components/jobs/job-detail-view";

const apiMocks = vi.hoisted(() => {
  const jobsApi = {
    get: vi.fn(),
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
    expect(container.textContent).not.toContain("Stretch fit");
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
        caveats: [],
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
        caveats: [],
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
    });

    expect(container.textContent).toContain("Ready to apply");
    expect(container.textContent).toContain("Strong match score");
    expect(container.textContent).toContain("2 matched skills found");
    expect(container.textContent).toContain("Missingkubernetes");
    expect(container.textContent).not.toContain(
      "Detailed skill gaps are not available yet",
    );
    expect(container.textContent).toContain("Get Detailed AI Assessment");
    expect(container.textContent).not.toContain("Known gaps: kubernetes, graphql");
    expect(container.textContent).not.toContain("Stretch fit");
  });
});
