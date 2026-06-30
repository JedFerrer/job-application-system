"use client";

import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, Briefcase, FileText, Link2, Loader2, MapPin, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi } from "@/lib/api";
import type { Job } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
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
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";

const PAGE_SIZE = 25;

/** Guard against duplicate rows across pages (offset pagination can overlap). */
function dedupeById(jobs: Job[]): Job[] {
  const seen = new Set<number>();
  return jobs.filter((job) => {
    if (seen.has(job.id)) return false;
    seen.add(job.id);
    return true;
  });
}

const JOB_SOURCE_LABELS: Record<string, string> = {
  indeed: "Indeed",
  linkedin: "LinkedIn",
  glassdoor: "Glassdoor",
  google: "Google Jobs",
  zip_recruiter: "ZipRecruiter",
};

export function JobsPanel() {
  const queryClient = useQueryClient();
  const [filterSource, setFilterSource] = useState("");
  const [filterJobType, setFilterJobType] = useState("");
  const [filterLocation, setFilterLocation] = useState("");
  const [filterSourceType, setFilterSourceType] = useState("");
  const [coverJob, setCoverJob] = useState<Job | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Refs keep the latest query state readable inside the observer callback
  // without ever recreating the observer (which would cause it to re-fire
  // immediately on creation and cascade-fetch every remaining page).
  const hasNextPageRef = useRef(false);
  const isFetchingNextPageRef = useRef(false);
  const fetchNextPageRef = useRef<() => void>(() => {});

  const jobsQuery = useInfiniteQuery({
    queryKey: ["jobs", filterSource, filterJobType, filterLocation, filterSourceType],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      jobsApi.list({
        limit: PAGE_SIZE,
        offset: (pageParam as number) * PAGE_SIZE,
        source: filterSource || undefined,
        jobType: filterJobType || undefined,
        location: filterLocation || undefined,
        sourceType: filterSourceType || undefined,
      }),
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
      return loaded < lastPage.total ? allPages.length : undefined;
    },
    refetchOnMount: "always",
  });

  // Keep refs in sync with the latest query state on every render.
  // This lets the stable observer callback read up-to-date values without
  // being a dependency of the effect that creates the observer.
  hasNextPageRef.current = jobsQuery.hasNextPage;
  isFetchingNextPageRef.current = jobsQuery.isFetchingNextPage;
  fetchNextPageRef.current = jobsQuery.fetchNextPage;

  const allJobs = dedupeById(
    jobsQuery.data?.pages.flatMap((p) => p.items) ?? [],
  );
  const total = jobsQuery.data?.pages[0]?.total ?? 0;

  // Infinite scroll sentinel — created ONCE (empty dep array).
  // The observer reads hasNextPage / isFetchingNextPage / fetchNextPage
  // through refs so it never needs to be recreated when those values change.
  // Recreating the observer on each state change was the bug: a new observer
  // fires its callback immediately for the current intersection state, so
  // every page-load completion triggered the next fetch without waiting for
  // the user to actually scroll — loading all remaining pages in one burst.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    // rootMargin "0px 0px 300px 0px" fires the callback when the sentinel is
    // 300 px below the visible viewport — i.e. before the user reaches it.
    // New items are therefore inserted while the sentinel is still offscreen,
    // so the viewport never needs to shift and the scroll position stays put.
    const observer = new IntersectionObserver(
      ([entry]) => {
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

  const archiveMutation = useMutation({
    mutationFn: (jobId: number) => jobsApi.archive(jobId),
    onSuccess: () => {
      toast.success("Job archived");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: () => toast.error("Failed to archive job"),
  });

  const saveAppMutation = useMutation({
    mutationFn: (jobId: number) => applicationsApi.create(jobId),
    onSuccess: () => {
      toast.success("Saved to applications");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Save failed"),
  });

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
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">
              Jobs{" "}
              {jobsQuery.data ? (
                <span className="text-muted-foreground">
                  ({total})
                </span>
              ) : null}
            </h2>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowImport(true)}
              >
                <Link2 className="h-4 w-4" />
                Import job
              </Button>
              {(filterSource || filterJobType || filterLocation || filterSourceType) && (
                <button
                  type="button"
                  onClick={() => {
                    setFilterSource("");
                    setFilterJobType("");
                    setFilterLocation("");
                    setFilterSourceType("");
                  }}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                  Clear filters
                </button>
              )}
            </div>
          </div>

          {/* Import job toggle */}

          {/* Filter bar */}
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={filterSource}
              onChange={(e) => setFilterSource(e.target.value)}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">All sources</option>
              {Object.entries(JOB_SOURCE_LABELS).map(([val, label]) => (
                <option key={val} value={val}>{label}</option>
              ))}
            </select>

            <select
              value={filterJobType}
              onChange={(e) => setFilterJobType(e.target.value)}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">All job types</option>
              <option value="fulltime">Full-time</option>
              <option value="parttime">Part-time</option>
              <option value="contract">Contract</option>
              <option value="temporary">Temporary</option>
              <option value="internship">Internship</option>
            </select>

            <Input
              value={filterLocation}
              onChange={(e) => setFilterLocation(e.target.value)}
              placeholder="Filter by location / country…"
              className="h-8 w-56 text-sm"
            />

            <select
              value={filterSourceType}
              onChange={(e) => setFilterSourceType(e.target.value)}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">All jobs</option>
              <option value="global">Global (scraped)</option>
              <option value="manual">Manual imports</option>
              <option value="extension">Extension imports</option>
            </select>
          </div>
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
        <div className="flex flex-col gap-3">
          {allJobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              onArchive={() => archiveMutation.mutate(job.id)}
              onSaveToApps={() => saveAppMutation.mutate(job.id)}
              onCoverLetter={() => setCoverJob(job)}
            />
          ))}
        </div>

        {/* Infinite scroll sentinel — overflow-anchor:none prevents the browser
            from scrolling the viewport to keep this element in view when new
            items are inserted above it, which would skip past the new content. */}
        <div ref={sentinelRef} className="h-4" style={{ overflowAnchor: "none" }} />
        {jobsQuery.isFetchingNextPage && (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {!jobsQuery.hasNextPage && allJobs.length > 0 && (
          <p className="py-2 text-center text-xs text-muted-foreground">
            All {total} jobs loaded
          </p>
        )}
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

      <CoverLetterDialog
        jobId={coverJob?.id ?? null}
        jobTitle={coverJob?.title}
        open={coverJob !== null}
        onOpenChange={(open) => !open && setCoverJob(null)}
      />
    </div>
  );
}

// ---- Helpers -----------------------------------------------------------------

function JobCard({
  job,
  onArchive,
  onSaveToApps,
  onCoverLetter,
}: {
  job: Job;
  onArchive: () => void;
  onSaveToApps: () => void;
  onCoverLetter: () => void;
}) {
  const sourceLabel =
    job.ingest_source === "manual"
      ? "Manual Import"
      : job.ingest_source === "extension"
        ? "Extension Import"
        : job.visibility === "public"
          ? "Global"
          : null;

  return (
    <Card>
      <CardContent className="flex flex-col gap-2 py-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <a
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium hover:underline"
            >
              {job.title}
            </a>
            <p className="text-sm text-muted-foreground">
              {job.company ?? "Unknown company"}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {sourceLabel && (
              <Badge variant="secondary" className="text-xs">{sourceLabel}</Badge>
            )}
            <Badge variant="outline">{job.source}</Badge>
            <button
              type="button"
              onClick={onArchive}
              title="Archive job"
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              <Archive className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          {job.location && (
            <span className="flex items-center gap-1">
              <MapPin className="h-3 w-3" />
              {job.location}
            </span>
          )}
          {job.is_remote && <span>Remote</span>}
          {job.job_type && <span>{job.job_type}</span>}
          {job.shift_type && (
            <span className={`capitalize font-medium ${
              job.shift_type === "night" ? "text-blue-500" :
              job.shift_type === "mid"   ? "text-amber-500" :
              job.shift_type === "day"   ? "text-green-600" : ""
            }`}>
              {job.shift_type} shift
            </span>
          )}
          {job.salary_min != null && (
            <span>
              {job.salary_currency ?? ""} {job.salary_min}
              {job.salary_max != null ? `–${job.salary_max}` : ""}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            size="sm"
            variant="outline"
            onClick={onCoverLetter}
          >
            <FileText className="h-4 w-4" />
            Cover letter
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onSaveToApps}
          >
            <Plus className="h-4 w-4" />
            Save to applications
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
