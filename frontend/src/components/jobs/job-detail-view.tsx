"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { InfiniteData } from "@tanstack/react-query";
import {
  ArrowLeft,
  Briefcase,
  ExternalLink,
  Loader2,
  MapPin,
  Bookmark,
  CheckCircle,
  Calendar,
  Building2,
  FileText,
  MessagesSquare,
  FilePenLine,
  EllipsisVertical,
  ChevronDown,
  Archive,
  ArrowRight,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi, listAllApplications, matchesApi } from "@/lib/api";
import type {
  ApplicationListItem,
  EmployerSummary,
  MatchExplanation,
  MatchItem,
  MatchListResponse,
  MatchSkillAnalysis,
  MatchSummary,
} from "@/lib/types";
import { useEffect, useState } from "react";
import { formatSalary, formatRelativeDate } from "@/lib/format";
import { getAiFitLabel, getQuickMatchConfidence } from "@/lib/match-display";
import { formatJobDescription, stripMarkdown } from "@/lib/utils";
import { formatSkillLabel } from "@/lib/skill-display";
import {
  getReadinessActionNavigationTarget,
  getReadinessActionSuggestion,
  type ReadinessActionNavigationTarget,
  type SuggestedReadinessAction,
} from "@/lib/readiness-actions";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";
import { InterviewPrepDialog } from "@/components/interview/interview-prep-dialog";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { CircularScore, SourceIcon } from "@/components/jobs/job-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SOURCE_LABELS: Record<string, string> = {
  indeed: "Indeed",
  linkedin: "LinkedIn",
  glassdoor: "Glassdoor",
  google: "Google Jobs",
  zip_recruiter: "ZipRecruiter",
  manual: "Manual",
  extension: "Extension",
};

const APPLICATION_STATUS_LABELS: Record<string, string> = {
  saved: "Saved",
  applied: "Applied",
  interview: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  closed: "Closed",
};

const STATUS_BADGE_CLASS: Record<string, string> = {
  saved: "bg-muted text-muted-foreground",
  applied: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  interview: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
  offer: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  rejected: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
  closed: "bg-muted text-muted-foreground",
};

function employerInitials(company: string): string {
  const parts = company.trim().split(/\s+/);
  if (parts.length >= 2 && parts[0] && parts[1]) {
    return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
  }
  return (company.charAt(0) || "?").toUpperCase();
}

function employerAvatarClass(company: string): string {
  const colors = [
    "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
    "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    "bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300",
    "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300",
  ];
  let hash = 0;
  for (let index = 0; index < company.length; index += 1) {
    hash = (hash * 31 + company.charCodeAt(index)) | 0;
  }
  return colors[Math.abs(hash) % colors.length]!;
}

function EmployerSummaryCard({ summary }: { summary: EmployerSummary }) {
  const [logoFailed, setLogoFailed] = useState(false);
  const initials = employerInitials(summary.company);
  const href = `/dashboard/jobs?company_exact=${encodeURIComponent(summary.company)}`;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">More jobs</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-3">
          {summary.company_logo && !logoFailed ? (
            <img
              src={summary.company_logo}
              alt=""
              className="h-10 w-10 shrink-0 rounded-md border border-border object-contain bg-background"
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <div
              aria-hidden="true"
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-sm font-semibold ${employerAvatarClass(summary.company)}`}
            >
              {initials}
            </div>
          )}
          <div className="min-w-0">
            <Link
              href={href}
              className="inline-flex max-w-full items-center gap-1 font-medium text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <span className="truncate">{summary.company}</span>
              <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </Link>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {summary.job_count} jobs in your catalog
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

const BASE_PILL = "rounded-full font-normal text-xs";

const RECOMMENDATION_BADGE_CLASS: Record<SuggestedReadinessAction, string> = {
  apply:
    "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300",
  review:
    "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300",
  skip:
    "border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300",
};

function renderDescription(description: string | null | undefined) {
  const lines = formatJobDescription(description).split("\n");

  return lines.map((line, index) => {
    const trimmed = line.trim();
    if (!trimmed) return <div key={`spacer-${index}`} className="h-2" aria-hidden="true" />;

    const isBullet = trimmed.startsWith("•");
    const headingText = isBullet ? trimmed.slice(1).trim() : trimmed;
    const isHeading =
      !isBullet &&
      headingText.length <= 80 &&
      (/^[A-Z][A-Z\s&/-]+:?$/.test(headingText) ||
        /^(?:Core Objectives|Key Responsibilities|Technical Skills(?: & Tools)?|Behavioral Competencies|Backend Ecosystem|Frontend Ecosystem|Databases? & Caching|Test Automation & Quality|DevOps, Security & CI\/CD|Observability & Triage)$/i.test(headingText));

    if (isHeading) {
      return (
        <h3 key={`heading-${index}`} className="mt-7 text-base font-semibold tracking-tight first:mt-0">
          {headingText.replace(/:\s*$/, "").toLowerCase().replace(/\b\w/g, (character) => character.toUpperCase())}
        </h3>
      );
    }

    if (isBullet) {
      return (
        <p key={`bullet-${index}`} className="pl-4 [text-indent:-0.75rem]">
          • {headingText}
        </p>
      );
    }

    return <p key={`paragraph-${index}`} className="max-w-4xl">{trimmed.replace(/^:\s*/, "")}</p>;
  });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function JobDetailView({ jobId }: { jobId: number }) {
  const router = useRouter();
  const queryClient = useQueryClient();

  // ---- Data fetching -------------------------------------------------------
  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => jobsApi.get(jobId),
    staleTime: 60_000,
  });

  const [explainData, setExplainData] = useState<MatchExplanation | null>(null);
  const [showCoverLetter, setShowCoverLetter] = useState(false);
  const [showInterviewPrep, setShowInterviewPrep] = useState(false);
  const [showTailorResume, setShowTailorResume] = useState(false);
  const [highlightedActionTarget, setHighlightedActionTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!highlightedActionTarget) return;
    const timeout = window.setTimeout(() => setHighlightedActionTarget(null), 1600);
    return () => window.clearTimeout(timeout);
  }, [highlightedActionTarget]);
  useEffect(() => {
    setExplainData(null);
  }, [jobId]);

  // The list cache gives immediate continuity when arriving from /matches.
  // It is only a fast path; persisted data below restores this view after refresh.
  const cachedMatchSummary = ((): MatchSummary | null => {
    // The matches page uses useInfiniteQuery with a query key that includes
    // filter values, e.g. ["matches", "", "", "", ...].  Use getQueriesData
    // with prefix matching to find all cached match pages regardless of which
    // filters were active.
    const queries = queryClient.getQueriesData<InfiniteData<MatchListResponse>>({
      queryKey: ["matches"],
    });
    for (const [, data] of queries) {
      if (!data?.pages) continue;
      for (const page of data.pages) {
        const cached = page.items?.find((m: MatchItem) => m.job_id === jobId);
        if (!cached) continue;
        return {
          job_id: cached.job_id,
          vector_score: cached.vector_score,
          confidence: cached.confidence,
          llm_score: cached.llm_score,
          llm_confidence: cached.llm_confidence,
          explanation: null,
          matched_skills: cached.matched_skills ?? [],
          missing_keywords: [],
          readiness: cached.readiness,
        };
      }
    }
    return null;
  })();

  const persistedMatchQuery = useQuery({
    queryKey: ["match-summary", jobId],
    queryFn: () => matchesApi.summary(jobId),
    enabled: jobQuery.isSuccess,
    // A saved summary is read-only. Surface a real server failure immediately
    // so the user can retry explicitly instead of remaining in a loading state.
    retry: false,
    staleTime: 5 * 60_000,
  });

  // An explanation request may finish after navigation. Never let a response
  // for the previous job participate in even one render of the current job.
  const currentExplainData = explainData?.job_id === jobId ? explainData : null;
  const matchSummary: MatchExplanation | MatchSummary | null = currentExplainData ?? (
    persistedMatchQuery.isSuccess
      ? persistedMatchQuery.data
      : cachedMatchSummary
  );
  const summaryLoadError = persistedMatchQuery.isError;

  const skillQuery = useQuery({
    queryKey: ["match-skills", jobId],
    queryFn: () => matchesApi.skills(jobId),
    enabled: Boolean(cachedMatchSummary),
    staleTime: 5 * 60_000,
  });
  const skillData = skillQuery.data ?? null;

  const explainMutation = useMutation({
    mutationFn: () => matchesApi.explain(jobId),
    onSuccess: (data) => {
      setExplainData(data);
      queryClient.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Explain failed"),
  });

  // Uses listAllApplications to avoid the first-50 limitation.
  const appsQuery = useQuery({
    queryKey: ["applications", "list"],
    queryFn: listAllApplications,
    staleTime: 30_000,
  });

  const existingApp: ApplicationListItem | undefined = appsQuery.data?.find(
    (a) => a.job_id === jobId,
  );

  // ---- Mutations -----------------------------------------------------------
  const saveMutation = useMutation({
    mutationFn: () => applicationsApi.create(jobId),
    onSuccess: (application) => {
      if (application.status === "saved") toast.success("Saved to applications");
      else toast.error(`Job is already tracked as ${application.status}`);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-recommendations"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Save failed"),
  });

  const unsaveMutation = useMutation({
    mutationFn: async () => {
      if (existingApp) {
        await applicationsApi.delete(existingApp.id);
      }
    },
    onSuccess: () => {
      toast.success("Removed from saved");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-recommendations"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Unsave failed"),
  });

  const applyMutation = useMutation({
    mutationFn: async () => {
      if (existingApp) {
        await applicationsApi.update(existingApp.id, { status: "applied" });
      } else {
        const app = await applicationsApi.create(jobId);
        await applicationsApi.update(app.id, { status: "applied" });
      }
    },
    onSuccess: () => {
      toast.success("Marked as applied");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-recommendations"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to update status"),
  });

  const archiveMutation = useMutation({
    mutationFn: () => jobsApi.archive(jobId),
    onSuccess: () => {
      toast.success("Job archived");
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      queryClient.invalidateQueries({ queryKey: ["matches"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      // Imported-list membership comes from UserJob relationship metadata,
      // which is not included in the job-detail response.
      queryClient.invalidateQueries({ queryKey: ["imported-jobs"] });
      router.push("/dashboard/jobs");
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to archive job"),
  });

  const analyzeMatchMutation = useMutation({
    mutationFn: () => matchesApi.analyze(jobId),
    retry: (failureCount, error) =>
      error instanceof ApiError && error.status === 409 && failureCount < 3,
    retryDelay: 1_000,
    onSuccess: (summary) => {
      toast.success("Match analysis complete");
      setExplainData(null);
      queryClient.setQueryData(["match-summary", jobId], summary);
      queryClient.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Analysis failed"),
  });

  // Derived values — must be computed BEFORE any early returns so hooks
  // stay in a consistent order.
  // ---- Loading state -------------------------------------------------------
  if (jobQuery.isLoading) {
    return <DetailSkeleton />;
  }

  // ---- Error state ---------------------------------------------------------
  if (jobQuery.isError) {
    const is404 = jobQuery.error instanceof ApiError && jobQuery.error.status === 404;
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4">
        <Briefcase className="h-16 w-16 text-muted-foreground/40" />
        <div className="text-center">
          <h2 className="text-xl font-semibold">
            {is404 ? "Job not found" : "Failed to load job"}
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {is404
              ? "This job may have been removed or the link is invalid."
              : jobQuery.error instanceof ApiError
                ? jobQuery.error.message
                : "An unexpected error occurred."}
          </p>
        </div>
        <div className="flex gap-3">
          {!is404 && (
            <Button variant="outline" onClick={() => jobQuery.refetch()}>
              Try again
            </Button>
          )}
          <Button variant="outline" onClick={() => router.push("/dashboard/jobs")}>
            <ArrowLeft className="h-4 w-4 mr-1.5" />
            Back to Jobs
          </Button>
        </div>
      </div>
    );
  }

  const job = jobQuery.data!;
  const match = matchSummary;
  const isSaved = existingApp?.status === "saved";
  const canSave = !existingApp || existingApp.status === "closed";
  const isApplied = existingApp?.status === "applied" || existingApp?.status === "interview" || existingApp?.status === "offer";

  const postedLabel = job.posted_at ? formatRelativeDate(job.posted_at) : null;
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const profileRelevanceScore = match?.vector_score ?? 0;
  const profileRelevanceConfidence = match
    ? getQuickMatchConfidence(match.vector_score)
    : null;
  const rawAiFitLabel = getAiFitLabel(match?.llm_confidence ?? currentExplainData?.llm_confidence);
  const aiFitLabel = rawAiFitLabel?.endsWith(" fit")
    ? rawAiFitLabel.replace(/ fit$/, " skills fit")
    : rawAiFitLabel;
  const aiReviewExplanation = match?.explanation ?? currentExplainData?.explanation ?? null;
  const hasAiFitReview = match?.llm_score != null || currentExplainData?.llm_score != null;
  const readiness = currentExplainData?.readiness ?? (
    persistedMatchQuery.isSuccess
      ? match?.readiness
      : skillData?.readiness ?? match?.readiness
  ) ?? null;
  const readinessSuggestion = getReadinessActionSuggestion(readiness);
  const readinessNavigationTarget = getReadinessActionNavigationTarget(readinessSuggestion);
  const navigationTargetIds: Record<ReadinessActionNavigationTarget, string> = {
    apply: "crm-mark-applied-action",
    save: "crm-save-action",
    archive: "crm-archive-action",
    readiness: "readiness-explanation",
  };
  const navigationTargetIsAvailable =
    readinessNavigationTarget === "apply"
      ? !isApplied && !applyMutation.isPending
      : readinessNavigationTarget === "save"
        ? canSave && !saveMutation.isPending
        : false;
  const navigateToReadinessAction = () => {
    if (!readinessNavigationTarget || !navigationTargetIsAvailable) return;
    const target = document.getElementById(navigationTargetIds[readinessNavigationTarget]);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
    setHighlightedActionTarget(navigationTargetIds[readinessNavigationTarget]);
  };
  const analyzedMatchedSkills = currentExplainData?.matched_skills ?? (
    persistedMatchQuery.isSuccess
      ? match?.matched_skills
      : skillData?.matched_skills ?? match?.matched_skills
  ) ?? [];
  const analyzedMissingSkills = currentExplainData?.missing_keywords ?? (
    persistedMatchQuery.isSuccess
      ? match?.missing_keywords
      : skillData?.missing_keywords ?? match?.missing_keywords
  ) ?? [];
  return (
    <div className="flex flex-col gap-6 w-full">
      {/* ── Back button ─────────────────────────────────────── */}
      <button
        type="button"
        onClick={() => {
          if (window.history.length > 1) {
            router.back();
          } else {
            router.push("/dashboard/jobs");
          }
        }}
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors w-fit"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Jobs
      </button>

      {/* ── Two-column layout ────────────────────────────────── */}
      <div className="flex flex-col lg:grid lg:grid-cols-[1fr_320px] gap-8">
        {/* ================================================================ */}
        {/* LEFT COLUMN                                                       */}
        {/* ================================================================ */}
        <div className="min-w-0 flex flex-col gap-6">

          {/* ── Header ──────────────────────────────────────── */}
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{job.title}</h1>
            <div className="flex items-center gap-2 mt-1.5 text-muted-foreground">
              <Building2 className="h-4 w-4" />
              <span>{job.company ?? "Unknown company"}</span>
            </div>
          </div>

          {/* ── Metadata pills ───────────────────────────────── */}
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline" className={`${BASE_PILL} gap-1`}>
              <SourceIcon source={job.source} />
              {SOURCE_LABELS[job.source] ?? job.source}
            </Badge>
            {job.job_type && (
              <Badge
                variant="outline"
                className={`${BASE_PILL} capitalize border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-300`}
              >
                {job.job_type}
              </Badge>
            )}
            {job.is_remote && (
              <Badge
                variant="outline"
                className={`${BASE_PILL} border-indigo-200 dark:border-indigo-500/30 bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-300`}
              >
                Remote
              </Badge>
            )}
            {job.location && (
              <Badge
                variant="outline"
                className={`${BASE_PILL} border-slate-200 dark:border-slate-500/30 bg-slate-50 dark:bg-slate-500/10 text-slate-700 dark:text-slate-300`}
              >
                <MapPin className="h-3 w-3 mr-1" />
                {job.location}
              </Badge>
            )}
            {salary && (
              <Badge
                variant="outline"
                className={`${BASE_PILL} border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300`}
              >
                {salary}
              </Badge>
            )}
            {job.shift_type && (
              <Badge
                variant="outline"
                className={`${BASE_PILL} capitalize border-purple-200 dark:border-purple-500/30 bg-purple-50 dark:bg-purple-500/10 text-purple-700 dark:text-purple-300`}
              >
                {job.shift_type}
              </Badge>
            )}
            {postedLabel && (
              <Badge variant="secondary" className={BASE_PILL}>
                <Calendar className="h-3 w-3 mr-1" />
                {postedLabel}
              </Badge>
            )}
          </div>

          {/* ── Primary action row ───────────────────────────── */}
          <div className="flex items-center gap-3 flex-wrap">
            {job.source_url && (
              <Button variant="default" size="sm" asChild>
                <a href={job.source_url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="h-4 w-4 mr-1.5" />
                  Open Original Job
                </a>
              </Button>
            )}
            {/* Mark as Applied */}
            <Button
              id="crm-mark-applied-action"
              variant={isApplied ? "secondary" : "outline"}
              size="sm"
              onClick={() => applyMutation.mutate()}
              disabled={applyMutation.isPending || isApplied}
              title={isApplied ? "Application already marked as applied" : "Mark this job as applied"}
              className={
                highlightedActionTarget === "crm-mark-applied-action"
                  ? "ring-2 ring-ring ring-offset-2"
                  : undefined
              }
            >
              {applyMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
              ) : (
                <CheckCircle className="h-4 w-4 mr-1.5" />
              )}
              {isApplied ? "Applied" : "Mark as Applied"}
            </Button>

            {/* Save / Unsaved */}
            {isSaved && existingApp ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => unsaveMutation.mutate()}
                disabled={unsaveMutation.isPending}
                title="Remove this job from saved applications"
              >
                {unsaveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                ) : (
                  <CheckCircle className="h-4 w-4 mr-1.5 text-green-500" />
                )}
                Saved
              </Button>
            ) : canSave ? (
              <Button
                id="crm-save-action"
                variant="outline"
                size="sm"
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending}
                title="Save this job to applications"
              >
                {saveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                ) : (
                  <Bookmark className="h-4 w-4 mr-1.5" />
                )}
                Save Job
              </Button>
            ) : null}

            {/* More actions dropdown — push to the right */}
            <div className="ml-auto">
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="gap-1.5" title="Open more job actions">
                  <EllipsisVertical className="h-4 w-4" />
                  More actions
                  <ChevronDown className="h-3.5 w-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-48">
                <DropdownMenuItem onClick={() => setShowTailorResume(true)}>
                  <FilePenLine className="h-4 w-4" />
                  Tailor Resume
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setShowCoverLetter(true)}>
                  <FileText className="h-4 w-4" />
                  Generate Cover Letter
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setShowInterviewPrep(true)}>
                  <MessagesSquare className="h-4 w-4" />
                  Interview Prep
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => archiveMutation.mutate()}>
                  <Archive className="h-4 w-4" />
                  Archive job
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            </div>
          </div>

          {/* ── Overview card ────────────────────────────────── */}
          {/* ── Tabs: Description / Requirements / Skills / Notes */}
          <Card aria-label="Job description">
            <CardHeader className="border-b border-border pb-3">
              <CardTitle className="text-base">Job description</CardTitle>
            </CardHeader>
            <CardContent className="pt-5">
              <div className="max-w-4xl text-sm leading-7 wrap-break-word">
                {job.description ? (
                  renderDescription(job.description)
                ) : (
                  <p className="text-muted-foreground">No description available.</p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>{/* /left column */}

        {/* ================================================================ */}
        {/* RIGHT SIDEBAR                                                     */}
        {/* ================================================================ */}
        <div className="flex flex-col gap-6 lg:sticky lg:top-6 lg:self-start">

          {/* ── Match Score ──────────────────────────────────── */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">
                Profile relevance
              </CardTitle>
            </CardHeader>
            <CardContent>
              {match ? (
                <div className="flex flex-col gap-5">
                  <div
                    data-testid="match-score-summary"
                    className="flex items-start gap-5"
                  >
                    <div className="shrink-0">
                      <CircularScore
                        score={profileRelevanceScore}
                        confidence={profileRelevanceConfidence}
                        size="lg"
                        showConfidenceLabel={false}
                        label="Relevance"
                      />
                    </div>
                    <div className="min-w-0 pt-1">
                      <p className="text-[13px] leading-relaxed text-muted-foreground">
                        {profileRelevanceScore >= 80
                          ? "You're a strong match for this role."
                          : profileRelevanceScore >= 50
                            ? "You match many of the key requirements for this role."
                            : "This role may require additional skills beyond your profile."}
                      </p>
                      {currentExplainData?.llm_error && (
                        <p className="mt-3 text-[13px] leading-relaxed text-amber-600 dark:text-amber-400">
                          Detailed AI assessment unavailable; deterministic skill results are shown.
                        </p>
                      )}
                    </div>
                  </div>

                  {summaryLoadError && (
                    <div className="flex items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50/60 px-2 py-1.5 text-xs text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
                      <span>Could not refresh saved match analysis.</span>
                      <button
                        type="button"
                        onClick={() => persistedMatchQuery.refetch()}
                        className="shrink-0 underline underline-offset-2"
                      >
                        Retry
                      </button>
                    </div>
                  )}

                  {readiness && (
                    <section
                      id="readiness-explanation"
                      tabIndex={-1}
                      className={`${
                        highlightedActionTarget === "readiness-explanation"
                          ? "ring-2 ring-ring ring-offset-2"
                          : ""
                      }`}
                    >
                      {readinessSuggestion && (
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <h3 className="text-xs font-medium text-foreground">
                              Application recommendation
                            </h3>
                            <p className="mt-1 text-xs text-muted-foreground">
                              Based on your preferences and readiness rules
                            </p>
                          </div>
                          <div className="flex shrink-0 flex-col items-end gap-1.5">
                            <Badge
                              variant="outline"
                              className={`rounded-full text-xs font-medium ${RECOMMENDATION_BADGE_CLASS[readinessSuggestion.action]}`}
                            >
                              {readinessSuggestion.label}
                            </Badge>
                            {(readinessNavigationTarget === "apply" || readinessNavigationTarget === "save") && navigationTargetIsAvailable && (
                              <button
                                type="button"
                                onClick={navigateToReadinessAction}
                                className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                                aria-label={
                                  readinessNavigationTarget === "apply"
                                    ? "Go to apply action"
                                    : "Go to save action"
                                }
                              >
                                {readinessNavigationTarget === "apply"
                                  ? "Go to apply"
                                  : "Go to save"}
                              </button>
                            )}
                          </div>
                        </div>
                      )}

                      {(readiness.deterministic_blockers ?? []).length > 0 && (
                        <section className="mt-4 rounded-md border border-red-200 bg-red-50/60 px-2.5 py-2 dark:border-red-900/60 dark:bg-red-950/20">
                          <h3 className="text-xs font-medium text-red-700 dark:text-red-300">Why we recommend skipping</h3>
                          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[13px] leading-relaxed text-red-700 dark:text-red-300">
                            {(readiness.deterministic_blockers ?? []).map((blocker) => (
                              <li key={blocker}>{blocker}</li>
                            ))}
                          </ul>
                        </section>
                      )}

                      {([...((readiness.deterministic_warnings ?? [])), ...(readiness.caveats ?? [])].length > 0) && (
                        <section className="mt-4 rounded-md border border-amber-200 bg-amber-50/60 px-2.5 py-2 dark:border-amber-900/60 dark:bg-amber-950/20">
                          <h3 className="text-xs font-medium text-amber-700 dark:text-amber-300">What to review</h3>
                          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[13px] leading-relaxed text-amber-700 dark:text-amber-300">
                            {[...(readiness.deterministic_warnings ?? []), ...(readiness.caveats ?? [])].map((item, index) => (
                              <li key={`${item}-${index}`}>{item}</li>
                            ))}
                          </ul>
                        </section>
                      )}

                    </section>
                  )}

                  {hasAiFitReview && (
                    <section data-testid="ai-fit-review" className="space-y-3">
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <h3 className="text-xs font-medium text-foreground">AI fit review</h3>
                          <p className="mt-1 text-xs text-muted-foreground">Skills and experience only</p>
                        </div>
                        {aiFitLabel && (
                          <Badge variant="outline" className="rounded-full text-xs font-normal">
                            {aiFitLabel}
                          </Badge>
                        )}
                      </div>
                      {aiReviewExplanation && (
                        <p className="text-[13px] leading-relaxed text-muted-foreground">
                          {aiReviewExplanation}
                        </p>
                      )}
                      {!aiReviewExplanation && (
                        <p className="text-[13px] leading-relaxed text-muted-foreground">
                          Review the matched and missing requirements below.
                        </p>
                      )}
                      {(analyzedMatchedSkills.length > 0 || analyzedMissingSkills.length > 0) && (
                        <div className="flex flex-col gap-3 border-t border-border/60 pt-3">
                          <div>
                            <h4 className="text-xs font-medium text-muted-foreground">
                              Matched skills ({analyzedMatchedSkills.length})
                            </h4>
                            {analyzedMatchedSkills.length > 0 && (
                              <div className="mt-2 flex flex-wrap gap-1.5">
                                {analyzedMatchedSkills.map((skill) => (
                                  <Badge key={skill} variant="secondary" className="text-[13px] font-normal">{formatSkillLabel(skill)}</Badge>
                                ))}
                              </div>
                            )}
                          </div>
                          <div>
                            <h4 className="text-xs font-medium text-muted-foreground">
                              Skill gaps ({analyzedMissingSkills.length})
                            </h4>
                            {analyzedMissingSkills.length > 0 && (
                              <div className="mt-2 flex flex-wrap gap-1.5">
                                {analyzedMissingSkills.map((keyword) => (
                                  <Badge key={keyword} variant="outline" className="text-[13px] font-normal border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-300">{formatSkillLabel(keyword)}</Badge>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </section>
                  )}

                  {!hasAiFitReview && !currentExplainData && (
                    <div data-testid="match-detailed-assessment" className="flex flex-col gap-1">
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => explainMutation.mutate()}
                        disabled={explainMutation.isPending}
                      >
                        {explainMutation.isPending && (
                          <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                        )}
                        {match.llm_score != null ? "View AI fit review" : "Get AI fit review"}
                      </Button>
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        Includes a structured skills-gap review and AI fit explanation.
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3">
                  <p className="text-xs text-muted-foreground text-center leading-relaxed">
                    {summaryLoadError
                      ? "Could not load your saved match analysis."
                      : persistedMatchQuery.isSuccess
                        ? "No saved match analysis for this job yet."
                        : "Loading saved match analysis…"}
                  </p>
                  {summaryLoadError && (
                    <Button variant="outline" size="sm" onClick={() => persistedMatchQuery.refetch()}>
                      Retry loading match
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={() => analyzeMatchMutation.mutate()}
                    disabled={analyzeMatchMutation.isPending}
                  >
                    {analyzeMatchMutation.isPending && (
                      <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                    )}
                    {analyzeMatchMutation.isPending ? "Preparing Match…" : "Analyze Match"}
                  </Button>
                  {!summaryLoadError && persistedMatchQuery.isSuccess && (
                    <p className="text-center text-[11px] text-muted-foreground">
                      Run Match Analysis to compare it with your active profile.
                    </p>
                  )}
                </div>
              )}

              {/* Deterministic evidence before an AI review is available. */}
              {match && match.llm_score == null && (
                analyzedMatchedSkills.length > 0 ||
                analyzedMissingSkills.length > 0 ||
                Boolean(currentExplainData?.explanation)
              ) && (
                <div className="mt-4 flex flex-col gap-3">
                  <Separator />
                  <div className="w-full">
                    <h4 className="text-xs font-medium text-muted-foreground mb-1.5">
                      Matched skills ({analyzedMatchedSkills.length})
                    </h4>
                    {analyzedMatchedSkills.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                          {analyzedMatchedSkills.map((s) => (
                            <Badge key={s} variant="secondary" className="text-xs font-normal">{formatSkillLabel(s)}</Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="w-full">
                    <h4 className="text-xs font-medium text-muted-foreground mb-1.5">
                      Skill gaps ({analyzedMissingSkills.length})
                    </h4>
                    {analyzedMissingSkills.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {analyzedMissingSkills.map((kw) => (
                          <Badge key={kw} variant="outline" className="text-xs font-normal border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-300">{formatSkillLabel(kw)}</Badge>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* ── Quick Facts ──────────────────────────────────── */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Quick Facts</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2.5 text-sm">
              {job.job_type && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Employment</span>
                  <span className="font-medium capitalize">{job.job_type}</span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Source</span>
                <span className="font-medium">{SOURCE_LABELS[job.source] ?? job.source}</span>
              </div>
              {postedLabel && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Posted</span>
                  <span className="font-medium">{postedLabel}</span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Work Model</span>
                <span className="font-medium">{job.is_remote ? "Remote" : "On-site"}</span>
              </div>
              {job.location && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Location</span>
                  <span className="min-w-0 text-right font-medium break-words">{job.location}</span>
                </div>
              )}
              {salary && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Salary</span>
                  <span className="font-medium">{salary}</span>
                </div>
              )}
              {match?.matched_skills && match.matched_skills.length > 0 && (
                <div className="flex items-start justify-between gap-4">
                  <span className="text-muted-foreground text-sm">Tech Stack</span>
                  <span className="max-w-[65%] text-right font-medium text-sm leading-relaxed break-words">
                    {match.matched_skills.slice(0, 8).map(formatSkillLabel).join(", ")}
                  </span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* ── Application Status ───────────────────────────── */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-3 pb-3">
              <CardTitle className="text-sm font-medium">Application Status</CardTitle>
              {existingApp && (
                <Badge
                  variant="secondary"
                  className={`pointer-events-none text-xs font-normal w-fit ${STATUS_BADGE_CLASS[existingApp.status] ?? "bg-muted text-muted-foreground"}`}
                >
                  {APPLICATION_STATUS_LABELS[existingApp.status] ?? existingApp.status}
                </Badge>
              )}
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {existingApp ? (
                <>
                  {existingApp.status === "saved" && (
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      Customize your resume and cover letter before applying.
                    </p>
                  )}
                  {existingApp.status === "applied" && (
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      Prepare for potential interviews with the Interview Prep tool.
                    </p>
                  )}
                  {existingApp.status === "interview" && (
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      Great progress! Prepare thoroughly for your upcoming interview.
                    </p>
                  )}
                  {existingApp.status === "offer" && (
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      Congratulations! Review and respond to your offer.
                    </p>
                  )}
                </>
              ) : (
                <>
                  <span className="text-xs text-muted-foreground">Not yet saved</span>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    Save this job to track your application progress.
                  </p>
                </>
              )}
            </CardContent>
          </Card>
          {job.employer_summary && <EmployerSummaryCard summary={job.employer_summary} />}
        </div>{/* /right sidebar */}
      </div>{/* /two-column grid */}

      {/* ── Dialogs ─────────────────────────────────────────── */}
      <CoverLetterDialog
        jobId={jobId}
        jobTitle={job.title}
        open={showCoverLetter}
        onOpenChange={setShowCoverLetter}
      />
      <InterviewPrepDialog
        jobId={jobId}
        jobTitle={job.title}
        open={showInterviewPrep}
        onOpenChange={setShowInterviewPrep}
      />
      <TailorResumeDialog
        jobId={jobId}
        jobTitle={job.title}
        open={showTailorResume}
        onOpenChange={setShowTailorResume}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-6 w-full">
      <Skeleton className="h-5 w-24" />
      <div className="flex flex-col lg:grid lg:grid-cols-[1fr_320px] gap-8">
        {/* Left column */}
        <div className="flex flex-col gap-6 min-w-0">
          <div className="flex flex-col gap-2">
            <Skeleton className="h-8 w-3/4" />
            <Skeleton className="h-4 w-48" />
          </div>
          <div className="flex gap-2">
            <Skeleton className="h-6 w-20 rounded-full" />
            <Skeleton className="h-6 w-16 rounded-full" />
            <Skeleton className="h-6 w-24 rounded-full" />
            <Skeleton className="h-6 w-32 rounded-full" />
          </div>
          <div className="flex gap-3">
            <Skeleton className="h-9 w-32" />
            <Skeleton className="h-9 w-24" />
            <Skeleton className="h-9 w-28" />
          </div>
          <Card>
            <CardHeader className="pb-3">
              <Skeleton className="h-5 w-28" />
            </CardHeader>
            <CardContent className="pt-0">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full mt-2" />
              <Skeleton className="h-4 w-4/5 mt-2" />
              <Skeleton className="h-4 w-full mt-2" />
              <Skeleton className="h-4 w-3/4 mt-2" />
            </CardContent>
          </Card>
        </div>
        {/* Right sidebar */}
        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader className="pb-3">
              <Skeleton className="h-4 w-24" />
            </CardHeader>
            <CardContent className="flex flex-col items-center gap-3">
              <Skeleton className="h-18 w-18 rounded-full" />
              <Skeleton className="h-3 w-48" />
              <Skeleton className="h-8 w-full" />
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3">
              <Skeleton className="h-4 w-20" />
            </CardHeader>
            <CardContent className="flex flex-col gap-2.5">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3">
              <Skeleton className="h-4 w-28" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-6 w-16" />
              <Skeleton className="h-3 w-full mt-2" />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
