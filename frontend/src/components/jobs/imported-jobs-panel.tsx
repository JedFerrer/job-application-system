"use client";

import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, Download, FileText, Loader2, MapPin, Plus } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi } from "@/lib/api";
import type { Job } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";

const PAGE_SIZE = 25;

function dedupeById(jobs: Job[]): Job[] {
  const seen = new Set<number>();
  return jobs.filter((job) => {
    if (seen.has(job.id)) return false;
    seen.add(job.id);
    return true;
  });
}

export function ImportedJobsPanel() {
  const queryClient = useQueryClient();
  const [coverJob, setCoverJob] = useState<Job | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const hasNextPageRef = useRef(false);
  const isFetchingNextPageRef = useRef(false);
  const fetchNextPageRef = useRef<() => void>(() => {});

  const jobsQuery = useInfiniteQuery({
    queryKey: ["imported-jobs"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      jobsApi.listImported({
        limit: PAGE_SIZE,
        offset: (pageParam as number) * PAGE_SIZE,
      }),
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
      return loaded < lastPage.total ? allPages.length : undefined;
    },
    refetchOnMount: "always",
  });

  hasNextPageRef.current = jobsQuery.hasNextPage;
  isFetchingNextPageRef.current = jobsQuery.isFetchingNextPage;
  fetchNextPageRef.current = jobsQuery.fetchNextPage;

  const allJobs = dedupeById(
    jobsQuery.data?.pages.flatMap((p) => p.items) ?? [],
  );
  const total = jobsQuery.data?.pages[0]?.total ?? 0;

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
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
  }, []);

  const archiveMutation = useMutation({
    mutationFn: (jobId: number) => jobsApi.archive(jobId),
    onSuccess: () => {
      toast.success("Job archived");
      queryClient.invalidateQueries({ queryKey: ["imported-jobs"] });
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

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            Imported Jobs{" "}
            {jobsQuery.data ? (
              <span className="text-muted-foreground">({total})</span>
            ) : null}
          </h2>
        </div>

        {jobsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading imported jobs…</p>
        )}
        {jobsQuery.data && allJobs.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-12 text-center text-sm text-muted-foreground">
              <Download className="h-10 w-10 text-emerald-400" />
              <p>
                No imported jobs yet. Use the <strong>Import job</strong> button on
                the Jobs page or the browser extension to add jobs.
              </p>
            </CardContent>
          </Card>
        )}
        <div className="flex flex-col gap-3">
          {allJobs.map((job) => (
            <Card key={job.id}>
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
                    <Badge variant="secondary" className="text-xs">
                      {job.ingest_source === "extension" ? "Extension" : "Manual"}
                    </Badge>
                    <Badge variant="outline">{job.source}</Badge>
                    <button
                      type="button"
                      onClick={() => archiveMutation.mutate(job.id)}
                      title="Archive"
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
                  {job.salary_min != null && (
                    <span>
                      {job.salary_currency ?? ""} {job.salary_min}
                      {job.salary_max != null ? `–${job.salary_max}` : ""}
                    </span>
                  )}
                  {job.imported_at && (
                    <span>
                      Imported {new Date(job.imported_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 pt-1">
                  {job.match_score != null ? (
                    <Badge variant="default" className="text-xs">
                      {Math.round(job.match_score)}% match
                    </Badge>
                  ) : (
                    <Badge variant="secondary" className="text-xs animate-pulse">
                      Analysis pending
                    </Badge>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setCoverJob(job)}
                  >
                    <FileText className="h-4 w-4" />
                    Cover letter
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => saveAppMutation.mutate(job.id)}
                  >
                    <Plus className="h-4 w-4" />
                    Save to applications
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        <div ref={sentinelRef} className="h-4" style={{ overflowAnchor: "none" }} />
        {jobsQuery.isFetchingNextPage && (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {!jobsQuery.hasNextPage && allJobs.length > 0 && (
          <p className="py-2 text-center text-xs text-muted-foreground">
            All {total} imported jobs loaded
          </p>
        )}
      </section>

      {coverJob && (
        <CoverLetterDialog
          job={coverJob}
          open={!!coverJob}
          onOpenChange={(open) => { if (!open) setCoverJob(null); }}
        />
      )}
    </div>
  );
}
