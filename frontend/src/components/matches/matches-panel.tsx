"use client";

import { useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { FileText, Loader2, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi, matchesApi } from "@/lib/api";
import type { MatchItem } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";

const CONFIDENCE_VARIANT: Record<
  string,
  "default" | "secondary" | "outline" | "destructive"
> = {
  strong: "default",
  moderate: "secondary",
  stretch: "outline",
  weak: "destructive",
};

export function MatchesPanel() {
  const queryClient = useQueryClient();
  const [explainJob, setExplainJob] = useState<MatchItem | null>(null);
  const [coverJob, setCoverJob] = useState<MatchItem | null>(null);

  const matchesQuery = useQuery({
    queryKey: ["matches"],
    queryFn: () => matchesApi.list({ limit: 100 }),
  });

  const rankMutation = useMutation({
    mutationFn: () => matchesApi.rank(100),
    onSuccess: (res) => {
      toast.success(`Ranked ${res.ranked} jobs`);
      queryClient.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Ranking failed"),
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
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {matchesQuery.data
            ? `${matchesQuery.data.total} ranked jobs`
            : "Rank your active profile against the job feed."}
        </p>
        <Button
          onClick={() => rankMutation.mutate()}
          disabled={rankMutation.isPending}
        >
          {rankMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          {rankMutation.isPending ? "Ranking…" : "Rank matches"}
        </Button>
      </div>

      {matchesQuery.isLoading && (
        <p className="text-sm text-muted-foreground">Loading matches…</p>
      )}

      {matchesQuery.data && matchesQuery.data.items.length === 0 && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No matches yet. Make sure you have an active profile and ingested
            jobs, then click “Rank matches”.
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-3">
        {matchesQuery.data?.items.map((match) => (
          <Card key={match.job_id}>
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-base">
                    <a
                      href={match.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:underline"
                    >
                      {match.title}
                    </a>
                  </CardTitle>
                  <CardDescription>
                    {match.company ?? "Unknown company"}
                    {match.location ? ` · ${match.location}` : ""}
                  </CardDescription>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className="text-lg font-semibold">
                    {Math.round(match.vector_score)}
                  </span>
                  {match.confidence && (
                    <Badge
                      variant={
                        CONFIDENCE_VARIANT[match.confidence] ?? "outline"
                      }
                    >
                      {match.confidence}
                    </Badge>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2 pt-0">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setExplainJob(match)}
              >
                Explain
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setCoverJob(match)}
              >
                <FileText className="h-4 w-4" />
                Cover letter
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => saveAppMutation.mutate(match.job_id)}
                disabled={saveAppMutation.isPending}
              >
                <Plus className="h-4 w-4" />
                Save to applications
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>

      <ExplainDialog
        job={explainJob}
        onOpenChange={(open) => !open && setExplainJob(null)}
      />
      <CoverLetterDialog
        jobId={coverJob?.job_id ?? null}
        jobTitle={coverJob?.title}
        open={coverJob !== null}
        onOpenChange={(open) => !open && setCoverJob(null)}
      />
    </div>
  );
}

function ExplainDialog({
  job,
  onOpenChange,
}: {
  job: MatchItem | null;
  onOpenChange: (open: boolean) => void;
}) {
  const explainQuery = useQuery({
    queryKey: ["match-explain", job?.job_id],
    queryFn: () => matchesApi.explain(job!.job_id),
    enabled: job !== null,
  });

  return (
    <Dialog open={job !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{job?.title}</DialogTitle>
          <DialogDescription>
            {job?.company ?? "Unknown company"} · match analysis
          </DialogDescription>
        </DialogHeader>

        {explainQuery.isLoading && (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Generating explanation…
          </div>
        )}

        {explainQuery.isError && (
          <p className="py-4 text-sm text-destructive">
            {explainQuery.error instanceof ApiError
              ? explainQuery.error.message
              : "Failed to load explanation"}
          </p>
        )}

        {explainQuery.data && (
          <div className="flex flex-col gap-4">
            <div className="flex gap-4 text-sm">
              <span>
                Vector score:{" "}
                <strong>{Math.round(explainQuery.data.vector_score)}</strong>
              </span>
              {explainQuery.data.llm_score != null && (
                <span>
                  LLM score: <strong>{explainQuery.data.llm_score}</strong>
                </span>
              )}
              {explainQuery.data.confidence && (
                <span>Confidence: {explainQuery.data.confidence}</span>
              )}
            </div>

            {explainQuery.data.explanation && (
              <p className="text-sm">{explainQuery.data.explanation}</p>
            )}

            {explainQuery.data.matched_skills.length > 0 && (
              <div className="flex flex-col gap-2">
                <h4 className="text-sm font-semibold">Matched skills</h4>
                <div className="flex flex-wrap gap-2">
                  {explainQuery.data.matched_skills.map((s) => (
                    <Badge key={s} variant="secondary">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {explainQuery.data.missing_keywords.length > 0 && (
              <div className="flex flex-col gap-2">
                <h4 className="text-sm font-semibold">Missing keywords</h4>
                <div className="flex flex-wrap gap-2">
                  {explainQuery.data.missing_keywords.map((s) => (
                    <Badge key={s} variant="outline">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
