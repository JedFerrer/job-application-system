"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { applicationsApi } from "@/lib/api";

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
  return <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Timeline</h2>{items.length ? <ul className="mt-2 space-y-2 text-sm">{items.map((item) => <li key={item.id}><span>{item.summary || "Application activity recorded"}</span>{item.counts ? <span className="text-muted-foreground"> · {Object.entries(item.counts).map(([key, value]) => `${key}: ${value}`).join(", ")}</span> : null}<span className="text-muted-foreground"> · {new Date(item.occurred_at).toLocaleString()}</span></li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">No timeline activity yet.</p>}{timeline.hasNextPage ? <button className="mt-3 text-sm underline" disabled={timeline.isFetchingNextPage} onClick={() => void timeline.fetchNextPage()}>{timeline.isFetchingNextPage ? "Loading…" : "Load more"}</button> : null}</section>;
}
