"use client";

import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi } from "@/lib/api";
import type { Job } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";
import { JobCard, type JobCardData, type JobCardMatch } from "@/components/jobs/job-card";

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
          {allJobs.map((job) => {
            const match: JobCardMatch | null =
              job.match_score != null
                ? {
                    score: job.match_score,
                    confidence:
                      typeof job.match_reason === "object" &&
                      job.match_reason &&
                      "confidence" in job.match_reason
                        ? (job.match_reason.confidence as string)
                        : null,
                    matchedSkills: null,
                  }
                : null;

            return (
              <JobCard
                key={job.id}
                job={toJobCardData(job)}
                sourceLabel={job.ingest_source === "extension" ? "Extension" : "Manual"}
                match={match}
                isAnalyzing={job.match_score == null}
                onArchive={() => archiveMutation.mutate(job.id)}
              >
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
              </JobCard>
            );
          })}
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

// ---- Helpers -----------------------------------------------------------------

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
  };
}
