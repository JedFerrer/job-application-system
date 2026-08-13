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
import type { IngestResponse } from "@/lib/types";

interface JobsTaskContextValue {
  taskId: string | null;
  /** Call this right after receiving the 202 task_id from the server. */
  setTask: (taskId: string, sources: string[]) => void;
  isRunning: boolean;
  progressMsg: string | null;
}

const JobsTaskContext = createContext<JobsTaskContextValue>({
  taskId: null,
  setTask: () => {},
  isRunning: false,
  progressMsg: null,
});

const PROXY_REQUIRED_SOURCES = new Set(["google"]);

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

  const setTask = (id: string, sources: string[]) => {
    setTaskIdState(id);
    setTaskSourcesState(sources);
    sessionStorage.setItem("jobs_task_id", id);
    sessionStorage.setItem("jobs_task_sources", JSON.stringify(sources));
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
    const sources = taskSources;
    clearTask();

    if (data.status === "failed") {
      toast.error(data.error || "Ingestion failed", {
        action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
        duration: 8000,
      });
      return;
    }

    const result = data.result as IngestResponse | undefined;
    if (!result || result.fetched === 0) {
      if (sources.some((s) => PROXY_REQUIRED_SOURCES.has(s))) {
        toast.warning(
          "Google Jobs returned 0 results — Google blocks server IPs. Add residential proxies to the backend.",
          {
            action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
            duration: 8000,
          },
        );
      } else {
        toast.warning("No jobs found. Try a different search term, location, or source.", {
            action: { label: "View jobs", onClick: () => router.push("/dashboard/jobs") },
          duration: 8000,
        });
      }
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

  return (
    <JobsTaskContext.Provider value={{ taskId, setTask, isRunning, progressMsg }}>
      {children}
    </JobsTaskContext.Provider>
  );
}

export const useJobsTask = () => useContext(JobsTaskContext);
