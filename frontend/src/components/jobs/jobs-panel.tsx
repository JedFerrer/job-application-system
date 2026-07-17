"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, Briefcase, Link2, Loader2, Search } from "lucide-react";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi, listAllApplications } from "@/lib/api";
import type { Job } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { JobCard, type JobCardData } from "@/components/jobs/job-card";

const PAGE_SIZE = 25;
const MAX_JOBS = 500; // UX cap — stop infinite scroll after this many loaded
const SESSION_KEY = "jobs-list-state";

const JOB_SOURCE_LABELS: Record<string, string> = {
  indeed: "Indeed",
  linkedin: "LinkedIn",
  glassdoor: "Glassdoor",
  google: "Google Jobs",
  zip_recruiter: "ZipRecruiter",
};

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

export function JobsPanel() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const archiveState = searchParams.get("archive_state") === "archived" ? "archived" : "active";
  const [filterSource, setFilterSource] = useState("");
  const [filterJobType, setFilterJobType] = useState("");
  const [filterLocationInput, setFilterLocationInput] = useState("");
  const [filterCompanyInput, setFilterCompanyInput] = useState("");
  const [filterSourceType, setFilterSourceType] = useState("");
  const [filterShiftType, setFilterShiftType] = useState("");
  const [filterRemote, setFilterRemote] = useState(false);
  const [filterSearch, setFilterSearch] = useState("");
  const [searchValue, setSearchValue] = useState("");

  // Debounced text filters (500ms delay)
  const [filterLocation, flushLocation] = useDebounce(filterLocationInput, 500);
  const [filterCompany, flushCompany] = useDebounce(filterCompanyInput, 500);

  // Restore saved filter state and scroll position on mount (back from detail)
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw);
      if (saved.filterSource) setFilterSource(saved.filterSource);
      if (saved.filterJobType) setFilterJobType(saved.filterJobType);
      if (saved.filterLocationInput) { setFilterLocationInput(saved.filterLocationInput); flushLocation(saved.filterLocationInput); }
      if (saved.filterCompanyInput) { setFilterCompanyInput(saved.filterCompanyInput); flushCompany(saved.filterCompanyInput); }
      if (saved.filterSourceType) setFilterSourceType(saved.filterSourceType);
      if (saved.filterShiftType) setFilterShiftType(saved.filterShiftType);
      if (saved.filterRemote) setFilterRemote(saved.filterRemote);
      if (saved.filterSearch) { setFilterSearch(saved.filterSearch); setSearchValue(saved.filterSearch); }
      restoredRef.current = true;
      // Restore scroll after a short delay for the virtualizer to stabilise
      if (saved.scrollY != null) {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            window.scrollTo({ top: saved.scrollY, behavior: "instant" as ScrollBehavior });
          });
        });
      }
      sessionStorage.removeItem(SESSION_KEY);
    } catch { /* ignore corrupted state */ }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only; guarded by restoredRef
  }, []);

  const jobsQuery = useInfiniteQuery({
    queryKey: ["jobs", archiveState, filterSearch, filterSource, filterJobType, filterLocation, filterCompany, filterSourceType, filterShiftType, filterRemote],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      jobsApi.list({
        limit: PAGE_SIZE,
        cursor: pageParam ?? undefined,
        q: filterSearch || undefined,
        source: filterSource || undefined,
        jobType: filterJobType || undefined,
        location: filterLocation || undefined,
        company: filterCompany || undefined,
        sourceType: filterSourceType || undefined,
        shiftType: filterShiftType || undefined,
        isRemote: filterRemote || undefined,
        archiveState,
      }),
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
      if (loaded >= MAX_JOBS) return undefined;
      return lastPage.next_cursor ?? undefined;
    },
    staleTime: 60_000,
  });

  const allJobs = jobsQuery.data?.pages.flatMap((p) => p.items) ?? [];
  const total = jobsQuery.data?.pages[0]?.total ?? 0;

  // Pre-compute card data and source labels — avoids re-creating objects on every render
  const jobCards = useMemo(
    () =>
      allJobs.map((job) => ({
        card: toJobCardData(job),
        sourceLabel: jobSourceLabel(job),
      })),
    [allJobs],
  );

  // Virtualize using window/page scroll
  const virtualizer = useWindowVirtualizer({
    count: allJobs.length + (jobsQuery.hasNextPage ? 1 : 0),
    estimateSize: () => 180,
    overscan: 3,
  });

  // Fetch next page when nearing the end — guard against double-fetches
  const fetchingRef = useRef(false);
  const virtualItems = virtualizer.getVirtualItems();
  const lastVirtualIndex = virtualItems.length > 0 ? virtualItems[virtualItems.length - 1]!.index : 0;
  useEffect(() => {
    if (
      lastVirtualIndex >= allJobs.length - 5 &&
      jobsQuery.hasNextPage &&
      !jobsQuery.isFetchingNextPage &&
      !fetchingRef.current
    ) {
      fetchingRef.current = true;
      jobsQuery.fetchNextPage().finally(() => { fetchingRef.current = false; });
    }
  }, [lastVirtualIndex, allJobs.length, jobsQuery.hasNextPage, jobsQuery.isFetchingNextPage, jobsQuery.fetchNextPage]);

  // Reset scroll to top when filters/search trigger a fresh query (allJobs resets to 0 then repopulates)
  const prevJobCountRef = useRef(allJobs.length);
  useEffect(() => {
    if (allJobs.length > 0 && prevJobCountRef.current === 0) {
      window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
    }
    prevJobCountRef.current = allJobs.length;
  }, [allJobs.length]);

  const [savedJobIds, setSavedJobIds] = useState<Set<number>>(new Set());

  // Fetch existing applications to know which jobs are already saved.
  // Uses listAllApplications to avoid the first-50 limitation.
  const { data: appData } = useQuery({
    queryKey: ["applications", "list"],
    queryFn: listAllApplications,
    staleTime: 30_000,
  });
  const seededRef = useRef(false);
  useEffect(() => {
    if (appData && !seededRef.current) {
      setSavedJobIds(new Set(appData.map((a) => a.job_id)));
      seededRef.current = true;
    }
  }, [appData]);

  const saveAppMutation = useMutation({
    mutationFn: (jobId: number) => applicationsApi.create(jobId),
    onSuccess: (_data, jobId) => {
      toast.success("Saved to applications");
      setSavedJobIds((prev) => new Set(prev).add(jobId));
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Save failed"),
  });

  const restoreMutation = useMutation({
    mutationFn: (jobId: number) => jobsApi.restore(jobId),
    onSuccess: () => {
      toast.success("Job restored");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["imported-jobs"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to restore job"),
  });

  const setArchiveState = (state: "active" | "archived") => {
    const params = new URLSearchParams(searchParams.toString());
    if (state === "archived") params.set("archive_state", "archived");
    else params.delete("archive_state");
    const query = params.toString();
    router.replace(query ? `/dashboard/jobs?${query}` : "/dashboard/jobs");
  };

  const [showImport, setShowImport] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importTitle, setImportTitle] = useState("");
  const [importCompany, setImportCompany] = useState("");
  const [importPlatform, setImportPlatform] = useState("");
  const [importPlatformCustom, setImportPlatformCustom] = useState("");
  const [importDescription, setImportDescription] = useState("");

  const effectivePlatform = importPlatform === "custom" ? importPlatformCustom : importPlatform;

  // Auto-detect platform from URL
  const detectedPlatform = (() => {
    if (!importUrl) return "";
    const u = importUrl.toLowerCase();
    if (u.includes("linkedin.com/jobs")) return "linkedin";
    if (u.includes("indeed.com")) return "indeed";
    if (u.includes("greenhouse.io")) return "greenhouse";
    if (u.includes("jobs.lever.co")) return "lever";
    if (u.includes("workdayjobs.com") || u.includes("myworkdayjobs.com")) return "workday";
    if (u.includes("ziprecruiter.com")) return "zip_recruiter";
    if (u.includes("glassdoor.com")) return "glassdoor";
    if (u.includes("wellfound.com")) return "wellfound";
    if (u.includes("ashbyhq.com")) return "ashby";
    if (u.includes("smartrecruiters.com")) return "smartrecruiters";
    if (u.includes("bamboohr.com")) return "bamboohr";
    if (u.includes("workable.com")) return "workable";
    if (u.includes("jobstreet.com")) return "jobstreet";
    if (u.includes("onlinejobs.ph")) return "onlinejobs_ph";
    return "";
  })();

  const PLATFORM_LABELS: Record<string, string> = {
    linkedin: "LinkedIn",
    indeed: "Indeed",
    greenhouse: "Greenhouse",
    lever: "Lever",
    workday: "Workday",
    zip_recruiter: "ZipRecruiter",
    glassdoor: "Glassdoor",
    wellfound: "Wellfound",
    ashby: "Ashby",
    smartrecruiters: "SmartRecruiters",
    bamboohr: "BambooHR",
    workable: "Workable",
    jobstreet: "JobStreet",
    onlinejobs_ph: "OnlineJobs.ph",
    custom: "Custom",
  };

  const importMutation = useMutation({
    mutationFn: () =>
      jobsApi.importJob({
        url: importUrl,
        title: importTitle,
        company: importCompany || undefined,
        description: importDescription || undefined,
      }),
    onSuccess: () => {
      toast.success("Job imported");
      setShowImport(false);
      setImportUrl("");
      setImportTitle("");
      setImportCompany("");
      setImportPlatform("");
      setImportPlatformCustom("");
      setImportDescription("");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Import failed"),
  });

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-1 self-start rounded-md border bg-muted p-1" role="group" aria-label="Job archive state">
          <Button size="sm" variant={archiveState === "active" ? "secondary" : "ghost"} onClick={() => setArchiveState("active")}>Active</Button>
          <Button size="sm" variant={archiveState === "archived" ? "secondary" : "ghost"} onClick={() => setArchiveState("archived")}>Archived</Button>
        </div>
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
          {/* ── Filter sidebar ──────────────────────────────── */}
          <aside className="w-72 shrink-0 self-start rounded-lg border bg-card p-4 flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium">Filters</h3>
              {(filterSource || filterJobType || filterLocationInput || filterCompanyInput || filterSourceType || filterShiftType || filterRemote || filterSearch || searchValue) && (
                <button
                  type="button"
                  onClick={() => {
                    setFilterSource("");
                    setFilterJobType("");
                    setFilterLocationInput("");
                    setFilterCompanyInput("");
                    flushLocation("");
                    flushCompany("");
                    setFilterSourceType("");
                    setFilterShiftType("");
                    setFilterRemote(false);
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

          {/* ── Job list ─────────────────────────────────────── */}
          <div className="flex-1 min-w-0 flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">
                Jobs{" "}
                {jobsQuery.data ? (
                  <span className="text-muted-foreground">({total})</span>
                ) : null}
              </h2>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowImport(true)}
              >
                <Link2 className="h-4 w-4" />
                Import job
              </Button>
            </div>
        {jobsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading jobs…</p>
        )}
        {jobsQuery.data && allJobs.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-12 text-center text-sm text-muted-foreground">
              <Briefcase className="h-10 w-10 text-blue-400" />
              <p>
                No jobs yet.{" "}
                <a href="/dashboard/settings/preferences" className="underline hover:text-foreground">
                  Save your preferences
                </a>{" "}
                and click <strong>Refresh now</strong> to pull the first batch.
              </p>
            </CardContent>
          </Card>
        )}
            {/* Virtualized job list — uses window scroll */}
            <div
              style={{
                height: `${virtualizer.getTotalSize()}px`,
                width: "100%",
                position: "relative",
              }}
            >
                {virtualItems.map((vi) => {
                  const isLoader = vi.index >= allJobs.length;
                  return (
                    <div
                      key={isLoader ? "__loader__" : allJobs[vi.index]!.id}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        transform: `translateY(${vi.start}px)`,
                      }}
                      data-index={vi.index}
                      ref={virtualizer.measureElement}
                    >
                      {isLoader ? (
                        jobsQuery.isFetchingNextPage ? (
                          <div className="flex justify-center py-4">
                            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                          </div>
                        ) : jobsQuery.hasNextPage ? null : (
                          <p className="py-2 text-center text-xs text-muted-foreground">
                            {allJobs.length >= MAX_JOBS
                              ? `Showing ${allJobs.length} of ${total} jobs. Refine your search or filters to see more.`
                              : `All ${total} jobs loaded`}
                          </p>
                        )
                      ) : (
                        <div className="pb-3">
                          {(() => {
                            const jc = jobCards[vi.index]!;
                            return (
                              <JobCard
                                job={jc.card}
                                sourceLabel={jc.sourceLabel}
                                hideExternalLink
                                isSaved={archiveState === "active" && savedJobIds.has(jc.card.jobId)}
                                isArchived={archiveState === "archived"}
                                onCardClick={() => {
                                  sessionStorage.setItem(
                                    SESSION_KEY,
                                    JSON.stringify({
                                      filterSource,
                                      filterJobType,
                                      filterLocationInput,
                                      filterCompanyInput,
                                      filterSourceType,
                                      filterShiftType,
                                      filterRemote,
                                      filterSearch: filterSearch || searchValue,
                                      scrollY: window.scrollY,
                                    }),
                                  );
                                  router.push(`/jobs/${jc.card.jobId}`);
                                }}
                                topRightAction={
                                  archiveState === "archived" ? (
                                    <Button
                                      size="sm"
                                      variant="secondary"
                                      disabled={restoreMutation.isPending}
                                      onClick={() => restoreMutation.mutate(jc.card.jobId)}
                                      className="h-7 px-2.5 text-xs"
                                    >
                                      {restoreMutation.isPending && <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />}
                                      Restore job
                                    </Button>
                                  ) : savedJobIds.has(jc.card.jobId) ? null : (
                                    <Button
                                      size="sm"
                                      variant="secondary"
                                      onClick={() => saveAppMutation.mutate(jc.card.jobId)}
                                      className="h-7 px-2.5 text-xs hover:bg-secondary-foreground/10 transition-colors"
                                    >
                                      <Bookmark className="h-3.5 w-3.5 mr-1" />
                                      Save
                                    </Button>
                                  )
                                }
                              />
                            );
                          })()}
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>
          </div>
        </div>
      </section>

      <Dialog open={showImport} onOpenChange={setShowImport}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Import job</DialogTitle>
            <DialogDescription>
              Paste a job posting URL and fill in the details to add it to your feed.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="import-url">Job posting URL *</Label>
              <Input
                id="import-url"
                placeholder="https://..."
                value={importUrl}
                onChange={(e) => setImportUrl(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="flex flex-col gap-2 sm:col-span-2">
                <Label htmlFor="import-title">Job title *</Label>
                <Input
                  id="import-title"
                  placeholder="e.g. Senior Frontend Engineer"
                  value={importTitle}
                  onChange={(e) => setImportTitle(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="import-platform">Platform</Label>
                <select
                  id="import-platform"
                  value={importPlatform}
                  onChange={(e) => setImportPlatform(e.target.value)}
                  className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="">Auto-detect</option>
                  {Object.entries(PLATFORM_LABELS).map(([val, label]) => (
                    <option key={val} value={val}>{label}</option>
                  ))}
                </select>
                {importPlatform === "custom" && (
                  <Input
                    placeholder="e.g. Kalibrr, BossJob, company site"
                    value={importPlatformCustom}
                    onChange={(e) => setImportPlatformCustom(e.target.value)}
                    className="mt-1"
                  />
                )}
                {!importPlatform && detectedPlatform && (
                  <p className="text-xs text-muted-foreground">
                    Detected: {PLATFORM_LABELS[detectedPlatform] ?? detectedPlatform}
                  </p>
                )}
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="import-company">Company</Label>
              <Input
                id="import-company"
                placeholder="e.g. Acme Corp"
                value={importCompany}
                onChange={(e) => setImportCompany(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="import-description">Job description</Label>
              <Textarea
                id="import-description"
                placeholder="Paste the job description text (optional)"
                value={importDescription}
                onChange={(e) => setImportDescription(e.target.value)}
                rows={5}
              />
            </div>
            <Button
              disabled={!importUrl.trim() || !importTitle.trim() || importMutation.isPending}
              onClick={() => importMutation.mutate()}
            >
              {importMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Link2 className="h-4 w-4" />
              )}
              Import job
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---- Helpers -----------------------------------------------------------------

/** Map a Job API object to the shared JobCardData shape. */
function toJobCardData(job: Job): JobCardData {
  return {
    jobId: job.id,
    title: job.title,
    company: job.company,
    location: job.location,
    isRemote: job.is_remote,
    jobType: job.job_type,
    shiftType: job.shift_type,
    salaryMin: job.salary_min,
    salaryMax: job.salary_max,
    salaryCurrency: job.salary_currency,
    source: job.source,
    sourceUrl: job.source_url,
    description: job.description,
    postedAt: job.posted_at,
    importedAt: job.imported_at,
    companyLogo: job.company_logo,
  };
}

function jobSourceLabel(job: Job): string | null {
  if (job.ingest_source === "manual") return "Manual Import";
  if (job.ingest_source === "extension") return "Extension Import";
  if (job.visibility === "public") return "Global";
  return null;
}
