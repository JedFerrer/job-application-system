"use client";

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Loader2, MapPin, Search, X } from "lucide-react";
import { toast } from "sonner";
import { ApiError, jobsApi } from "@/lib/api";
import { useJobsTask } from "@/context/jobs-task-context";
import { JOB_SOURCES, ingestSchema, type IngestForm } from "@/lib/validators";
import type { Job } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const JOB_TYPES = [
  { label: "Full-time", value: "fulltime" },
  { label: "Part-time", value: "parttime" },
  { label: "Contract", value: "contract" },
  { label: "Temporary", value: "temporary" },
  { label: "Internship", value: "internship" },
];

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

  const jobsQuery = useQuery({
    queryKey: ["jobs", filterSource, filterJobType, filterLocation, showArchived],
    queryFn: () =>
      jobsApi.list({
        limit: 100,
        source: filterSource || undefined,
        jobType: filterJobType || undefined,
        location: filterLocation || undefined,
        includeArchived: showArchived,
      }),
    refetchOnMount: "always",
  });

  const archiveMutation = useMutation({
    mutationFn: ({ jobId, archived }: { jobId: number; archived: boolean }) =>
      jobsApi.archive(jobId, archived),
    onSuccess: (_, { archived }) => {
      toast.success(archived ? "Job archived" : "Job restored");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: () => toast.error("Failed to update job"),
  });

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<IngestForm>({
    resolver: zodResolver(ingestSchema),
    defaultValues: {
      search_term: "",
      location: "",
      country: "USA",
      is_remote: false,
      hours_old: 504,
      results_wanted: 25,
      sources: ["indeed"],
    },
  });

  const selectedSources = watch("sources");

  // Sources that only work with residential proxies when running on a server.
  const PROXY_REQUIRED_SOURCES = new Set(["google"]);

  // ---- Ingest task state managed globally so polling survives navigation ----
  const { setTask, isRunning, progressMsg } = useJobsTask();

  const ingestMutation = useMutation({
    mutationFn: (data: IngestForm) =>
      jobsApi.ingest({
        search_term: data.search_term,
        location: data.location || null,
        country: data.country || null,
        is_remote: data.is_remote ?? null,
        hours_old: data.hours_old ?? 504,
        results_wanted: data.results_wanted ?? null,
        sources: data.sources,
      }),
    onSuccess: (res, variables) => setTask(res.task_id, variables.sources),
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Ingestion failed"),
  });

  const elapsed = useElapsed(isRunning);

  const toggleSource = (source: (typeof JOB_SOURCES)[number]) => {
    const next = selectedSources.includes(source)
      ? selectedSources.filter((s) => s !== source)
      : [...selectedSources, source];
    setValue("sources", next, { shouldValidate: true });
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Ingest jobs</CardTitle>
          <CardDescription>
            Scrape and embed postings. Requires the scraping extra installed on
            the backend (LinkedIn needs proxies).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={handleSubmit((d) => ingestMutation.mutate(d))}
            className="flex flex-col gap-4"
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="search_term">Search term</Label>
                <Input
                  id="search_term"
                  placeholder="python engineer"
                  {...register("search_term")}
                />
                {errors.search_term && (
                  <p className="text-sm text-destructive">
                    {errors.search_term.message}
                  </p>
                )}
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="location">Location</Label>
                <Input
                  id="location"
                  placeholder="Remote"
                  {...register("location")}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="country">Country (Indeed/Glassdoor)</Label>
                <Input id="country" placeholder="USA" {...register("country")} />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="results_wanted">Results wanted</Label>
                <Input
                  id="results_wanted"
                  type="number"
                  min={1}
                  max={200}
                  {...register("results_wanted", { valueAsNumber: true })}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="hours_old">Posted within</Label>
                <select
                  id="hours_old"
                  className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                  {...register("hours_old", { valueAsNumber: true })}
                >
                  <option value={24}>Last 24 hours</option>
                  <option value={72}>Last 3 days</option>
                  <option value={168}>Last week</option>
                  <option value={336}>Last 2 weeks</option>
                  <option value={504}>Last 3 weeks</option>
                  <option value={720}>Last 30 days</option>
                </select>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <input
                id="is_remote"
                type="checkbox"
                className="h-4 w-4"
                {...register("is_remote")}
              />
              <Label htmlFor="is_remote">Remote only</Label>
            </div>

            <div className="flex flex-col gap-2">
              <Label>Sources</Label>
              <div className="flex flex-wrap gap-2">
                {JOB_SOURCES.map((source) => {
                  const active = selectedSources.includes(source);
                  const needsProxy = PROXY_REQUIRED_SOURCES.has(source);
                  return (
                    <button
                      key={source}
                      type="button"
                      onClick={() => toggleSource(source)}
                      title={needsProxy ? "Requires residential proxies on a server — will return 0 results without them" : undefined}
                      className={`flex flex-col items-start rounded-md border px-3 py-1.5 text-sm transition-colors ${
                        active
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-input hover:bg-secondary"
                      }`}
                    >
                      <span>{source}</span>
                      {needsProxy && (
                        <span className={`text-[10px] leading-none ${
                          active ? "text-primary-foreground/70" : "text-muted-foreground"
                        }`}>
                          needs proxies
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
              {errors.sources && (
                <p className="text-sm text-destructive">
                  {errors.sources.message}
                </p>
              )}
            </div>

            <div className="flex flex-col gap-2">
              <Button type="submit" disabled={isSubmitting || isRunning}>
                {isRunning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Search className="h-4 w-4" />
                )}
                {isRunning
                  ? `${progressMsg} (${elapsed}s)`
                  : "Ingest jobs"}
              </Button>
              {isRunning && (
                <p className="text-xs text-muted-foreground">
                  Running in the background — you can navigate away and come
                  back; results will appear in the list below when done.
                </p>
              )}
            </div>
          </form>
        </CardContent>
      </Card>

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">
              Jobs{" "}
              {jobsQuery.data ? (
                <span className="text-muted-foreground">
                  ({jobsQuery.data.total})
                </span>
              ) : null}
            </h2>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setShowArchived((v) => !v)}
                className={`flex items-center gap-1 text-xs ${
                  showArchived
                    ? "text-foreground font-medium"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Archive className="h-3 w-3" />
                {showArchived ? "Hide archived" : "Show archived"}
              </button>
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

          {/* Filter bar */}
          <div className="flex flex-wrap gap-2">
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
              {JOB_TYPES.map(({ label, value }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>

            <Input
              value={filterLocation}
              onChange={(e) => setFilterLocation(e.target.value)}
              placeholder="Filter by location / country…"
              className="h-8 w-56 text-sm"
            />
          </div>
        </div>
        {jobsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading jobs…</p>
        )}
        {jobsQuery.data && jobsQuery.data.items.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No jobs yet. Run an ingestion above.
            </CardContent>
          </Card>
        )}
        <div className="flex flex-col gap-3">
          {jobsQuery.data?.items.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              onArchive={(archived) =>
                archiveMutation.mutate({ jobId: job.id, archived })
              }
            />
          ))}
        </div>
      </section>
    </div>
  );
}

// ---- Helpers -----------------------------------------------------------------

function useElapsed(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (active) {
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [active]);
  return elapsed;
}

function JobCard({ job, onArchive }: { job: Job; onArchive: (archived: boolean) => void }) {
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
      </CardContent>
    </Card>
  );
}
