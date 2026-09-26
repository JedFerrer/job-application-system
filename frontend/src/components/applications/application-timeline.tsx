"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ApiError, applicationsApi } from "@/lib/api";
import type { ApplicationTimelineEvent } from "@/lib/types";
import { submissionDetailKey } from "./submission-history";

function TimelineRow({ applicationId, item }: { applicationId: number; item: ApplicationTimelineEvent }) {
  const submissionId = item.type === "submission_confirmed" ? item.submission_id : null;
  const detail = useQuery({
    queryKey: submissionDetailKey(applicationId, submissionId ?? 0),
    queryFn: () => applicationsApi.getSubmission(applicationId, submissionId!),
    enabled: submissionId !== null,
    retry: false,
  });
  return <li className="min-w-0 [overflow-wrap:anywhere]">
    <span>{item.type === "submission_confirmed" ? "Submitted material explicitly confirmed" : item.type === "confirmation_detected" ? "Application page confirmation detected" : item.summary || "Application activity recorded"}</span>
    {submissionId !== null ? <span className="text-muted-foreground"> · Submission #{submissionId} · {detail.isPending ? "Checking submission details" : detail.isError && detail.error instanceof ApiError && detail.error.status === 404 ? "Submission details no longer available" : detail.isError ? "Submission details could not be checked" : "Details are available in submission history while retained"}</span> : null}
    {item.counts ? <span className="text-muted-foreground"> · {Object.entries(item.counts).map(([key, value]) => `${key}: ${value}`).join(", ")}</span> : null}
    <span className="text-muted-foreground"> · {new Date(item.occurred_at).toLocaleString()}</span>
  </li>;
}

export function ApplicationTimeline({ applicationId }: { applicationId: number }) {
  const timeline = useInfiniteQuery({
    queryKey: ["application-timeline", applicationId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => applicationsApi.getTimeline(applicationId, pageParam ?? undefined),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
  if (timeline.isLoading) return <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Timeline</h2><p className="mt-2 text-sm text-muted-foreground">Loading timeline…</p></section>;
  if (timeline.isError) return <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Timeline</h2><p className="mt-2 text-sm text-muted-foreground">Could not load timeline.</p></section>;
  const items = timeline.data?.pages.flatMap((page) => page.items) ?? [];
  return <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Timeline</h2>{items.length ? <ul className="mt-2 space-y-2 text-sm">{items.map((item) => <TimelineRow key={item.id} applicationId={applicationId} item={item} />)}</ul> : <p className="mt-2 text-sm text-muted-foreground">No timeline activity yet.</p>}{timeline.hasNextPage ? <button className="mt-3 text-sm underline" disabled={timeline.isFetchingNextPage} onClick={() => void timeline.fetchNextPage()}>{timeline.isFetchingNextPage ? "Loading…" : "Load more"}</button> : null}</section>;
}
