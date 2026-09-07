"use client";

/**
 * Global jobs-ingest task watcher.
 *
 * Lives at the root (inside Providers) so polling continues even when the
 * user navigates away from /dashboard/jobs. The success/failure toast
 * includes an action button that routes back to /dashboard/jobs.
 *
 * JobsPanel calls `setTask(taskId, sources)` to hand off the task.
 */

import { createContext, useContext, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ApiError, jobsApi } from "@/lib/api";
import { useAuth } from "@/context/auth-context";
import type { IngestResponse, SourceRunResult } from "@/lib/types";

interface JobsTaskContextValue {
  taskId: string | null;
  /** Call this right after receiving the 202 task_id from the server. */
  setTask: (taskId: string, sources: string[]) => void;
  isRunning: boolean;
  progressMsg: string | null;
  sourceResults: SourceRunResult[];
}

const JobsTaskContext = createContext<JobsTaskContextValue>({
  taskId: null,
  setTask: () => {},
  isRunning: false,
  progressMsg: null,
  sourceResults: [],
});

export function JobsTaskProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { status: authStatus } = useAuth();

  // Seed from sessionStorage so navigating away and back restores the indicator.
  const [taskId, setTaskIdState] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return sessionStorage.getItem("jobs_task_id");
  });
  const [taskSources, setTaskSourcesState] = useState<string[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      return JSON.parse(sessionStorage.getItem("jobs_task_sources") ?? "[]");
    } catch {
      return [];
    }
  });
  const [lastSourceResults, setLastSourceResults] = useState<SourceRunResult[]>([]);

  const setTask = (id: string, sources: string[]) => {
    setTaskIdState(id);
    setTaskSourcesState(sources);
    sessionStorage.setItem("jobs_task_id", id);
    sessionStorage.setItem("jobs_task_sources", JSON.stringify(sources));
    setLastSourceResults([]);
  };

  const clearTask = () => {
    setTaskIdState(null);
    setTaskSourcesState([]);
    sessionStorage.removeItem("jobs_task_id");
    sessionStorage.removeItem("jobs_task_sources");
  };

  // Poll while a task is active — continues even when JobsPanel is unmounted.
  const taskQuery = useQuery({
    queryKey: ["job-task", taskId],
    queryFn: () => jobsApi.getTaskStatus(taskId!),
    enabled: !!taskId && authStatus === "authenticated",
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 1,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return !s || s === "queued" || s === "running" ? 2000 : false;
    },
  });

  useEffect(() => {
    const error = taskQuery.error;
    if (!(error instanceof ApiError && error.status === 404) || !taskId) return;
    clearTask();
    toast.error("Job refresh status is no longer available. Start a new refresh.", {
      action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
      duration: 8000,
    });
  }, [taskQuery.error, taskId]);

  // Fire toast from any page when the task settles.
  useEffect(() => {
    const data = taskQuery.data;
    if (!data || (data.status !== "done" && data.status !== "failed")) return;
    clearTask();

    const result = data.result as IngestResponse | undefined;
    setLastSourceResults(data.source_results ?? result?.source_results ?? []);
    if (data.status === "failed") {
      toast.error(result?.source_results?.length ? "No requested source completed successfully." : (data.error || "Ingestion failed"), {
        action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
        duration: 8000,
      });
      return;
    }

    if (!result || result.outcome === "partial_success") {
      toast.warning("Job refresh completed with some source results unavailable. Review the source summary.", {
            action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
          duration: 8000,
        });
    } else if (result.fetched === 0) {
      toast.warning("No results returned. Source completeness may be unknown.", {
        action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
        duration: 8000,
      });
    } else {
      toast.success(
        `Ingested ${result.fetched} jobs — ${result.inserted} new, ${result.updated} updated, ${result.embedded} embedded`,
        {
          action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
          duration: 8000,
        },
      );
    }

    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }, [taskQuery.data?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const isRunning =
    !!taskId &&
    (!taskQuery.data ||
      (taskQuery.data.status !== "done" && taskQuery.data.status !== "failed"));

  const progressMsg =
    taskId && taskQuery.data
      ? taskQuery.data.message
      : taskId
        ? "Starting…"
        : null;
  const sourceResults = taskQuery.data?.source_results
    ?? (taskQuery.data?.result as IngestResponse | undefined)?.source_results
    ?? lastSourceResults;

  return (
    <JobsTaskContext.Provider value={{ taskId, setTask, isRunning, progressMsg, sourceResults }}>
      {children}
    </JobsTaskContext.Provider>
  );
}

export const useJobsTask = () => useContext(JobsTaskContext);
