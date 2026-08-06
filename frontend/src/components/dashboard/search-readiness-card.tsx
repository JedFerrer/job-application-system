"use client";

import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { dashboardApi } from "@/lib/api";
import type { SearchReadinessReason, SearchReadinessResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const DESTINATIONS: Record<SearchReadinessReason, string> = {
  draft_review: "/dashboard/settings/profile",
  import_resume: "/dashboard/settings/profile",
  complete_profile: "/dashboard/settings/profile",
  set_job_preferences: "/dashboard/settings/preferences",
  view_matches: "/dashboard/matches",
};

const OVERALL_LABELS: Record<SearchReadinessResponse["overall_state"], string> = {
  needs_setup: "Needs setup",
  almost_ready: "Almost ready",
  ready: "Ready",
};

const CHECK_LABELS = {
  ready: "Ready",
  needs_import: "Needs resume",
  draft_review: "Draft awaiting review",
  needs_setup: "Needs setup",
} as const;

export function SearchReadinessCard() {
  const readinessQuery = useQuery({
    queryKey: ["dashboard-search-readiness"],
    queryFn: dashboardApi.getSearchReadiness,
  });

  if (readinessQuery.data?.overall_state === "ready") {
    return null;
  }

  return (
    <Card aria-labelledby="search-readiness-title">
      <CardContent className="px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 id="search-readiness-title" className="text-sm font-semibold">
              Search Readiness
            </h3>
            <p className="text-xs text-muted-foreground">
              Check your setup before reviewing new matches.
            </p>
          </div>
          {readinessQuery.data && (
            <Badge variant="secondary">
              {OVERALL_LABELS[readinessQuery.data.overall_state]}
            </Badge>
          )}
        </div>

        {readinessQuery.isLoading ? (
          <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading search readiness…
          </div>
        ) : readinessQuery.isError && !readinessQuery.data ? (
          <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            <span>Couldn’t load search readiness.</span>
            <Button variant="outline" size="sm" onClick={() => readinessQuery.refetch()}>
              Retry
            </Button>
          </div>
        ) : readinessQuery.data ? (
          <ReadinessContent data={readinessQuery.data} />
        ) : null}
      </CardContent>
    </Card>
  );
}

function ReadinessContent({ data }: { data: SearchReadinessResponse }) {
  const checks = [
    ["Resume", data.checks.resume],
    ["Profile", data.checks.profile],
    ["Job preferences", data.checks.preferences],
  ] as const;
  const nextAction = data.next_action;
  const destination = DESTINATIONS[nextAction.reason];
  const showDraftLink =
    data.checks.resume.state === "ready" && data.checks.resume.has_pending_draft;

  return (
    <div className="mt-4 flex flex-col gap-4">
      <div className="grid gap-2 sm:grid-cols-3" aria-label="Search readiness checks">
        {checks.map(([label, check]) => {
          const ready = check.state === "ready";
          return (
            <div key={label} className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
              {ready ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
              ) : (
                <AlertCircle className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
              )}
              <span className="min-w-0">
                <span className="block font-medium">{label}</span>
                <span className="block text-xs text-muted-foreground">
                  {CHECK_LABELS[check.state]}
                </span>
              </span>
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Link
          href={destination}
          className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {nextAction.label}
        </Link>
        {showDraftLink && (
          <Link
            href={DESTINATIONS.draft_review}
            className="text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Review draft
          </Link>
        )}
      </div>
    </div>
  );
}
