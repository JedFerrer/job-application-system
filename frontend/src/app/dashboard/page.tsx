"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Briefcase,
  Check,
  ListChecks,
  Loader2,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { ApiError, applicationsApi, jobsApi, matchesApi } from "@/lib/api";
import { type ApplicationListItem } from "@/lib/types";
import {
  formatNextActionDateLabel,
  getNextActionLabel,
  getNextActionState,
  getTodayIsoDate,
} from "@/lib/next-actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export default function DashboardHome() {
  const queryClient = useQueryClient();
  const todayIso = getTodayIsoDate();

  const appsQuery = useQuery({
    queryKey: ["home-applications"],
    queryFn: () => applicationsApi.list(),
  });

  const nextActionsQuery = useQuery({
    queryKey: ["home-next-actions", todayIso],
    queryFn: () => applicationsApi.listDueNextActions(todayIso),
  });

  const matchesQuery = useQuery({
    queryKey: ["home-matches"],
    queryFn: () => matchesApi.list({ limit: 1 }),
  });

  const jobsQuery = useQuery({
    queryKey: ["home-jobs"],
    queryFn: () => jobsApi.list({ limit: 1 }),
  });

  const apps = appsQuery.data?.items ?? [];
  const totalMatches = matchesQuery.data?.total ?? 0;
  const totalJobs = jobsQuery.data?.total ?? 0;

  const statusCounts = {
    saved: apps.filter((a) => a.status === "saved").length,
    applied: apps.filter((a) => a.status === "applied").length,
    interview: apps.filter((a) => a.status === "interview").length,
    offer: apps.filter((a) => a.status === "offer").length,
    rejected: apps.filter((a) => a.status === "rejected").length,
    closed: apps.filter((a) => a.status === "closed").length,
  };

  const activeApplications =
    statusCounts.saved + statusCounts.applied + statusCounts.interview;
  const successRate =
    apps.length > 0 ? Math.round((statusCounts.offer / apps.length) * 100) : 0;

  const completeNextActionMutation = useMutation({
    mutationFn: (applicationId: number) =>
      applicationsApi.update(applicationId, {
        next_action_completed_at: new Date().toISOString(),
      }),
    onSuccess: () => {
      toast.success("Marked next action complete");
      queryClient.invalidateQueries({ queryKey: ["home-next-actions"] });
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : "Update failed");
    },
  });

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h2 className="text-lg font-semibold">Dashboard</h2>
        <p className="text-sm text-muted-foreground">
          Overview of your job search pipeline.
        </p>
      </div>

      <Card>
        <CardContent className="px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-sm font-semibold">Today</h3>
              <p className="text-xs text-muted-foreground">
                User-managed next actions due today or overdue.
              </p>
            </div>
            <Badge variant="secondary">{nextActionsQuery.data?.items.length ?? 0}</Badge>
          </div>

          {nextActionsQuery.isLoading ? (
            <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading next actions…
            </div>
          ) : nextActionsQuery.isError ? (
            <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>Couldn’t load next actions.</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => nextActionsQuery.refetch()}
              >
                Retry
              </Button>
            </div>
          ) : (nextActionsQuery.data?.items.length ?? 0) === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">
              No due or overdue next actions right now.
            </p>
          ) : (
            <div className="mt-4 space-y-3">
              {nextActionsQuery.data?.items.map((item) => (
                <TodayNextActionRow
                  key={item.id}
                  item={item}
                  todayIso={todayIso}
                  isCompleting={
                    completeNextActionMutation.isPending &&
                    completeNextActionMutation.variables === item.id
                  }
                  onComplete={(applicationId) =>
                    completeNextActionMutation.mutate(applicationId)
                  }
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Link href="/dashboard/jobs">
          <Card className="hover:bg-muted/50 transition-colors">
            <CardContent className="flex items-center gap-3 px-5 py-4">
              <Briefcase className="h-5 w-5 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-2xl font-semibold leading-tight">
                  {jobsQuery.isLoading ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    totalJobs.toLocaleString()
                  )}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  Jobs in feed
                </p>
              </div>
            </CardContent>
          </Card>
        </Link>

        <Link href="/dashboard/matches">
          <Card className="hover:bg-muted/50 transition-colors">
            <CardContent className="flex items-center gap-3 px-5 py-4">
              <Sparkles className="h-5 w-5 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-2xl font-semibold leading-tight">
                  {matchesQuery.isLoading ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    totalMatches.toLocaleString()
                  )}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  Ranked matches
                </p>
              </div>
            </CardContent>
          </Card>
        </Link>

        <Link href="/dashboard/applications">
          <Card className="hover:bg-muted/50 transition-colors">
            <CardContent className="flex items-center gap-3 px-5 py-4">
              <ListChecks className="h-5 w-5 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-2xl font-semibold leading-tight">
                  {appsQuery.isLoading ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    activeApplications
                  )}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  Active applications
                </p>
              </div>
            </CardContent>
          </Card>
        </Link>

        <Card>
          <CardContent className="flex items-center gap-3 px-5 py-4">
            <TrendingUp className="h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <p className="text-2xl font-semibold leading-tight">
                {appsQuery.isLoading ? (
                  <Loader2 className="h-5 w-5 animate-spin" />
                ) : (
                  `${successRate}%`
                )}
              </p>
              <p className="truncate text-xs text-muted-foreground">Offer rate</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="px-5 py-4">
          <h3 className="mb-3 text-sm font-semibold">Application pipeline</h3>
          {appsQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading…
            </div>
          ) : apps.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No applications yet. Save jobs from the{" "}
              <Link
                href="/dashboard/matches"
                className="underline hover:text-foreground"
              >
                Matches
              </Link>{" "}
              page to start tracking.
            </p>
          ) : (
            <div className="flex flex-wrap gap-4">
              {[
                {
                  label: "Saved",
                  count: statusCounts.saved,
                  color: "bg-muted-foreground",
                },
                { label: "Applied", count: statusCounts.applied, color: "bg-blue-500" },
                {
                  label: "Interview",
                  count: statusCounts.interview,
                  color: "bg-amber-500",
                },
                { label: "Offer", count: statusCounts.offer, color: "bg-green-500" },
                { label: "Rejected", count: statusCounts.rejected, color: "bg-red-500" },
                { label: "Closed", count: statusCounts.closed, color: "bg-muted" },
              ].map((stage) => (
                <div key={stage.label} className="flex items-center gap-2">
                  <div className={`h-3 w-3 rounded-full ${stage.color}`} />
                  <span className="text-sm">
                    {stage.label}{" "}
                    <span className="text-muted-foreground">({stage.count})</span>
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function TodayNextActionRow({
  item,
  todayIso,
  isCompleting,
  onComplete,
}: {
  item: ApplicationListItem;
  todayIso: string;
  isCompleting: boolean;
  onComplete: (applicationId: number) => void;
}) {
  const dueDate = item.next_action_due_date;
  if (!dueDate) return null;

  const nextActionState = getNextActionState(
    item.next_action_type,
    item.next_action_due_date,
    item.next_action_completed_at,
    todayIso,
  );

  return (
    <div className="rounded-lg border px-3 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/jobs/${item.job_id}`}
              className="font-medium hover:underline"
            >
              {item.job_title}
            </Link>
            <span className="text-xs text-muted-foreground">
              {item.company ?? "—"}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Badge variant={nextActionState.isOverdue ? "destructive" : "outline"}>
              {nextActionState.isOverdue ? "Overdue" : "Due today"}
            </Badge>
            <Badge variant="secondary">{getNextActionLabel(item.next_action_type)}</Badge>
            <span className="text-xs text-muted-foreground">
              {formatNextActionDateLabel(dueDate)}
            </span>
          </div>
          {item.next_action_note && (
            <p className="mt-2 max-w-2xl line-clamp-2 text-xs text-muted-foreground">
              {item.next_action_note}
            </p>
          )}
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => onComplete(item.id)}
          disabled={isCompleting}
          className="gap-1.5"
        >
          {isCompleting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="h-3.5 w-3.5" />
          )}
          {isCompleting ? "Completing…" : "Complete"}
        </Button>
      </div>
    </div>
  );
}
