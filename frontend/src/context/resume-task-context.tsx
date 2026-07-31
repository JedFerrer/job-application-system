"use client";

/**
 * Global resume-task watcher.
 *
 * Lives at the root (inside Providers) so polling continues even when the
 * user navigates away from the /resume page. The success/failure toast
 * includes an action button that routes to the relevant resume surface.
 *
 * ResumePanel calls `setTask(taskId, filename)` to hand off the task.
 */

import { createContext, useContext, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { resumeApi } from "@/lib/api";
import type { ResumeResponse } from "@/lib/types";

interface ResumeTaskContextValue {
  taskId: string | null;
  /** Call this right after receiving the 202 task_id from the server. */
  setTask: (taskId: string, filename: string) => void;
  isRunning: boolean;
  progressMsg: string | null;
}

const ResumeTaskContext = createContext<ResumeTaskContextValue>({
  taskId: null,
  setTask: () => {},
  isRunning: false,
  progressMsg: null,
});

export function ResumeTaskProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const router = useRouter();

  // Seed from sessionStorage so a page refresh / navigation-back restores state.
  const [taskId, setTaskIdState] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return sessionStorage.getItem("resume_task_id");
  });
  const [selectedName, setSelectedNameState] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return sessionStorage.getItem("resume_task_name");
  });

  const setTask = (id: string, name: string) => {
    setTaskIdState(id);
    setSelectedNameState(name);
    sessionStorage.setItem("resume_task_id", id);
    sessionStorage.setItem("resume_task_name", name);
  };

  const clearTask = () => {
    setTaskIdState(null);
    setSelectedNameState(null);
    sessionStorage.removeItem("resume_task_id");
    sessionStorage.removeItem("resume_task_name");
  };

  // Poll while a task is active — continues even when ResumePanel is unmounted.
  const taskQuery = useQuery({
    queryKey: ["resume-task", taskId],
    queryFn: () => resumeApi.getTaskStatus(taskId!),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return !s || s === "queued" || s === "running" ? 2000 : false;
    },
  });

  // Fire toast (from any page) when the task settles.
  useEffect(() => {
    const data = taskQuery.data;
    if (!data || (data.status !== "done" && data.status !== "failed")) return;
    const name = selectedName;
    clearTask();

    if (data.status === "failed") {
      toast.error(data.error || "Resume parsing failed", {
        action: { label: "View résumé", onClick: () => router.push("/dashboard/resume") },
        duration: 8000,
      });
      return;
    }

    const result = data.result as ResumeResponse | undefined;
    const isDraft = result?.is_active === false || result?.lifecycle_status === "draft";
    toast.success(`Résumé parsed — "${result?.filename ?? name ?? "résumé"}"`, {
      description: isDraft
        ? "Saved as a draft awaiting review. Your active profile was not changed."
        : "Your profile has been updated.",
      action: {
        label: isDraft ? "Review profile" : "View résumé",
        onClick: () =>
          router.push(isDraft ? "/dashboard/settings/profile" : "/dashboard/resume"),
      },
      duration: 8000,
    });
    for (const warning of result?.extraction_warnings ?? []) {
      toast.warning(warning.message, { duration: 8000 });
    }
    if (isDraft) {
      queryClient.invalidateQueries({ queryKey: ["resume-imports"] });
    } else {
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    }
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
    <ResumeTaskContext.Provider value={{ taskId, setTask, isRunning, progressMsg }}>
      {children}
    </ResumeTaskContext.Provider>
  );
}

export const useResumeTask = () => useContext(ResumeTaskContext);
