"use client";

import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, FileText, Link2, Loader2, MapPin, Plus, X } from "lucide-react";
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
  const [showArchived, setShowArchived] = useState(false);
  const [coverJob, setCoverJob] = useState<Job | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Refs keep the latest query state readable inside the observer callback
  // without ever recreating the observer (which would cause it to re-fire
  // immediately on creation and cascade-fetch every remaining page).
  const hasNextPageRef = useRef(false);
  const isFetchingNextPageRef = useRef(false);
  const fetchNextPageRef = useRef<() => void>(() => {});

  const jobsQuery = useInfiniteQuery({
    queryKey: ["jobs", filterSource, filterJobType, filterLocation, showArchived],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      jobsApi.list({
        limit: PAGE_SIZE,
        offset: (pageParam as number) * PAGE_SIZE,
        source: filterSource || undefined,
        jobType: filterJobType || undefined,
        location: filterLocation || undefined,
        includeArchived: showArchived,
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
    mutationFn: ({ jobId, archived }: { jobId: number; archived: boolean }) =>
      jobsApi.archive(jobId, archived),
    onSuccess: (_, { archived }) => {
      toast.success(archived ? "Job archived" : "Job restored");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: () => toast.error("Failed to update job"),
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
  const [importDescription, setImportDescription] = useState("");

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
              {(filterSource || filterJobType || filterLocation) && (
                <button
                  type="button"
                  onClick={() => {
                    setFilterSource("");
                    setFilterJobType("");
                    setFilterLocation("");
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

            <button
              type="button"
              onClick={() => setShowArchived((v) => !v)}
              className={`inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs transition-colors ${
                showArchived
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-input hover:bg-muted"
              }`}
            >
              <Archive className="h-3 w-3" />
              {showArchived ? "Archived" : "Show archived"}
            </button>
          </div>
        </div>
        {jobsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading jobs…</p>
        )}
        {jobsQuery.data && allJobs.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No jobs yet.{" "}
              <a href="/dashboard/settings/preferences" className="underline hover:text-foreground">
                Save your preferences
              </a>{" "}
              and click <strong>Refresh now</strong> to pull the first batch.
            </CardContent>
          </Card>
        )}
        <div className="flex flex-col gap-3">
          {allJobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              onArchive={(archived) =>
                archiveMutation.mutate({ jobId: job.id, archived })
              }
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
        <DialogContent>
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
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="import-title">Job title *</Label>
                <Input
                  id="import-title"
                  placeholder="e.g. Senior Frontend Engineer"
                  value={importTitle}
                  onChange={(e) => setImportTitle(e.target.value)}
                />
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
  onArchive: (archived: boolean) => void;
  onSaveToApps: () => void;
  onCoverLetter: () => void;
}) {
  return (
    <Card className={job.is_archived ? "opacity-60" : undefined}>
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
            {job.is_archived && (
              <Badge variant="secondary" className="text-xs">Archived</Badge>
            )}
            <Badge variant="outline">{job.source}</Badge>
            <button
              type="button"
              onClick={() => onArchive(!job.is_archived)}
              title={job.is_archived ? "Restore job" : "Archive job"}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              {job.is_archived ? (
                <ArchiveRestore className="h-4 w-4" />
              ) : (
                <Archive className="h-4 w-4" />
              )}
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
