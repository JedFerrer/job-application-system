"use client";

import { useEffect, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { FilePenLine, FileText, Loader2, MessagesSquare, Plus, RefreshCw, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi, matchesApi } from "@/lib/api";
import type { MatchItem } from "@/lib/types";

const PAGE_SIZE = 20;

/** Guard against duplicate rows across pages (offset pagination can overlap). */
function dedupeByJobId(matches: MatchItem[]): MatchItem[] {
  const seen = new Set<number>();
  return matches.filter((m) => {
    if (seen.has(m.job_id)) return false;
    seen.add(m.job_id);
    return true;
  });
}
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
import { InterviewPrepDialog } from "@/components/interview/interview-prep-dialog";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { SkillsGapSummary } from "@/components/matches/skills-gap-summary";

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
  const [prepJob, setPrepJob] = useState<MatchItem | null>(null);
  const [tailorJob, setTailorJob] = useState<MatchItem | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Refs keep the latest query state readable inside the observer callback
  // without ever recreating the observer (which would cause it to re-fire
  // immediately on creation and cascade-fetch every remaining page).
  const hasNextPageRef = useRef(false);
  const isFetchingNextPageRef = useRef(false);
  const fetchNextPageRef = useRef<() => void>(() => {});

  const matchesQuery = useInfiniteQuery({
    queryKey: ["matches"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      matchesApi.list({ limit: PAGE_SIZE, offset: (pageParam as number) * PAGE_SIZE }),
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
      return loaded < lastPage.total ? allPages.length : undefined;
    },
  });

  const allMatches = dedupeByJobId(
    matchesQuery.data?.pages.flatMap((p) => p.items) ?? [],
  );
  const total = matchesQuery.data?.pages[0]?.total ?? 0;

  // Keep refs in sync with the latest query state on every render.
  hasNextPageRef.current = matchesQuery.hasNextPage;
  isFetchingNextPageRef.current = matchesQuery.isFetchingNextPage;
  fetchNextPageRef.current = matchesQuery.fetchNextPage;

  // Infinite scroll sentinel — created ONCE (empty dep array).
  // The observer reads hasNextPage / isFetchingNextPage / fetchNextPage
  // through refs so it never needs to be recreated when those values change.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    // rootMargin "0px 0px 300px 0px" fires the callback when the sentinel is
    // 300 px below the visible viewport — i.e. before the user reaches it.
    // New items are therefore inserted while the sentinel is still offscreen,
    // so the viewport never needs to shift and the scroll position stays put.
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (
          entry.isIntersecting &&
          hasNextPageRef.current &&
          !isFetchingNextPageRef.current
        ) {
          fetchNextPageRef.current();
        }
      },
      { rootMargin: "0px 0px 300px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
      <SkillsGapSummary />
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {matchesQuery.data
            ? `${total} ranked jobs`
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

      {matchesQuery.data && allMatches.length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center text-sm text-muted-foreground">
            <Sparkles className="h-10 w-10 text-amber-400" />
            <p>
              No matches yet. Make sure you have an active profile and ingested
              jobs, then click "Rank matches".
            </p>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-3">
        {allMatches.map((match) => (
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
                variant="outline"
                onClick={() => setPrepJob(match)}
              >
                <MessagesSquare className="h-4 w-4" />
                Interview prep
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setTailorJob(match)}
              >
                <FilePenLine className="h-4 w-4" />
                Tailor resume
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

      {/* Sentinel — IntersectionObserver watches this to trigger next page.
          overflow-anchor:none prevents the browser from scrolling the viewport
          down to keep this element in view when new items are inserted above it. */}
      <div ref={sentinelRef} className="h-4" style={{ overflowAnchor: "none" }} />

      {matchesQuery.isFetchingNextPage && (
        <div className="flex justify-center py-4">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {!matchesQuery.hasNextPage && allMatches.length > 0 && (
        <p className="py-2 text-center text-xs text-muted-foreground">
          All {total} matches loaded
        </p>
      )}

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
      <InterviewPrepDialog
        jobId={prepJob?.job_id ?? null}
        jobTitle={prepJob?.title}
        open={prepJob !== null}
        onOpenChange={(open) => !open && setPrepJob(null)}
      />
      <TailorResumeDialog
        jobId={tailorJob?.job_id ?? null}
        jobTitle={tailorJob?.title}
        open={tailorJob !== null}
        onOpenChange={(open) => !open && setTailorJob(null)}
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
