"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Briefcase,
  ListChecks,
  Loader2,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { applicationsApi, jobsApi, matchesApi } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";

export default function DashboardHome() {
  const appsQuery = useQuery({
    queryKey: ["home-applications"],
    queryFn: applicationsApi.list,
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

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h2 className="text-lg font-semibold">Dashboard</h2>
        <p className="text-sm text-muted-foreground">
          Overview of your job search pipeline.
        </p>
      </div>

      {/* Summary stats */}
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

      {/* Pipeline breakdown */}
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
                { label: "Saved", count: statusCounts.saved, color: "bg-muted-foreground" },
                { label: "Applied", count: statusCounts.applied, color: "bg-blue-500" },
                { label: "Interview", count: statusCounts.interview, color: "bg-amber-500" },
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
