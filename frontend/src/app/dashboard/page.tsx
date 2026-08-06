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
import {
  type ApplicationActionCenterDatedItem,
  type ApplicationActionCenterMissingItem,
} from "@/lib/types";
import {
  formatNextActionDateLabel,
  getNextActionLabel,
  getNextActionState,
  getTodayIsoDate,
} from "@/lib/next-actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { TopRecommendedMatches } from "@/components/dashboard/top-recommended-matches";
import { SearchReadinessCard } from "@/components/dashboard/search-readiness-card";

export default function DashboardHome() {
  const queryClient = useQueryClient();
  const todayIso = getTodayIsoDate();

  const summaryQuery = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: applicationsApi.getDashboardSummary,
  });

  const actionCenterQuery = useQuery({
    queryKey: ["dashboard-action-center", todayIso],
    queryFn: () => applicationsApi.getActionCenter(todayIso),
  });

  const matchesQuery = useQuery({
    queryKey: ["home-matches"],
    queryFn: () => matchesApi.list({ limit: 1 }),
  });

  const jobsQuery = useQuery({
    queryKey: ["home-jobs"],
    queryFn: () => jobsApi.list({ limit: 1 }),
  });

  const summary = summaryQuery.data;
  const statusCounts = summary?.counts_by_status;
  const hasSummary = summary != null;
  const summaryLoading =
    !hasSummary &&
    (summaryQuery.isLoading || summaryQuery.isFetching || summaryQuery.isPending);
  const totalMatches = matchesQuery.data?.total ?? 0;
  const totalJobs = jobsQuery.data?.total ?? 0;
  const pipelineStages = [
    { label: "Saved", count: statusCounts?.saved ?? 0, color: "bg-muted-foreground" },
    { label: "Applied", count: statusCounts?.applied ?? 0, color: "bg-blue-500" },
    { label: "Interview", count: statusCounts?.interview ?? 0, color: "bg-amber-500" },
    { label: "Offer", count: statusCounts?.offer ?? 0, color: "bg-green-500" },
    { label: "Rejected", count: statusCounts?.rejected ?? 0, color: "bg-red-500" },
    { label: "Closed", count: statusCounts?.closed ?? 0, color: "bg-muted" },
  ];
  const pipelineTotal = summary?.total_applications ?? 1;

  const completeNextActionMutation = useMutation({
    mutationFn: (applicationId: number) =>
      applicationsApi.update(applicationId, {
        next_action_completed_at: new Date().toISOString(),
      }),
    onSuccess: () => {
      toast.success("Marked next action complete");
      queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
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

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Link href="/dashboard/jobs">
          <Card className="transition-colors hover:bg-muted/50">
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
          <Card className="transition-colors hover:bg-muted/50">
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
          <Card className="transition-colors hover:bg-muted/50">
            <CardContent className="flex items-center gap-3 px-5 py-4">
              <ListChecks className="h-5 w-5 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-2xl font-semibold leading-tight">
                  {summaryLoading ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    summary ? summary.active_pipeline_count : "—"
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
                {summaryLoading ? (
                  <Loader2 className="h-5 w-5 animate-spin" />
                ) : (
                  summary ? `${summary.offer_rate_percent}%` : "—"
                )}
              </p>
              <p className="truncate text-xs text-muted-foreground">Offer rate</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="order-3 empty:hidden">
        <SearchReadinessCard />
      </div>

      <div className="order-4">
        <Card>
          <CardContent className="px-5 py-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-sm font-semibold">Action Center</h3>
                <p className="text-xs text-muted-foreground">
                  User-managed actions that need attention today and this week.
                </p>
              </div>
              <Badge variant="secondary">
                {actionCenterQuery.data
                  ? actionCenterQuery.data.overdue.count +
                    actionCenterQuery.data.due_today.count +
                    actionCenterQuery.data.upcoming.count +
                    actionCenterQuery.data.needs_next_action.count
                  : 0}
              </Badge>
            </div>

          {actionCenterQuery.isLoading ? (
            <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading action center…
            </div>
          ) : actionCenterQuery.isError ? (
            <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>Couldn’t load action center.</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => actionCenterQuery.refetch()}
              >
                Retry
              </Button>
            </div>
          ) : (
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <ActionCenterGroup
                title="Overdue"
                count={actionCenterQuery.data?.overdue.count ?? 0}
                empty="No overdue actions."
                items={actionCenterQuery.data?.overdue.items ?? []}
                todayIso={todayIso}
                isCompleting={completeNextActionMutation.isPending}
                completingId={completeNextActionMutation.variables}
                onComplete={(id) => completeNextActionMutation.mutate(id)}
                variant="overdue"
              />
              <ActionCenterGroup
                title="Due today"
                count={actionCenterQuery.data?.due_today.count ?? 0}
                empty="No actions due today."
                items={actionCenterQuery.data?.due_today.items ?? []}
                todayIso={todayIso}
                isCompleting={completeNextActionMutation.isPending}
                completingId={completeNextActionMutation.variables}
                onComplete={(id) => completeNextActionMutation.mutate(id)}
                variant="today"
              />
              <ActionCenterGroup
                title="Upcoming"
                count={actionCenterQuery.data?.upcoming.count ?? 0}
                empty="No actions due in the next seven days."
                items={actionCenterQuery.data?.upcoming.items ?? []}
                todayIso={todayIso}
                isCompleting={completeNextActionMutation.isPending}
                completingId={completeNextActionMutation.variables}
                onComplete={(id) => completeNextActionMutation.mutate(id)}
                variant="upcoming"
              />
              <NeedsNextActionGroup
                count={actionCenterQuery.data?.needs_next_action.count ?? 0}
                items={actionCenterQuery.data?.needs_next_action.items ?? []}
              />
            </div>
          )}
        </CardContent>
        </Card>
      </div>

      <div className="order-5">
        <TopRecommendedMatches />
      </div>

      {!hasSummary && summaryQuery.isError && !summaryQuery.isFetching && (
        <div className="order-1">
          <Card>
          <CardContent className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 text-sm text-muted-foreground">
            <span>Couldn’t load application summary.</span>
            <Button variant="outline" size="sm" onClick={() => summaryQuery.refetch()}>
              Retry
            </Button>
          </CardContent>
          </Card>
        </div>
      )}

      <div className="order-2">
        <Card>
        <CardContent className="px-5 py-4">
          <h3 className="mb-3 text-sm font-semibold">Application pipeline</h3>
          {summaryLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading…
            </div>
          ) : !hasSummary ? (
            <p className="text-sm text-muted-foreground">
              Application metrics are unavailable. Use Retry above to try again.
            </p>
          ) : (summary?.total_applications ?? 0) === 0 ? (
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
            <div>
              <div
                className="flex h-3 overflow-hidden rounded-full bg-muted"
                role="img"
                aria-label="Application pipeline by status"
              >
                {pipelineStages.map((stage) => (
                  <div
                    key={stage.label}
                    className={`h-full ${stage.color}`}
                    style={{ width: `${(stage.count / pipelineTotal) * 100}%` }}
                    title={`${stage.label}: ${stage.count}`}
                  />
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">
                {pipelineStages.map((stage) => (
                  <div key={stage.label} className="flex items-center gap-2">
                    <div className={`h-3 w-3 rounded-full ${stage.color}`} />
                    <span className="text-sm">
                      {stage.label}{" "}
                      <span className="text-muted-foreground">({stage.count})</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
        </Card>
      </div>
    </div>
  );
}

function ActionCenterGroup({
  title,
  count,
  empty,
  items,
  todayIso,
  isCompleting,
  completingId,
  onComplete,
  variant,
}: {
  title: string;
  count: number;
  empty: string;
  items: ApplicationActionCenterDatedItem[];
  todayIso: string;
  isCompleting: boolean;
  completingId: number | undefined;
  onComplete: (applicationId: number) => void;
  variant: "overdue" | "today" | "upcoming";
}) {
  return (
    <section className="rounded-lg border p-3" aria-labelledby={`action-center-${variant}`}>
      <div className="flex items-center justify-between gap-2">
        <h4 id={`action-center-${variant}`} className="text-sm font-semibold">{title}</h4>
        <Badge variant="secondary">{count}</Badge>
      </div>
      {items.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">{empty}</p>
      ) : (
        <div className="mt-3 space-y-2">
          {items.map((item) => (
            <ActionCenterDatedRow
              key={item.application_id}
              item={item}
              todayIso={todayIso}
              isCompleting={isCompleting && completingId === item.application_id}
              onComplete={onComplete}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ActionCenterDatedRow({
  item,
  todayIso,
  isCompleting,
  onComplete,
}: {
  item: ApplicationActionCenterDatedItem;
  todayIso: string;
  isCompleting: boolean;
  onComplete: (applicationId: number) => void;
}) {
  const dueDate = item.next_action_due_date;
  const nextActionState = getNextActionState(
    item.next_action_type,
    item.next_action_due_date,
    null,
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
              {nextActionState.isOverdue
                ? "Overdue"
                : nextActionState.isDueToday
                  ? "Due today"
                  : "Upcoming"}
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
          onClick={() => onComplete(item.application_id)}
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

function NeedsNextActionGroup({
  count,
  items,
}: {
  count: number;
  items: ApplicationActionCenterMissingItem[];
}) {
  return (
    <section className="rounded-lg border p-3" aria-labelledby="action-center-needs">
      <div className="flex items-center justify-between gap-2">
        <h4 id="action-center-needs" className="text-sm font-semibold">Needs a next action</h4>
        <Badge variant="secondary">{count}</Badge>
      </div>
      {items.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">All active applications have a current action.</p>
      ) : (
        <div className="mt-3 space-y-2">
          {items.map((item) => (
            <div key={item.application_id} className="rounded-md bg-muted/30 px-3 py-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <Link href={`/jobs/${item.job_id}`} className="font-medium hover:underline">
                    {item.job_title}
                  </Link>
                  <p className="text-xs text-muted-foreground">
                    {item.company ?? "Company not listed"} · {item.application_status}
                  </p>
                </div>
                <Link
                  href={`/dashboard/applications?next_action_application_id=${item.application_id}`}
                  className="rounded-md border px-2.5 py-1.5 text-xs font-medium hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Set action
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
