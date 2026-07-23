"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Archive, Bookmark, Loader2, RefreshCw, Search, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi, listAllApplications, matchesApi } from "@/lib/api";
import type { MatchItem } from "@/lib/types";

const PAGE_SIZE = 20;
const RANK_TASK_STORAGE_KEY = "match_ranking_task_id";

function dedupeByJobId(matches: MatchItem[]): MatchItem[] {
  const seen = new Set<number>();
  return matches.filter((m) => {
    if (seen.has(m.job_id)) return false;
    seen.add(m.job_id);
    return true;
  });
}

/** Debounce hook — returns debounced value after `delay` ms, plus a `flush(v)` to skip the delay. */
function useDebounce<T>(value: T, delay: number): [T, (v: T) => void] {
  const [debounced, setDebounced] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => setDebounced(value), delay);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [value, delay]);

  const flush = (v: T) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setDebounced(v);
  };

  return [debounced, flush];
}

const JOB_SOURCE_LABELS: Record<string, string> = {
  indeed: "Indeed",
  linkedin: "LinkedIn",
  glassdoor: "Glassdoor",
  google: "Google Jobs",
  zip_recruiter: "ZipRecruiter",
};
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SkillsGapSummary } from "@/components/matches/skills-gap-summary";
import { JobCard, type JobCardData, type JobCardMatch } from "@/components/jobs/job-card";
import { getDisplayMatchConfidence } from "@/lib/match-display";

export function MatchesPanel() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const sentinelRef = useRef<HTMLDivElement>(null);

  // ---- Filters ------------------------------------------------------------
  const [filterSource, setFilterSource] = useState("");
  const [filterJobType, setFilterJobType] = useState("");
  const [filterLocationInput, setFilterLocationInput] = useState("");
  const [filterCompanyInput, setFilterCompanyInput] = useState("");
  const [filterShiftType, setFilterShiftType] = useState("");
  const [filterRemote, setFilterRemote] = useState(false);
  const [filterSourceType, setFilterSourceType] = useState("");
  const [filterSearch, setFilterSearch] = useState("");
  const [searchValue, setSearchValue] = useState("");
  const [rankTaskId, setRankTaskId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return sessionStorage.getItem(RANK_TASK_STORAGE_KEY);
  });
  const rankCompletionHandled = useRef<string | null>(null);

  const [filterLocation, flushLocation] = useDebounce(filterLocationInput, 500);
  const [filterCompany, flushCompany] = useDebounce(filterCompanyInput, 500);

  // Restore scroll position on mount (back from detail page)
  const scrollRestoredRef = useRef(false);
  useEffect(() => {
    if (scrollRestoredRef.current) return;
    const raw = sessionStorage.getItem("matches-list-state");
    if (!raw) return;
    try {
      const saved = JSON.parse(raw);
      scrollRestoredRef.current = true;
      if (saved.scrollY != null) {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            window.scrollTo({ top: saved.scrollY, behavior: "instant" as ScrollBehavior });
          });
        });
      }
      sessionStorage.removeItem("matches-list-state");
    } catch { /* ignore corrupted state */ }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only; guarded by scrollRestoredRef
  }, []);

  // Refs keep the latest query state readable inside the observer callback
  // without ever recreating the observer (which would cause it to re-fire
  // immediately on creation and cascade-fetch every remaining page).
  const hasNextPageRef = useRef(false);
  const isFetchingNextPageRef = useRef(false);
  const fetchNextPageRef = useRef<() => void>(() => {});

  const matchesQuery = useInfiniteQuery({
    queryKey: ["matches", filterSearch, filterSource, filterJobType, filterLocation, filterCompany, filterShiftType, filterRemote, filterSourceType],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      matchesApi.list({
        limit: PAGE_SIZE,
        cursor: pageParam ?? undefined,
        q: filterSearch || undefined,
        source: filterSource || undefined,
        jobType: filterJobType || undefined,
        location: filterLocation || undefined,
        company: filterCompany || undefined,
        shiftType: filterShiftType || undefined,
        isRemote: filterRemote || undefined,
        sourceType: filterSourceType || undefined,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const allMatches = dedupeByJobId(
    matchesQuery.data?.pages.flatMap((p) => p.items) ?? [],
  );
  const total = matchesQuery.data?.pages[0]?.total ?? 0;

  // Keep refs in sync with the latest query state on every render.
  hasNextPageRef.current = matchesQuery.hasNextPage;
  isFetchingNextPageRef.current = matchesQuery.isFetchingNextPage;
  fetchNextPageRef.current = matchesQuery.fetchNextPage;

  // Infinite scroll sentinel — created ONCE (empty dep array).
  // The observer reads hasNextPage / isFetchingNextPage / fetchNextPage
  // through refs so it never needs to be recreated when those values change.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    // rootMargin "0px 0px 300px 0px" fires the callback when the sentinel is
    // 300 px below the visible viewport — i.e. before the user reaches it.
    // New items are therefore inserted while the sentinel is still offscreen,
    // so the viewport never needs to shift and the scroll position stays put.
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return;
        if (
          entry.isIntersecting &&
          hasNextPageRef.current &&
          !isFetchingNextPageRef.current
        ) {
          fetchNextPageRef.current();
        }
      },
      { rootMargin: "0px 0px 300px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const setRankingTask = (taskId: string | null) => {
    setRankTaskId(taskId);
    if (taskId) sessionStorage.setItem(RANK_TASK_STORAGE_KEY, taskId);
    else sessionStorage.removeItem(RANK_TASK_STORAGE_KEY);
  };

  const rankMutation = useMutation({
    mutationFn: () => matchesApi.rank(),
    onSuccess: (res) => {
      if (res.task_id) setRankingTask(res.task_id);
      if (res.state === "active") {
        toast.message("Match rankings are already refreshing");
      } else if (res.state === "cooldown") {
        const minutes = Math.max(1, Math.ceil((res.retry_after_seconds ?? 0) / 60));
        toast.message(`Rankings were refreshed recently. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`);
      } else {
        toast.message("Match ranking queued");
      }
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Ranking failed"),
  });

  const rankTaskQuery = useQuery({
    queryKey: ["match-ranking-task", rankTaskId],
    queryFn: () => matchesApi.getRankTaskStatus(rankTaskId!),
    enabled: !!rankTaskId,
    refetchInterval: (query) => {
      const current = query.state.data?.status;
      return current === "queued" || current === "running" ? 1_000 : false;
    },
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 3,
  });

  useEffect(() => {
    if (
      !rankTaskId ||
      !(rankTaskQuery.error instanceof ApiError) ||
      rankTaskQuery.error.status !== 404
    ) {
      return;
    }
    setRankingTask(null);
  }, [rankTaskId, rankTaskQuery.error]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const task = rankTaskQuery.data;
    if (!rankTaskId || !task || rankCompletionHandled.current === rankTaskId) return;
    if (task.status === "done") {
      rankCompletionHandled.current = rankTaskId;
      setRankingTask(null);
      toast.success("Match rankings refreshed");
      queryClient.invalidateQueries({ queryKey: ["matches"] });
    } else if (task.status === "failed") {
      rankCompletionHandled.current = rankTaskId;
      setRankingTask(null);
      toast.error(task.error ?? "Ranking failed. Please try again.");
    } else if (task.status === "skipped") {
      rankCompletionHandled.current = rankTaskId;
      setRankingTask(null);
      toast.message(task.message);
    }
  }, [queryClient, rankTaskId, rankTaskQuery.data]);

  const rankingInProgress =
    rankMutation.isPending ||
    rankTaskQuery.data?.status === "queued" ||
    rankTaskQuery.data?.status === "running";

  const saveAppMutation = useMutation({
    mutationFn: (jobId: number) => applicationsApi.create(jobId),
    onSuccess: (application) => {
      if (application.status === "saved") toast.success("Saved to applications");
      else toast.error(`Job is already tracked as ${application.status}`);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Save failed"),
  });

  const unsaveAppMutation = useMutation({
    mutationFn: (applicationId: number) => applicationsApi.delete(applicationId),
    onSuccess: () => {
      toast.success("Removed from saved");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Unsave failed"),
  });

  const archiveMutation = useMutation({
    mutationFn: (jobId: number) => jobsApi.archive(jobId),
    onSuccess: () => {
      toast.success("Job archived");
      queryClient.invalidateQueries({ queryKey: ["matches"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to archive job"),
  });

  // Uses listAllApplications to avoid the first-50 limitation.
  const { data: appData } = useQuery({
    queryKey: ["applications", "list"],
    queryFn: listAllApplications,
    staleTime: 30_000,
  });
  const savedJobIds = new Set(
    (appData ?? []).filter((a) => a.status === "saved").map((a) => a.job_id),
  );
  const applicationsByJobId = new Map((appData ?? []).map((a) => [a.job_id, a]));

  return (
    <div className="flex flex-col gap-6">
      <SkillsGapSummary />

      {/* ── Full-width search bar ─────────────────────────── */}
      <div className="flex gap-2 py-3 border-y border-border">
        <Input
          value={searchValue}
          onChange={(e) => setSearchValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") setFilterSearch(searchValue); }}
          placeholder="Search title, company, or description…"
          className="h-9 text-sm flex-1"
        />
        <Button
          size="sm"
          onClick={() => setFilterSearch(searchValue)}
        >
          <Search className="h-4 w-4" />
          Search
        </Button>
        {filterSearch && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => { setSearchValue(""); setFilterSearch(""); }}
          >
            Clear
          </Button>
        )}
      </div>

      <div className="flex gap-6">
        {/* ── Filter sidebar ─────────────────────────────── */}
        <aside className="w-72 shrink-0 self-start rounded-lg border bg-card p-4 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Filters</h3>
            {(filterSource || filterJobType || filterLocationInput || filterCompanyInput || filterShiftType || filterRemote || filterSourceType || filterSearch || searchValue) && (
              <button
                type="button"
                onClick={() => {
                  setFilterSource("");
                  setFilterJobType("");
                  setFilterLocationInput("");
                  setFilterCompanyInput("");
                  flushLocation("");
                  flushCompany("");
                  setFilterShiftType("");
                  setFilterRemote(false);
                  setFilterSourceType("");
                  setFilterSearch("");
                  setSearchValue("");
                }}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Clear
              </button>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">Source</label>
            <select
              value={filterSource}
              onChange={(e) => setFilterSource(e.target.value)}
              className="rounded-md border border-input bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">All sources</option>
              {Object.entries(JOB_SOURCE_LABELS).map(([val, label]) => (
                <option key={val} value={val}>{label}</option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={filterRemote}
              onChange={(e) => setFilterRemote(e.target.checked)}
              className="rounded border-input"
            />
            <span>Remote only</span>
          </label>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">Job type</label>
            <select
              value={filterJobType}
              onChange={(e) => setFilterJobType(e.target.value)}
              className="rounded-md border border-input bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">All types</option>
              <option value="fulltime">Full-time</option>
              <option value="parttime">Part-time</option>
              <option value="contract">Contract</option>
              <option value="temporary">Temporary</option>
              <option value="internship">Internship</option>
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">Shift</label>
            <select
              value={filterShiftType}
              onChange={(e) => setFilterShiftType(e.target.value)}
              className="rounded-md border border-input bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">Any</option>
              <option value="day">Day shift</option>
              <option value="night">Night shift</option>
              <option value="mid">Mid shift</option>
              <option value="flexible">Flexible</option>
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">Location</label>
            <Input
              value={filterLocationInput}
              onChange={(e) => setFilterLocationInput(e.target.value)}
              placeholder="City or country…"
              className="h-8 text-sm"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">Company</label>
            <Input
              value={filterCompanyInput}
              onChange={(e) => setFilterCompanyInput(e.target.value)}
              placeholder="Company name…"
              className="h-8 text-sm"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">Origin</label>
            <select
              value={filterSourceType}
              onChange={(e) => setFilterSourceType(e.target.value)}
              className="rounded-md border border-input bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">All</option>
              <option value="global">Global (scraped)</option>
              <option value="manual">Manual imports</option>
              <option value="extension">Extension imports</option>
            </select>
          </div>
        </aside>

        {/* ── Match list ──────────────────────────────────── */}
        <div className="flex-1 min-w-0 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              {matchesQuery.data
                ? `${total} ranked jobs`
                : "Rank your active profile against the job feed."}
            </p>
            <Button
              onClick={() => rankMutation.mutate()}
              disabled={rankingInProgress}
            >
              {rankingInProgress ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              {rankingInProgress
                ? rankTaskQuery.data?.status === "queued" ? "Refresh queued…" : "Refreshing…"
                : "Refresh Match Rankings"}
            </Button>
          </div>

          {matchesQuery.isLoading && (
            <p className="text-sm text-muted-foreground">Loading matches…</p>
          )}

          {matchesQuery.data && allMatches.length === 0 && (
            <Card>
              <CardContent className="flex flex-col items-center gap-3 py-12 text-center text-sm text-muted-foreground">
                <Sparkles className="h-10 w-10 text-amber-400" />
                <p>
                  No matches yet. Make sure you have an active profile and ingested
                  jobs, then click "Refresh Match Rankings".
                </p>
              </CardContent>
            </Card>
          )}

      <div className="flex flex-col gap-3">
        {allMatches.map((match) => {
          const card: JobCardData = {
            jobId: match.job_id,
            title: match.title,
            company: match.company,
            location: match.location,
            isRemote: match.is_remote,
            jobType: match.job_type,
            shiftType: match.shift_type,
            salaryMin: match.salary_min,
            salaryMax: match.salary_max,
            salaryCurrency: match.salary_currency,
            source: match.source,
            sourceUrl: match.source_url,
            description: match.description,
            postedAt: match.posted_at,
            importedAt: null,
            companyLogo: match.company_logo,
          };
          const matchData: JobCardMatch = {
            score: match.llm_score ?? match.vector_score,
            confidence: getDisplayMatchConfidence(match),
            matchedSkills: match.matched_skills,
            readiness: match.readiness,
          };

          return (
            <JobCard
              key={match.job_id}
              job={card}
              match={matchData}
              compactMatchLayout
              showConfidenceLabel={false}
              hideExternalLink
              onCardClick={() => {
                sessionStorage.setItem(
                  "matches-list-state",
                  JSON.stringify({ scrollY: window.scrollY }),
                );
                router.push(`/jobs/${match.job_id}`);
              }}
              topRightAction={
                <div className="flex items-center gap-1">
                {savedJobIds.has(match.job_id) ? (
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label="Unsave job"
                    title="Unsave job"
                    disabled={unsaveAppMutation.isPending}
                    onClick={() => {
                      const application = applicationsByJobId.get(match.job_id);
                      if (application?.status === "saved") {
                        unsaveAppMutation.mutate(application.id);
                      }
                    }}
                    className="h-7 w-7 self-end bg-blue-100 text-blue-600 hover:bg-blue-200 hover:text-blue-700 dark:bg-blue-900/30 dark:hover:bg-blue-900/50"
                  >
                    <Bookmark className="h-4 w-4 fill-current" />
                  </Button>
                ) : (applicationsByJobId.get(match.job_id) != null &&
                  applicationsByJobId.get(match.job_id)?.status !== "closed") ? null : (
                  <Button
                    size="icon"
                    variant="secondary"
                    aria-label="Save job"
                    title="Save job"
                    onClick={() => saveAppMutation.mutate(match.job_id)}
                    className="h-7 w-7 self-end p-0 hover:bg-secondary-foreground/10 transition-colors"
                  >
                    <Bookmark className="h-4 w-4" />
                  </Button>
                )}
                <Button
                  size="icon"
                  variant="outline"
                  title="Archive job"
                  disabled={archiveMutation.isPending}
                  onClick={() => archiveMutation.mutate(match.job_id)}
                  className="h-7 w-7 p-0"
                >
                  {archiveMutation.isPending && <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />}
                  <Archive className="h-4 w-4" />
                </Button>
                </div>
              }
            />
          );
        })}
      </div>
      </div>{/* /flex-1 match list wrapper */}
      </div>{/* /flex gap-6 sidebar+list row */}

      {/* Sentinel — IntersectionObserver watches this to trigger next page.
          overflow-anchor:none prevents the browser from scrolling the viewport
          down to keep this element in view when new items are inserted above it. */}
      <div ref={sentinelRef} className="h-4" style={{ overflowAnchor: "none" }} />

      {matchesQuery.isFetchingNextPage && (
        <div className="flex justify-center py-4">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {!matchesQuery.hasNextPage && allMatches.length > 0 && (
        <p className="py-2 text-center text-xs text-muted-foreground">
          All {total} matches loaded
        </p>
      )}
    </div>
  );
}
