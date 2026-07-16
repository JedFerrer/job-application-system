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
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi, listAllApplications, matchesApi } from "@/lib/api";
import type {
  ApplicationListItem,
  MatchExplanation,
  MatchItem,
  MatchListResponse,
  MatchSkillAnalysis,
  MatchReadinessDecision,
} from "@/lib/types";
import { useMemo, useState } from "react";
import { formatSalary, formatRelativeDate } from "@/lib/format";
import { formatJobDescription } from "@/lib/utils";
import {
  getReadinessActionNavigationTarget,
  getReadinessActionSuggestion,
  type ReadinessActionNavigationTarget,
} from "@/lib/readiness-actions";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";
import { InterviewPrepDialog } from "@/components/interview/interview-prep-dialog";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { CircularScore } from "@/components/jobs/job-card";
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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

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

const BASE_PILL = "rounded-full font-normal text-xs";

const TABS = ["Description", "Skills", "Notes"] as const;
type Tab = (typeof TABS)[number];

const READINESS_LABELS: Record<MatchReadinessDecision, string> = {
  apply: "Ready to apply",
  save: "Save for review",
  stretch: "Stretch",
  skip: "Likely skip",
};

const READINESS_BADGE_CLASS: Record<MatchReadinessDecision, string> = {
  apply:
    "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300",
  save:
    "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300",
  stretch:
    "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300",
  skip:
    "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-500/30 dark:bg-slate-500/10 dark:text-slate-300",
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
      headingText === headingText.toUpperCase() &&
      /[A-Z]/.test(headingText);

    if (isHeading) {
      return (
        <h3 key={`heading-${index}`} className="mt-6 text-sm font-semibold tracking-wide first:mt-0">
          {headingText}
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

    return <p key={`paragraph-${index}`}>{trimmed}</p>;
  });
}

/** Derive a 1–2 sentence overview from available job data. */
function deriveOverview(job: { title?: string | null } | undefined, match: MatchExplanation | null): string {
  if (!job) return "";
  const role = job.title ?? "This role";
  const skills = match?.matched_skills?.slice(0, 5) ?? [];
  const skillText = skills.length > 0
    ? ` requiring ${skills.slice(0, -1).join(", ")}${skills.length > 1 ? " and " : ""}${skills[skills.length - 1]}`
    : "";
  return `${role} role${skillText}.`;
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
  const [activeTab, setActiveTab] = useState<Tab>("Description");
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false);

  // Derive match summary from the matches-list cache — no API call needed.
  // Falls back to the explain result if the user has already triggered it.
  // Computed on every render (cheap — getQueryData is O(1) cache lookup) so
  // it picks up background cache updates from e.g. analyzeMatchMutation.
  const matchSummary = ((): MatchExplanation | null => {
    if (explainData) return explainData;
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
          llm_confidence: null,
          llm_available: false,
          llm_error: null,
          explanation: null,
          matched_skills: cached.matched_skills ?? [],
          missing_keywords: [],
          readiness: cached.readiness,
        };
      }
    }
    return null;
  })();

  const skillQuery = useQuery({
    queryKey: ["match-skills", jobId],
    queryFn: () => matchesApi.skills(jobId),
    enabled: Boolean(matchSummary),
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
    onSuccess: () => {
      toast.success("Saved to applications");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Save failed"),
  });

  const unsaveMutation = useMutation({
    mutationFn: async () => {
      if (existingApp) {
        await applicationsApi.update(existingApp.id, { status: "closed" });
      }
    },
    onSuccess: () => {
      toast.success("Removed from saved");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
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
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to update status"),
  });

  const archiveMutation = useMutation({
    mutationFn: () => jobsApi.archive(jobId),
    onSuccess: () => {
      toast.success("Job archived");
      setShowArchiveConfirm(false);
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
    mutationFn: () => matchesApi.rank(100),
    onSuccess: () => {
      toast.success("Match analysis started");
      setExplainData(null);
      queryClient.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Analysis failed"),
  });

  // Derived values — must be computed BEFORE any early returns so hooks
  // stay in a consistent order.
  const derivedOverview = useMemo(
    () => (jobQuery.data ? deriveOverview(jobQuery.data, matchSummary) : ""),
    [jobQuery.data, matchSummary],
  );

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
  const isApplied = existingApp?.status === "applied" || existingApp?.status === "interview" || existingApp?.status === "offer";

  const postedLabel = job.posted_at ? formatRelativeDate(job.posted_at) : null;
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const matchScore = match ? (match.llm_score != null ? match.llm_score : match.vector_score) : 0;
  const quickMatchScore = match?.vector_score ?? null;
  const hasDetailedScore = explainData != null && match?.llm_score != null;
  const readiness = explainData?.readiness ?? skillData?.readiness ?? match?.readiness ?? null;
  const readinessSuggestion = getReadinessActionSuggestion(readiness);
  const hasApplySuggestionAssociation =
    readinessSuggestion?.action === "apply" &&
    !isApplied &&
    !applyMutation.isPending;
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
        ? !isSaved && !saveMutation.isPending
        : readinessNavigationTarget === "readiness";
  const navigateToSuggestedAction = () => {
    if (!readinessNavigationTarget || !navigationTargetIsAvailable) return;

    const target = document.getElementById(
      navigationTargetIds[readinessNavigationTarget],
    );
    if (!target) return;

    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
  };
  const analyzedMatchedSkills = explainData?.matched_skills ?? skillData?.matched_skills ?? match?.matched_skills ?? [];
  const analyzedMissingSkills = explainData?.missing_keywords ?? skillData?.missing_keywords ?? [];

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
            {job.job_type && (
              <Badge
                variant="outline"
                className={`${BASE_PILL} capitalize border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-300`}
              >
                {job.job_type}
              </Badge>
            )}
            <Badge variant="outline" className={BASE_PILL}>
              {SOURCE_LABELS[job.source] ?? job.source}
            </Badge>
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
              aria-describedby={
                hasApplySuggestionAssociation
                  ? "suggested-apply-action-description"
                  : undefined
              }
              variant={isApplied ? "secondary" : "outline"}
              size="sm"
              onClick={() => applyMutation.mutate()}
              disabled={applyMutation.isPending || isApplied}
              className={
                highlightedActionTarget === "crm-mark-applied-action"
                  ? "ring-2 ring-ring ring-offset-2"
                  : undefined
              }
            >
              {applyMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
              ) : isApplied ? (
                <CheckCircle className="h-4 w-4 mr-1.5" />
              ) : null}
              {isApplied ? "Applied" : "Mark as Applied"}
            </Button>

            {/* Save / Unsaved */}
            {isSaved && existingApp ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => unsaveMutation.mutate()}
                disabled={unsaveMutation.isPending}
              >
                {unsaveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                ) : (
                  <CheckCircle className="h-4 w-4 mr-1.5 text-green-500" />
                )}
                Saved
              </Button>
            ) : (
              <Button
                id="crm-save-action"
                variant="outline"
                size="sm"
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                ) : (
                  <Bookmark className="h-4 w-4 mr-1.5" />
                )}
                Save Job
              </Button>
            )}

            {/* More actions dropdown — push to the right */}
            <div className="ml-auto">
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="gap-1.5">
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
                <DropdownMenuItem onClick={() => setShowArchiveConfirm(true)}>
                  <Archive className="h-4 w-4" />
                  Archive job
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            </div>
          </div>

          {/* ── Overview card ────────────────────────────────── */}
          <Card>
            <CardContent className="pt-5">
              <p className="text-sm text-muted-foreground leading-relaxed">
                {derivedOverview || "No overview available for this job."}
              </p>
            </CardContent>
          </Card>

          {/* ── Tabs: Description / Requirements / Skills / Notes */}
          <Card>
            <CardHeader className="pb-0">
              <div role="tablist" aria-label="Job details" className="flex gap-1 border-b border-border">
                {TABS.map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={activeTab === tab}
                    aria-controls={`job-detail-tabpanel-${tab.toLowerCase()}`}
                    id={`job-detail-tab-${tab.toLowerCase()}`}
                    onClick={() => setActiveTab(tab)}
                    className={`px-4 py-2.5 text-sm font-medium transition-colors relative -mb-px ${
                      activeTab === tab
                        ? "text-foreground border-b-2 border-primary"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardContent className="pt-5">
              {activeTab === "Description" && (
                <div
                  role="tabpanel"
                  id="job-detail-tabpanel-description"
                  aria-labelledby="job-detail-tab-description"
                  className="text-sm leading-7 wrap-break-word"
                >
                  {job.description ? (
                    renderDescription(job.description)
                  ) : (
                    <p className="text-muted-foreground">No description available.</p>
                  )}
                </div>
              )}
              {activeTab === "Skills" && (
                <div role="tabpanel" id="job-detail-tabpanel-skills" aria-labelledby="job-detail-tab-skills">
                  {match?.matched_skills && match.matched_skills.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {match.matched_skills.map((skill) => (
                        <Badge key={skill} variant="secondary" className="text-sm font-normal">
                          {skill}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Run match analysis to identify relevant skills for this role.
                    </p>
                  )}
                  {!match && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-3"
                      onClick={() => analyzeMatchMutation.mutate()}
                      disabled={analyzeMatchMutation.isPending}
                    >
                      {analyzeMatchMutation.isPending && (
                        <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                      )}
                      Analyze Match
                    </Button>
                  )}
                </div>
              )}
              {activeTab === "Notes" && (
                <p
                  role="tabpanel"
                  id="job-detail-tabpanel-notes"
                  aria-labelledby="job-detail-tab-notes"
                  className="text-sm text-muted-foreground"
                >
                  Personal notes for this job will appear here.
                </p>
              )}
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
                {hasDetailedScore ? "Detailed Match" : "Quick Match"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {match ? (
                  <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-4 gap-y-4">
                    {/* Left: score ring + fit label */}
                    <div className="shrink-0">
                      <CircularScore
                        score={matchScore}
                        confidence={match.confidence}
                        size="lg"
                        showConfidenceLabel={false}
                      />
                      {hasDetailedScore && quickMatchScore != null && (
                        <p className="mt-1 text-center text-[11px] text-muted-foreground">
                          Quick: {Math.round(quickMatchScore)}%
                        </p>
                      )}
                    </div>
                  {/* Row 1, column 2: match summary and readiness */}
                  <div className="flex min-w-0 flex-col gap-2 pt-1">
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      {matchScore >= 80
                        ? "You're a strong match for this role."
                        : matchScore >= 50
                          ? "You match many of the key requirements for this role."
                          : "This role may require additional skills beyond your profile."}
                    </p>
                    {explainData?.llm_error && (
                      <p className="text-xs text-amber-600 dark:text-amber-400">
                        Detailed AI assessment unavailable; deterministic skill results are shown.
                      </p>
                    )}
                    {readiness && (
                      <section
                        id="readiness-explanation"
                        tabIndex={-1}
                        className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2"
                      >
                        <div className="flex items-center gap-2">
                          <Badge
                            variant="outline"
                            className={`rounded-full text-[11px] font-normal ${READINESS_BADGE_CLASS[readiness.decision]}`}
                          >
                            {READINESS_LABELS[readiness.decision]}
                          </Badge>
                        </div>
                        {readinessSuggestion && (
                          <section className="mt-2 rounded-md border border-border/60 bg-background/70 px-2 py-1.5">
                            <h3 className="text-xs font-medium text-foreground">Suggested next action</h3>
                            <p className="mt-0.5 text-sm font-medium text-foreground">
                              Suggested: {readinessSuggestion.label}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {readinessSuggestion.explanation}
                            </p>
                            {hasApplySuggestionAssociation && (
                              <span
                                id="suggested-apply-action-description"
                                className="sr-only"
                              >
                                Suggested next action: Apply. Mark as Applied updates
                                your application status.
                              </span>
                            )}
                            {navigationTargetIsAvailable && (
                              <button
                                type="button"
                                onClick={navigateToSuggestedAction}
                                className="mt-1 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                                aria-label={
                                  readinessNavigationTarget === "readiness"
                                    ? "View readiness explanation"
                                    : `Go to ${readinessSuggestion.label.toLowerCase()} action`
                                }
                              >
                                {readinessNavigationTarget === "readiness"
                                  ? "View action"
                                  : "Go to action"}
                              </button>
                            )}
                          </section>
                        )}
                        {(readiness.deterministic_blockers ?? []).length > 0 && (
                          <section className="mt-2 rounded-md border border-red-200 bg-red-50/60 px-2 py-1.5 dark:border-red-900/60 dark:bg-red-950/20">
                            <h3 className="text-xs font-medium text-red-700 dark:text-red-300">Blocked by your rule</h3>
                            <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-red-700 dark:text-red-300">
                              {(readiness.deterministic_blockers ?? []).map((blocker) => (
                                <li key={blocker}>{blocker}</li>
                              ))}
                            </ul>
                          </section>
                        )}
                        {([...((readiness.deterministic_warnings ?? [])), ...(readiness.caveats ?? [])].length > 0) && (
                          <section className="mt-2 rounded-md border border-amber-200 bg-amber-50/60 px-2 py-1.5 dark:border-amber-900/60 dark:bg-amber-950/20">
                            <h3 className="text-xs font-medium text-amber-700 dark:text-amber-300">Needs review</h3>
                            <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-amber-700 dark:text-amber-300">
                              {[...(readiness.deterministic_warnings ?? []), ...(readiness.caveats ?? [])].map((item, index) => (
                                <li key={`${item}-${index}`}>{item}</li>
                              ))}
                            </ul>
                          </section>
                        )}
                        {(readiness.reasons ?? []).filter((reason) => !reason.startsWith("Known gaps:")).length > 0 && (
                          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                            {(readiness.reasons ?? [])
                              .filter((reason) => !reason.startsWith("Known gaps:"))
                              .slice(0, 2)
                              .map((reason) => (
                                <li key={reason}>{reason}</li>
                              ))}
                          </ul>
                        )}
                        {readiness.caveats.length > 0 && (
                          <p className="mt-2 text-xs text-muted-foreground">
                            {readiness.caveats.slice(0, 1).join(" ")}
                          </p>
                        )}
                      </section>
                    )}
                  </div>
                  {/* Row 2: detailed assessment CTA and supporting text */}
                  {!explainData && (
                    <div className="col-span-2 flex min-w-0 flex-col items-end gap-1 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => explainMutation.mutate()}
                        disabled={explainMutation.isPending}
                      >
                        {explainMutation.isPending && (
                          <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                        )}
                        Get Detailed AI Assessment
                      </Button>
                      <p className="self-stretch text-left text-[11px] text-muted-foreground">
                        Includes a structured skills-gap review and AI fit explanation.
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3">
                  <p className="text-xs text-muted-foreground text-center leading-relaxed">
                    Run match analysis to see how this job fits your profile.
                  </p>
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
                    Analyze Match
                  </Button>
                </div>
              )}

              {/* Detailed explain section (shown after View Match Details is clicked) */}
              {match && (skillData || explainData) && (
                <div className="flex flex-col gap-3 mt-4">
                  <Separator />
                  {analyzedMatchedSkills.length > 0 && (
                    <div className="w-full">
                      <h4 className="text-xs font-medium text-muted-foreground mb-1.5">Matched</h4>
                      <div className="flex flex-wrap gap-1">
                        {analyzedMatchedSkills.map((s) => (
                          <Badge key={s} variant="secondary" className="text-xs font-normal">{s}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {analyzedMissingSkills.length > 0 && (
                    <div className="w-full">
                      <h4 className="text-xs font-medium text-muted-foreground mb-1.5">Missing</h4>
                      <div className="flex flex-wrap gap-1">
                        {analyzedMissingSkills.map((kw) => (
                          <Badge key={kw} variant="outline" className="text-xs font-normal border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-300">{kw}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {match.explanation && (
                    <p className="text-xs text-muted-foreground leading-relaxed">{match.explanation}</p>
                  )}
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
                    {match.matched_skills.slice(0, 8).join(", ")}
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
      <AlertDialog open={showArchiveConfirm} onOpenChange={setShowArchiveConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Archive this job?</AlertDialogTitle>
            <AlertDialogDescription>
              {existingApp
                ? "This removes the job from your lists for you. Your saved application will remain available in Applications."
                : "This removes the job from your lists for you. It does not delete the job."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={archiveMutation.isPending}>Keep job</AlertDialogCancel>
            <AlertDialogAction
              disabled={archiveMutation.isPending}
              onClick={(event) => {
                event.preventDefault();
                archiveMutation.mutate();
              }}
            >
              {archiveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Archive job
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
            <CardContent className="pt-5">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-4/5 mt-2" />
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-0">
              <div className="flex gap-1 border-b border-border pb-0">
                {TABS.map((tab) => (
                  <Skeleton key={tab} className="h-9 w-24" />
                ))}
              </div>
            </CardHeader>
            <CardContent className="pt-5">
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
