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
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi, listAllApplications, matchesApi } from "@/lib/api";
import type {
  ApplicationListItem,
  MatchExplanation,
  MatchItem,
  MatchListResponse,
  MatchReadinessDecision,
} from "@/lib/types";
import { useState, useMemo } from "react";
import { formatSalary, formatRelativeDate } from "@/lib/format";
import { formatJobDescription } from "@/lib/utils";
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
          explanation: null,
          matched_skills: cached.matched_skills ?? [],
          missing_keywords: [],
          readiness: cached.readiness,
        };
      }
    }
    return null;
  })();

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
  const readiness = match?.readiness ?? null;

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
            {/* Mark as Applied */}
            <Button
              variant={isApplied ? "secondary" : "default"}
              size="sm"
              onClick={() => applyMutation.mutate()}
              disabled={applyMutation.isPending || isApplied}
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
                {job.source_url && (
                  <DropdownMenuItem asChild>
                    <a
                      href={job.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="cursor-pointer"
                    >
                      <ExternalLink className="h-4 w-4" />
                      View Original Job
                    </a>
                  </DropdownMenuItem>
                )}
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
              <div className="flex gap-1 border-b border-border">
                {TABS.map((tab) => (
                  <button
                    key={tab}
                    type="button"
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
                <div className="text-sm leading-relaxed whitespace-pre-wrap wrap-break-word">
                  {job.description ? (
                    formatJobDescription(job.description)
                  ) : (
                    <p className="text-muted-foreground">No description available.</p>
                  )}
                </div>
              )}
              {activeTab === "Skills" && (
                <div>
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
                <p className="text-sm text-muted-foreground">
                  Personal notes for this job will appear here.
                </p>
              )}
            </CardContent>
          </Card>
        </div>{/* /left column */}

        {/* ================================================================ */}
        {/* RIGHT SIDEBAR                                                     */}
        {/* ================================================================ */}
        <div className="flex flex-col gap-6">

          {/* ── Match Score ──────────────────────────────────── */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Match Score</CardTitle>
            </CardHeader>
            <CardContent>
              {match ? (
                  <div className="flex items-start gap-4">
                    {/* Left: score ring + fit label */}
                    <div className="shrink-0">
                      <CircularScore
                        score={matchScore}
                        confidence={match.confidence}
                        size="lg"
                        showConfidenceLabel={false}
                      />
                    </div>
                  {/* Right: text + button */}
                  <div className="flex flex-col gap-2 min-w-0 flex-1 pt-1 ml-2">
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      {matchScore >= 80
                        ? "You're a strong match for this role."
                        : matchScore >= 50
                          ? "You match many of the key requirements for this role."
                          : "This role may require additional skills beyond your profile."}
                    </p>
                    {readiness && (
                      <div className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Badge
                            variant="outline"
                            className={`rounded-full text-[11px] font-normal ${READINESS_BADGE_CLASS[readiness.decision]}`}
                          >
                            {READINESS_LABELS[readiness.decision]}
                          </Badge>
                        </div>
                        {readiness.reasons.length > 0 && (
                          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                            {readiness.reasons.slice(0, 2).map((reason) => (
                              <li key={reason}>{reason}</li>
                            ))}
                          </ul>
                        )}
                        {readiness.caveats.length > 0 && (
                          <p className="mt-2 text-xs text-muted-foreground">
                            {readiness.caveats.slice(0, 1).join(" ")}
                          </p>
                        )}
                      </div>
                    )}
                    {!explainData && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-2 w-fit"
                        onClick={() => explainMutation.mutate()}
                        disabled={explainMutation.isPending}
                      >
                        {explainMutation.isPending && (
                          <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                        )}
                        View Match Details
                      </Button>
                    )}
                  </div>
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
              {match && explainData && (
                <div className="flex flex-col gap-3 mt-4">
                  <Separator />
                  {match.matched_skills.length > 0 && (
                    <div className="w-full">
                      <h4 className="text-xs font-medium text-muted-foreground mb-1.5">Matched</h4>
                      <div className="flex flex-wrap gap-1">
                        {match.matched_skills.map((s) => (
                          <Badge key={s} variant="secondary" className="text-xs font-normal">{s}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {match.missing_keywords.length > 0 && (
                    <div className="w-full">
                      <h4 className="text-xs font-medium text-muted-foreground mb-1.5">Missing</h4>
                      <div className="flex flex-wrap gap-1">
                        {match.missing_keywords.map((kw) => (
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
                  <span className="font-medium truncate max-w-40">{job.location}</span>
                </div>
              )}
              {salary && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Salary</span>
                  <span className="font-medium">{salary}</span>
                </div>
              )}
              {match?.matched_skills && match.matched_skills.length > 0 && (
                <div className="flex flex-col gap-1">
                  <span className="text-muted-foreground text-xs">Tech Stack</span>
                  <span className="font-medium text-xs leading-relaxed">
                    {match.matched_skills.slice(0, 8).join(", ")}
                  </span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* ── Application Status ───────────────────────────── */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Application Status</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {existingApp ? (
                <>
                  <Badge
                    className={`text-xs font-normal w-fit ${STATUS_BADGE_CLASS[existingApp.status] ?? "bg-muted text-muted-foreground"}`}
                  >
                    {APPLICATION_STATUS_LABELS[existingApp.status] ?? existingApp.status}
                  </Badge>
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
