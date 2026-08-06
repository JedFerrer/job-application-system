"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, Loader2 } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { ApiError, applicationsApi, matchesApi } from "@/lib/api";
import type { MatchRecommendationItem } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const readinessClass: Record<MatchRecommendationItem["readiness"], string> = {
  apply: "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-400/35 dark:bg-emerald-500/10 dark:text-emerald-200",
  save: "border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-400/35 dark:bg-blue-500/10 dark:text-blue-200",
  stretch: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-400/35 dark:bg-amber-500/10 dark:text-amber-200",
};

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function TopRecommendedMatches() {
  const queryClient = useQueryClient();
  const recommendationsQuery = useQuery({
    queryKey: ["dashboard-recommendations", 4],
    queryFn: () => matchesApi.recommendations(4),
  });

  const saveMutation = useMutation({
    mutationFn: (jobId: number) => applicationsApi.create(jobId),
    onSuccess: (application) => {
      if (application.status === "saved") toast.success("Saved to applications");
      else toast.error("Job is already tracked as " + application.status);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-recommendations"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Save failed"),
  });

  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold">Top Recommended Matches</h3>
            <p className="text-xs text-muted-foreground">
              Strong current matches to review or save.
            </p>
          </div>
          <Link href="/dashboard/matches" className="text-xs underline hover:text-foreground">
            View all matches
          </Link>
        </div>

        {recommendationsQuery.isLoading ? (
          <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading recommendations…
          </div>
        ) : recommendationsQuery.isError ? (
          <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>Couldn’t load recommendations.</span>
            <Button variant="outline" size="sm" onClick={() => recommendationsQuery.refetch()}>
              Retry
            </Button>
          </div>
        ) : recommendationsQuery.data?.items.length ? (
          <div className="mt-4 space-y-2">
            {recommendationsQuery.data.items.map((item) => (
              <RecommendationRow
                key={item.job_id}
                item={item}
                isSaving={saveMutation.isPending && saveMutation.variables === item.job_id}
                onSave={() => saveMutation.mutate(item.job_id)}
              />
            ))}
          </div>
        ) : (
          <p className="mt-4 text-sm text-muted-foreground">
            No recommendations yet.{" "}
            <Link href="/dashboard/matches" className="underline hover:text-foreground">
              View all matches
            </Link>
            .
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function RecommendationRow({
  item,
  isSaving,
  onSave,
}: {
  item: MatchRecommendationItem;
  isSaving: boolean;
  onSave: () => void;
}) {
  return (
    <div className="rounded-lg border px-3 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <Link href={"/jobs/" + item.job_id} className="font-medium hover:underline">
            {item.title}
          </Link>
          <p className="text-xs text-muted-foreground">
            {item.company ?? "Company not listed"}
            {item.location ? " · " + item.location : item.remote ? " · Remote" : ""}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <Badge variant="outline" className={"rounded-full text-[11px] " + readinessClass[item.readiness]}>
              {titleCase(item.readiness)}
            </Badge>
            <Badge variant="secondary" className="text-[11px]">{Math.round(item.score)}% fit</Badge>
            {item.matched_skills.slice(0, 3).map((skill) => (
              <Badge key={skill} variant="outline" className="text-[11px] font-normal">
                {skill}
              </Badge>
            ))}
            {item.missing_skill && (
              <span className="text-[11px] text-muted-foreground">Missing: {item.missing_skill}</span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {item.application_status ? (
            <Badge variant="secondary" className="text-[11px]">{titleCase(item.application_status)}</Badge>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              onClick={onSave}
              disabled={isSaving}
              aria-label={"Save " + item.title}
              className="gap-1.5"
            >
              {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bookmark className="h-3.5 w-3.5" />}
              {isSaving ? "Saving…" : "Save"}
            </Button>
          )}
          <Button asChild size="sm" variant="outline">
            <Link href={"/jobs/" + item.job_id}>View</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
